"""
Multi-pass PDF-extraktion för svenska kvartalsrapporter.

Pipeline:
  Pass 1 (Haiku): Identifiera struktur (tabeller, sektioner, grafer)
  Pass 2 (Sonnet): Extrahera tabeller och grafer med precision
  Pass 3 (Haiku): Extrahera narrativ text

Pass 2 och 3 körs parallellt efter Pass 1.
"""

import asyncio
import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, TypedDict

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

# Ladda .env-fil
load_dotenv()

from prompts import PASS_1_STRUCTURE_PROMPT, PASS_2_TABLES_PROMPT, PASS_3_TEXT_PROMPT
from supabase_client import (
    get_or_create_company,
    save_period,
    get_pdf_hash,
    period_exists,
    load_period,
)
from validation import (
    validate_tables,
    validate_sections,
    format_validation_report,
    ValidationResult,
)

# Modell-IDs
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-5-20250929"

# Konfiguration
MAX_CONCURRENT = 10
MAX_RETRIES = 3
API_TIMEOUT = 300  # 5 minuter timeout per API-anrop

# Priser (USD per 1M tokens)
HAIKU_INPUT_PRICE = 0.80
HAIKU_OUTPUT_PRICE = 4.00
SONNET_INPUT_PRICE = 3.00
SONNET_OUTPUT_PRICE = 15.00
USD_TO_SEK = 10.50


class PassResult(TypedDict):
    pass_number: int
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    data: dict


class RetryStats(TypedDict):
    """Statistik for validation retries."""
    retry_count: int
    tables_retried: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    cost_sek: float


class PipelineResult(TypedDict):
    metadata: dict
    tables: list[dict]
    sections: list[dict]
    charts: list[dict]
    pass_info: list[PassResult]
    retry_stats: RetryStats
    total_cost_sek: float


def parse_json_response(text: str) -> dict:
    """Extrahera JSON från Claude-svar med robust felhantering."""
    text = text.strip()

    # Ta bort markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])

    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        json_str = json_match.group()

        # Försök parsa direkt
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            # Försök fixa vanliga JSON-fel
            fixed = json_str

            # 1. Ta bort trailing commas före } eller ]
            fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)

            # 2. Fixa problem med avslutande komma i arrays
            fixed = re.sub(r',\s*\]', ']', fixed)

            # 3. Hantera oavslutade strängar (t.ex. om output trunkeras)
            # Om JSON slutar mitt i en sträng, försök stänga den
            open_quotes = fixed.count('"') % 2
            if open_quotes == 1:
                # Hitta sista öppna citattecknet
                last_quote_pos = fixed.rfind('"')
                if last_quote_pos > 0:
                    # Ta bort allt efter sista hela objektet
                    # Leta efter sista } eller ] före den trasiga strängen
                    truncate_pos = max(
                        fixed.rfind('}', 0, last_quote_pos),
                        fixed.rfind(']', 0, last_quote_pos)
                    )
                    if truncate_pos > 0:
                        fixed = fixed[:truncate_pos + 1]
                        # Stäng eventuella öppna strukturer
                        open_braces = fixed.count('{') - fixed.count('}')
                        open_brackets = fixed.count('[') - fixed.count(']')
                        fixed += ']' * open_brackets + '}' * open_braces

            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                # Sista försök: trunkera vid sista kompletta objekt
                # Hitta balanserad JSON genom att räkna klamrar
                depth = 0
                last_valid_pos = 0
                in_string = False
                escape_next = False

                for i, char in enumerate(json_str):
                    if escape_next:
                        escape_next = False
                        continue
                    if char == '\\':
                        escape_next = True
                        continue
                    if char == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue

                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            last_valid_pos = i + 1

                if last_valid_pos > 0:
                    try:
                        return json.loads(json_str[:last_valid_pos])
                    except json.JSONDecodeError:
                        pass

                # Ge upp - skriv ut för debugging
                print(f"\n[VARNING] JSON-parsningsfel: {e}")
                print(f"   Första 500 tecken: {json_str[:500]}...")
                raise ValueError(f"Ogiltig JSON: {e}")

    raise ValueError("Ingen JSON hittad i svaret")


def calculate_pass_cost(pass_result: PassResult) -> float:
    """Beräkna kostnad för ett pass i SEK."""
    if pass_result["model"] == "haiku":
        cost_usd = (
            pass_result["input_tokens"] * HAIKU_INPUT_PRICE +
            pass_result["output_tokens"] * HAIKU_OUTPUT_PRICE
        ) / 1_000_000
    else:
        cost_usd = (
            pass_result["input_tokens"] * SONNET_INPUT_PRICE +
            pass_result["output_tokens"] * SONNET_OUTPUT_PRICE
        ) / 1_000_000
    return cost_usd * USD_TO_SEK


async def run_pass_1(
    pdf_base64: str,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> PassResult:
    """
    Pass 1: Strukturidentifiering med Haiku.

    Returnerar en "karta" över dokumentet med alla tabeller,
    sektioner och grafer identifierade.
    """
    start_time = time.perf_counter()
    async with semaphore:
        # Använd streaming för att undvika timeout
        full_response_text = ""
        input_tokens = 0
        output_tokens = 0

        async with client.messages.stream(
            model=HAIKU_MODEL,
            max_tokens=16000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": PASS_1_STRUCTURE_PROMPT
                    }
                ]
            }]
        ) as stream:
            async for text in stream.text_stream:
                full_response_text += text
            final_message = await stream.get_final_message()
            input_tokens = final_message.usage.input_tokens
            output_tokens = final_message.usage.output_tokens

        result = parse_json_response(full_response_text)
        elapsed = time.perf_counter() - start_time

        return PassResult(
            pass_number=1,
            model="haiku",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
            data=result
        )


async def run_pass_2(
    pdf_base64: str,
    structure_map: dict,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> PassResult:
    """
    Pass 2: Tabellextraktion med Sonnet.

    Extraherar alla tabeller och grafer med hög precision.
    """
    start_time = time.perf_counter()

    # Samla element-IDs för tabeller och grafer
    tables = structure_map.get("structure_map", {}).get("tables", [])
    charts = structure_map.get("structure_map", {}).get("charts", [])
    table_ids = [t["id"] for t in tables]
    chart_ids = [c["id"] for c in charts]
    element_ids = table_ids + chart_ids

    if not element_ids:
        # Inga tabeller/grafer att extrahera
        return PassResult(
            pass_number=2,
            model="sonnet",
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=0.0,
            data={"tables": [], "charts": []}
        )

    # Hämta språk och nummerformat från Pass 1 metadata
    metadata = structure_map.get("metadata", {})
    language = metadata.get("sprak", "sv")
    number_format = metadata.get("number_format", "swedish")

    # Bygg prompt med strukturkarta och dokumentinfo
    prompt = PASS_2_TABLES_PROMPT.format(
        structure_map_json=json.dumps(structure_map, ensure_ascii=False, indent=2),
        element_ids=", ".join(element_ids),
        language=language,
        number_format=number_format
    )

    async with semaphore:
        # Använd streaming för att undvika timeout
        full_response_text = ""
        input_tokens = 0
        output_tokens = 0

        async with client.messages.stream(
            model=SONNET_MODEL,
            max_tokens=60000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        ) as stream:
            async for text in stream.text_stream:
                full_response_text += text
            final_message = await stream.get_final_message()
            input_tokens = final_message.usage.input_tokens
            output_tokens = final_message.usage.output_tokens

        result = parse_json_response(full_response_text)
        elapsed = time.perf_counter() - start_time

        return PassResult(
            pass_number=2,
            model="sonnet",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
            data=result
        )


async def validate_and_retry_with_haiku(
    pdf_base64: str,
    tables: list[dict],
    structure_map: dict,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> tuple[list[dict], ValidationResult, RetryStats]:
    """
    Validera tabeller och kör ETT retry med Haiku om något saknas eller har fel.

    Flöde:
    1. Validera extraherade tabeller (lokal, ingen API)
    2. Hitta saknade tabeller (finns i Pass 1 men inte Pass 2)
    3. Om något saknas ELLER har fel → ETT Haiku-anrop för att fixa allt

    Args:
        pdf_base64: Base64-kodad PDF
        tables: Lista med extraherade tabeller från Pass 2
        structure_map: Strukturkarta från Pass 1
        client: Anthropic async-klient
        semaphore: För rate-limiting

    Returns:
        Tuple av (slutgiltiga tabeller, ValidationResult, RetryStats)
    """
    current_tables = tables.copy()

    # Steg 1: Validera extraherade tabeller
    validation_result = validate_tables(current_tables)
    tables_with_errors = validation_result.tables_with_errors

    # Steg 2: Hitta saknade tabeller
    expected_table_ids = {t["id"] for t in structure_map.get("structure_map", {}).get("tables", [])}
    extracted_table_ids = {t.get("id") for t in current_tables}
    missing_table_ids = expected_table_ids - extracted_table_ids

    # Steg 3: Bestäm om retry behövs
    needs_retry = len(missing_table_ids) > 0 or len(tables_with_errors) > 0

    # Tom retry stats som default
    retry_stats: RetryStats = {
        "retry_count": 0,
        "tables_retried": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "elapsed_seconds": 0.0,
        "cost_sek": 0.0,
    }

    if not needs_retry:
        return current_tables, validation_result, retry_stats

    # Logga vad som behöver fixas
    if missing_table_ids:
        print(f"\n   [VALIDERING] {len(missing_table_ids)} tabeller saknas", flush=True)
        for tid in sorted(missing_table_ids):
            for t in structure_map.get("structure_map", {}).get("tables", []):
                if t["id"] == tid:
                    print(f"      - {tid}: {t.get('title', 'Okänd')} (sida {t.get('page', '?')})", flush=True)
                    break

    if tables_with_errors:
        print(f"   [VALIDERING] {len(tables_with_errors)} tabeller har fel", flush=True)
        for tid in sorted(tables_with_errors):
            for t in current_tables:
                if t.get("id") == tid:
                    print(f"      - {tid}: {t.get('title', 'Okänd')}", flush=True)
                    break

    # Steg 4: Bygg prompt för Haiku retry
    start_time = time.perf_counter()

    # Hämta metadata
    metadata = structure_map.get("metadata", {})
    language = metadata.get("sprak", "sv")
    number_format = metadata.get("number_format", "swedish")

    # Bygg lista över tabeller att extrahera/korrigera
    tables_to_fix = []

    # Saknade tabeller - hämta info från Pass 1
    for tid in missing_table_ids:
        for t in structure_map.get("structure_map", {}).get("tables", []):
            if t["id"] == tid:
                tables_to_fix.append({
                    "id": tid,
                    "title": t.get("title", "Okänd"),
                    "type": t.get("type", "other"),
                    "page": t.get("page", "?"),
                    "issue": "SAKNAS - extrahera från PDF",
                    "columns": t.get("column_headers", [])
                })
                break

    # Tabeller med fel - inkludera nuvarande data + felbeskrivning
    for tid in tables_with_errors:
        for t in current_tables:
            if t.get("id") == tid:
                # Hitta felen för denna tabell
                errors = [e for e in validation_result.errors if e.table_id == tid]
                error_msgs = [e.message for e in errors]
                tables_to_fix.append({
                    "id": tid,
                    "title": t.get("title", "Okänd"),
                    "type": t.get("type", "other"),
                    "page": t.get("page", "?"),
                    "issue": f"FEL: {'; '.join(error_msgs)}",
                    "columns": t.get("columns", [])
                })
                break

    tables_json = json.dumps(tables_to_fix, ensure_ascii=False, indent=2)
    all_ids = sorted(list(missing_table_ids | tables_with_errors))

    prompt = f"""EXTRAHERA/KORRIGERA TABELLER

Följande tabeller behöver extraheras eller korrigeras:

{tables_json}

DOKUMENTINFO:
- Språk: {language}
- Nummerformat: {number_format}

RETURNERA JSON:
{{
  "tables": [
    {{
      "id": "table_X",
      "title": "Tabellens titel",
      "type": "income_statement|balance_sheet|cash_flow|kpi|other",
      "page": N,
      "columns": ["", "Kolumn1", "Kolumn2", ...],
      "rows": [
        {{"label": "Faktiskt radnamn från PDF", "values": [null, 123, 456], "order": 1}}
      ]
    }}
  ]
}}

KRITISKA REGLER:
1. Extrahera ALLA tabeller i listan ({', '.join(all_ids)})
2. Läs FAKTISKA radnamn från PDF - aldrig generiska som "1", "row 1"
3. Första värdet i values är ALLTID null (label-kolumnen)
4. Antal values MÅSTE matcha antal columns
5. Konvertera tal korrekt: {"komma=decimal, mellanslag=tusen" if number_format == "swedish" else "punkt=decimal, komma=tusen"}
"""

    # Steg 5: Kör Haiku retry
    async with semaphore:
        try:
            full_response_text = ""
            input_tokens = 0
            output_tokens = 0

            print(f"\n   [RETRY] Kör Haiku för {len(tables_to_fix)} tabeller...", flush=True)

            async with client.messages.stream(
                model=HAIKU_MODEL,
                max_tokens=32000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            ) as stream:
                async for text in stream.text_stream:
                    full_response_text += text
                final_message = await stream.get_final_message()
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens

            elapsed = time.perf_counter() - start_time
            result = parse_json_response(full_response_text)

            # Steg 6: Uppdatera tabeller med resultat
            fixed_tables = result.get("tables", [])
            fixed_ids = {t.get("id") for t in fixed_tables}

            # Ta bort gamla versioner av fixade tabeller
            current_tables = [t for t in current_tables if t.get("id") not in fixed_ids]

            # Lägg till fixade tabeller
            current_tables.extend(fixed_tables)

            # Beräkna kostnad (Haiku-priser)
            retry_cost = (input_tokens * HAIKU_INPUT_PRICE + output_tokens * HAIKU_OUTPUT_PRICE) / 1_000_000 * USD_TO_SEK

            print(f"      [RETRY KLAR] {len(fixed_tables)}/{len(tables_to_fix)} tabeller fixade "
                  f"({elapsed:.1f}s, {input_tokens:,}+{output_tokens:,} tokens, {retry_cost:.2f} SEK)", flush=True)

            # Validera igen
            final_validation = validate_tables(current_tables)

            retry_stats = {
                "retry_count": 1,
                "tables_retried": len(tables_to_fix),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "elapsed_seconds": round(elapsed, 2),
                "cost_sek": round(retry_cost, 4),
            }

            return current_tables, final_validation, retry_stats

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            print(f"   [VARNING] Haiku retry misslyckades: {e}", flush=True)

            retry_stats = {
                "retry_count": 1,
                "tables_retried": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "elapsed_seconds": round(elapsed, 2),
                "cost_sek": 0.0,
            }

            return current_tables, validation_result, retry_stats


async def run_pass_3(
    pdf_base64: str,
    structure_map: dict,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> PassResult:
    """
    Pass 3: Textextraktion med Haiku.

    Extraherar narrativ text från alla sektioner.
    """
    start_time = time.perf_counter()

    sections = structure_map.get("structure_map", {}).get("sections", [])
    section_ids = [s["id"] for s in sections]

    if not section_ids:
        return PassResult(
            pass_number=3,
            model="haiku",
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=0.0,
            data={"sections": [], "quotes": [], "contacts": [], "calendar": [], "footnotes": []}
        )

    # Hämta språk från Pass 1 metadata
    metadata = structure_map.get("metadata", {})
    language = metadata.get("sprak", "sv")

    prompt = PASS_3_TEXT_PROMPT.format(
        structure_map_json=json.dumps(structure_map, ensure_ascii=False, indent=2),
        section_ids=", ".join(section_ids),
        language=language
    )

    async with semaphore:
        # Använd streaming för att undvika timeout
        full_response_text = ""
        input_tokens = 0
        output_tokens = 0

        async with client.messages.stream(
            model=HAIKU_MODEL,
            max_tokens=32000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        ) as stream:
            async for text in stream.text_stream:
                full_response_text += text
            final_message = await stream.get_final_message()
            input_tokens = final_message.usage.input_tokens
            output_tokens = final_message.usage.output_tokens

        result = parse_json_response(full_response_text)
        elapsed = time.perf_counter() - start_time

        return PassResult(
            pass_number=3,
            model="haiku",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
            data=result
        )


def merge_results(
    pass_1: PassResult,
    pass_2: PassResult,
    pass_3: PassResult
) -> PipelineResult:
    """
    Kombinera resultat fran alla tre pass till slutgiltig struktur.
    """
    total_cost = sum(calculate_pass_cost(p) for p in [pass_1, pass_2, pass_3])

    # Tom retry_stats (fylls i separat)
    empty_retry_stats: RetryStats = {
        "retry_count": 0,
        "tables_retried": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "elapsed_seconds": 0.0,
        "cost_sek": 0.0,
    }

    return PipelineResult(
        metadata=pass_1["data"].get("metadata", {}),
        tables=pass_2["data"].get("tables", []),
        sections=pass_3["data"].get("sections", []),
        charts=pass_2["data"].get("charts", []),
        pass_info=[pass_1, pass_2, pass_3],
        retry_stats=empty_retry_stats,
        total_cost_sek=total_cost
    )


async def extract_pdf_multi_pass(
    pdf_path: str,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    company_id: str,
    progress_callback: Callable[[str, str, dict | None], None] | None = None,
    use_cache: bool = True,
) -> dict:
    """
    Multi-pass extraktion av en PDF.

    Args:
        pdf_path: Sökväg till PDF
        client: Anthropic async-klient
        semaphore: För rate-limiting
        company_id: Bolagets UUID
        progress_callback: Callback för progress
        use_cache: Om True, använd cachad data

    Returns:
        Dict kompatibelt med excel_builder.py
    """
    pdf_hash = get_pdf_hash(pdf_path)
    filename = Path(pdf_path).stem

    # Cache-kontroll
    if use_cache:
        # Stöd både "q1-2025" och "2025-q1" format
        period_match = re.search(r'[qQ](\d)[_-]?(\d{4})', filename)
        if period_match:
            quarter = int(period_match.group(1))
            year = int(period_match.group(2))
        else:
            # Alternativt format: 2025-q1
            period_match = re.search(r'(\d{4})[_-]?[qQ](\d)', filename)
            if period_match:
                year = int(period_match.group(1))
                quarter = int(period_match.group(2))
        if period_match:
            if period_exists(company_id, quarter, year, pdf_hash):
                if progress_callback:
                    progress_callback(pdf_path, "cached", None)
                data = load_period(company_id, quarter, year)
                if data:
                    data["_source_file"] = str(pdf_path)
                    return data

    if progress_callback:
        progress_callback(pdf_path, "extracting", None)

    # Läs PDF
    pdf_bytes = Path(pdf_path).read_bytes()
    pdf_base64 = base64.standard_b64encode(pdf_bytes).decode()

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            extraction_start = time.perf_counter()

            # === PASS 1: Strukturidentifiering ===
            if progress_callback:
                progress_callback(pdf_path, "pass_1", None)

            pass_1 = await run_pass_1(pdf_base64, client, semaphore)
            p1_cost = calculate_pass_cost(pass_1)
            print(f"   Pass 1 (Haiku):  {pass_1['elapsed_seconds']:.1f}s | "
                  f"{pass_1['input_tokens']:,}+{pass_1['output_tokens']:,} tokens | "
                  f"{p1_cost:.2f} SEK", flush=True)

            # === PASS 2 & 3: Parallell extraktion ===
            if progress_callback:
                progress_callback(pdf_path, "pass_2_3", None)

            pass_2_task = asyncio.create_task(
                run_pass_2(pdf_base64, pass_1["data"], client, semaphore)
            )
            pass_3_task = asyncio.create_task(
                run_pass_3(pdf_base64, pass_1["data"], client, semaphore)
            )

            pass_2, pass_3 = await asyncio.gather(pass_2_task, pass_3_task)

            p2_cost = calculate_pass_cost(pass_2)
            p3_cost = calculate_pass_cost(pass_3)
            print(f"   Pass 2 (Sonnet): {pass_2['elapsed_seconds']:.1f}s | "
                  f"{pass_2['input_tokens']:,}+{pass_2['output_tokens']:,} tokens | "
                  f"{p2_cost:.2f} SEK", flush=True)
            print(f"   Pass 3 (Haiku):  {pass_3['elapsed_seconds']:.1f}s | "
                  f"{pass_3['input_tokens']:,}+{pass_3['output_tokens']:,} tokens | "
                  f"{p3_cost:.2f} SEK", flush=True)

            # === VALIDERING & RETRY (förenklad med Haiku) ===
            if progress_callback:
                progress_callback(pdf_path, "validating", None)

            tables = pass_2["data"].get("tables", [])
            validated_tables, validation_result, retry_stats = await validate_and_retry_with_haiku(
                pdf_base64, tables, pass_1["data"], client, semaphore
            )

            # Uppdatera pass_2 med validerade tabeller
            pass_2["data"]["tables"] = validated_tables

            # Validera sections (ingen retry, bara varningar)
            sections = pass_3["data"].get("sections", [])
            section_validation = validate_sections(sections)

            # Tydlig valideringsrapport
            table_count = len(validated_tables)
            section_count = len(sections)
            error_count = len(validation_result.errors)
            warning_count = len(validation_result.warnings)
            retry_count = retry_stats['retry_count']
            tables_fixed = retry_stats['tables_retried']

            print(f"\n   [VALIDERING]", flush=True)

            # Tabeller
            if error_count == 0 and retry_count == 0:
                print(f"      Tabeller: {table_count} st - OK", flush=True)
            elif error_count == 0 and retry_count > 0:
                print(f"      Tabeller: {table_count} st - OK ({tables_fixed} fixade efter {retry_count} retry)", flush=True)
            else:
                print(f"      Tabeller: {table_count} st - {error_count} FEL kvarstar", flush=True)
                for e in validation_result.errors:
                    print(f"         [FEL] {e.table_title}: {e.message}", flush=True)

            if warning_count > 0:
                print(f"      Varningar: {warning_count} minor (paverkar ej data)", flush=True)

            # Sections
            section_warning_count = len(section_validation.warnings) if section_validation.has_warnings else 0
            if section_warning_count == 0:
                print(f"      Sections: {section_count} st - OK", flush=True)
            else:
                print(f"      Sections: {section_count} st - {section_warning_count} varningar (minor)", flush=True)

            # Kombinera resultat
            result = merge_results(pass_1, pass_2, pass_3)

            # Beräkna totaler inklusive retry
            total_cost_with_retries = result["total_cost_sek"] + retry_stats["cost_sek"]
            total_elapsed = time.perf_counter() - extraction_start

            # Konvertera till format kompatibelt med excel_builder
            output = {
                "metadata": result["metadata"],
                "tables": result["tables"],
                "sections": result["sections"],
                "charts": result["charts"],
                "_source_file": str(pdf_path),
                "_pipeline_info": {
                    "passes": [
                        {
                            "pass": p["pass_number"],
                            "model": p["model"],
                            "input_tokens": p["input_tokens"],
                            "output_tokens": p["output_tokens"],
                            "elapsed_seconds": round(p["elapsed_seconds"], 2),
                            "cost_sek": round(calculate_pass_cost(p), 4)
                        }
                        for p in result["pass_info"]
                    ],
                    "retry_stats": retry_stats,
                    "total_cost_sek": round(total_cost_with_retries, 2),
                    "total_elapsed_seconds": round(total_elapsed, 2),
                    "validation": {
                        "tables": {
                            "is_valid": validation_result.is_valid,
                            "error_count": len(validation_result.errors),
                            "warning_count": len(validation_result.warnings),
                            "errors": [
                                {
                                    "table_id": e.table_id,
                                    "table_title": e.table_title,
                                    "error_type": e.error_type,
                                    "message": e.message,
                                    "row_index": e.row_index
                                }
                                for e in validation_result.errors
                            ] if validation_result.errors else [],
                        },
                        "sections": {
                            "is_valid": section_validation.is_valid,
                            "error_count": len(section_validation.errors),
                            "warning_count": len(section_validation.warnings),
                            "warnings": [
                                {
                                    "section_id": w.table_id,
                                    "section_title": w.table_title,
                                    "warning_type": w.error_type,
                                    "message": w.message
                                }
                                for w in section_validation.warnings
                            ] if section_validation.warnings else [],
                        }
                    }
                }
            }

            # Skriv ut sammanfattning
            total_input = sum(p["input_tokens"] for p in result["pass_info"]) + retry_stats["input_tokens"]
            total_output = sum(p["output_tokens"] for p in result["pass_info"]) + retry_stats["output_tokens"]

            print(f"\n   --- {filename} KLAR ---")
            print(f"   Tid: {total_elapsed:.1f}s | Tokens: {total_input:,}+{total_output:,} | Kostnad: {total_cost_with_retries:.2f} SEK")
            if retry_stats["retry_count"] > 0:
                print(f"   Retry (Haiku): {retry_stats['tables_retried']} tabeller ({retry_stats['cost_sek']:.2f} SEK)")
            print(f"   Tabeller: {len(result['tables'])} | Sektioner: {len(result['sections'])} | Grafer: {len(result['charts'])}")

            # Spara till Supabase
            save_period(company_id, output, pdf_hash, str(pdf_path))

            if progress_callback:
                progress_callback(pdf_path, "done", {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cost_sek": total_cost_with_retries,
                })

            return output

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                print(f"\n[VARNING] Fel vid extraktion av {filename}:")
                print(f"   {type(e).__name__}: {e}")
                print(f"\n   Retry {attempt + 1}/{MAX_RETRIES}?")

                while True:
                    answer = input("   Försök igen? [Y/N]: ").strip().upper()
                    if answer == "Y":
                        wait_time = 2 ** attempt
                        print(f"   Väntar {wait_time}s innan retry...")
                        await asyncio.sleep(wait_time)
                        break
                    elif answer == "N":
                        if progress_callback:
                            progress_callback(pdf_path, f"failed: {e}", None)
                        raise
                    else:
                        print("   Ange Y eller N")
            else:
                if progress_callback:
                    progress_callback(pdf_path, f"failed: {e}", None)
                raise

    raise last_error  # type: ignore


async def extract_all_pdfs_multi_pass(
    pdf_paths: list[str],
    company_name: str,
    on_progress: Callable[[str, str], None] | None = None,
    use_cache: bool = True,
) -> tuple[list[dict], list[tuple[str, Exception]]]:
    """
    Multi-pass extraktion av alla PDFs.

    Samma signatur som extractor.extract_all_pdfs för kompatibilitet.

    Args:
        pdf_paths: Lista med sökvägar till PDF-filer
        company_name: Bolagsnamn för datalagring
        on_progress: Callback för progress-uppdateringar
        use_cache: Om True, använd cachad data

    Returns:
        Tuple av (lyckade resultat, misslyckade med fel)
    """
    # Ladda om .env för att få senaste nyckeln
    load_dotenv(override=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY saknas. "
            "Exportera den med: export ANTHROPIC_API_KEY='din-nyckel'"
        )

    # Hämta eller skapa bolag i Supabase
    company = get_or_create_company(company_name)
    company_id = company["id"]

    client = AsyncAnthropic(api_key=api_key, timeout=API_TIMEOUT)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def safe_extract(path: str):
        """Wrapper som fångar fel istället för att krascha"""
        try:
            return await extract_pdf_multi_pass(
                path, client, semaphore, company_id,
                on_progress, use_cache
            )
        except Exception as e:
            return (path, e)

    # Kör alla extraktioner parallellt
    results = await asyncio.gather(*[safe_extract(p) for p in pdf_paths])

    # Separera lyckade och misslyckade
    successful = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, tuple)]

    return successful, failed
