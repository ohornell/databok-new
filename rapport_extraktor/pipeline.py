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
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, TypedDict

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from pypdf import PdfReader, PdfWriter

# Ladda .env-fil
load_dotenv()

from prompts import PASS_1_STRUCTURE_PROMPT, PASS_2_TABLES_PROMPT, PASS_3_TEXT_PROMPT
from supabase_client import (
    get_or_create_company,
    save_period,
    save_period_atomic_async,
    update_period_status,
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
from extraction_log import process_extraction_complete
from checkpoint import (
    generate_batch_id,
    get_completed_files,
    add_completed_file,
    add_failed_file,
    get_batch_progress,
    save_checkpoint,
)

# Modell-IDs
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-5-20250929"

# Konfiguration
# Token limits: 450K input/min TOTALT för alla requests till en modell
# Med ~75K tokens/PDF → max ~6 samtida requests för att hålla under 450K
# Pass körs sekventiellt per PDF, så faktisk belastning sprids ut över tid
MAX_CONCURRENT = 4    # 4 samtida × 75K = 300K tokens, säker marginal under 450K limit
MAX_RETRIES = 3
API_TIMEOUT = 300     # 5 minuter timeout per API-anrop
BATCH_SIZE = 10       # Antal PDFs att processa åt gången
BATCH_TIMEOUT = 3600  # 1 timme max per batch

# Priser (USD per 1M tokens)
HAIKU_INPUT_PRICE = 0.80
HAIKU_OUTPUT_PRICE = 4.00
SONNET_INPUT_PRICE = 3.00
SONNET_OUTPUT_PRICE = 15.00
USD_TO_SEK = 10.50


def extract_pdf_pages(pdf_bytes: bytes, pages: list[int]) -> bytes:
    """
    Extrahera specifika sidor från PDF.

    Args:
        pdf_bytes: Rå PDF-data
        pages: Lista med sidnummer (1-indexerade, som PDF-viewer)

    Returns:
        Ny PDF med endast de angivna sidorna
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    total_pages = len(reader.pages)

    for page_num in sorted(pages):
        # Konvertera till 0-indexerat för pypdf
        idx = page_num - 1
        if 0 <= idx < total_pages:
            writer.add_page(reader.pages[idx])

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


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


async def validate_and_retry_with_sonnet(
    pdf_bytes: bytes,
    tables: list[dict],
    structure_map: dict,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> tuple[list[dict], ValidationResult, RetryStats]:
    """
    Validera tabeller och kör ETT retry med Sonnet på relevanta sidor.

    Skillnad från validate_and_retry_with_haiku:
    1. Använder Sonnet istället för Haiku (bättre kvalitet)
    2. Extraherar endast relevanta sidor (lägre kostnad)

    Flöde:
    1. Validera extraherade tabeller (lokal, ingen API)
    2. Hitta saknade tabeller (finns i Pass 1 men inte Pass 2)
    3. Om något saknas ELLER har fel → extrahera relevanta sidor + Sonnet-anrop

    Args:
        pdf_bytes: Rå PDF-data (bytes, inte base64)
        tables: Lista med extraherade tabeller från Pass 2
        structure_map: Strukturkarta från Pass 1
        client: Anthropic async-klient
        semaphore: För rate-limiting

    Returns:
        Tuple av (slutgiltiga tabeller, ValidationResult, RetryStats)
    """
    current_tables = tables.copy()

    # Steg 1: Validera extraherade tabeller (inkl. kolumnjämförelse med Pass 1)
    validation_result = validate_tables(current_tables, structure_map)
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

    # Steg 4: Bygg prompt och samla sidor
    start_time = time.perf_counter()

    # Hämta metadata
    metadata = structure_map.get("metadata", {})
    language = metadata.get("sprak", "sv")
    number_format = metadata.get("number_format", "swedish")

    # Bygg lista över tabeller att extrahera/korrigera
    tables_to_fix = []
    pages_needed = set()

    # Saknade tabeller - hämta info från Pass 1
    for tid in missing_table_ids:
        for t in structure_map.get("structure_map", {}).get("tables", []):
            if t["id"] == tid:
                page = t.get("page")
                tables_to_fix.append({
                    "id": tid,
                    "title": t.get("title", "Okänd"),
                    "type": t.get("type", "other"),
                    "page": page if page else "?",
                    "issue": "SAKNAS - extrahera från PDF",
                    "columns": t.get("column_headers", [])
                })
                if isinstance(page, int) and page >= 1:
                    pages_needed.add(page)
                    if page > 1:
                        pages_needed.add(page - 1)  # Sidan innan för kontext
                    pages_needed.add(page + 1)  # Sidan efter
                break

    # Tabeller med fel - inkludera nuvarande data + felbeskrivning
    for tid in tables_with_errors:
        for t in current_tables:
            if t.get("id") == tid:
                page = t.get("page")
                errors = [e for e in validation_result.errors if e.table_id == tid]
                error_msgs = [e.message for e in errors]
                tables_to_fix.append({
                    "id": tid,
                    "title": t.get("title", "Okänd"),
                    "type": t.get("type", "other"),
                    "page": page if page else "?",
                    "issue": f"FEL: {'; '.join(error_msgs)}",
                    "columns": t.get("columns", [])
                })
                if isinstance(page, int) and page >= 1:
                    pages_needed.add(page)
                    if page > 1:
                        pages_needed.add(page - 1)
                    pages_needed.add(page + 1)
                break

    # Steg 5: Extrahera relevanta sidor från PDF
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total_pages = len(reader.pages)

    # Begränsa till giltiga sidor
    pages_needed = {p for p in pages_needed if 1 <= p <= total_pages}

    # Extrahera bara relevanta sidor om det sparar >50% av PDF:en
    if pages_needed and len(pages_needed) < total_pages * 0.5:
        partial_pdf_bytes = extract_pdf_pages(pdf_bytes, sorted(pages_needed))
        partial_pdf_base64 = base64.standard_b64encode(partial_pdf_bytes).decode()
        pages_info = f" (sidor: {sorted(pages_needed)})"
        page_note = f"\n\nVIKTIGT: Denna PDF innehåller endast sidorna {sorted(pages_needed)} från originaldokumentet."
    else:
        # Använd hela PDF:en
        partial_pdf_base64 = base64.standard_b64encode(pdf_bytes).decode()
        pages_info = f" (hela PDF:en, {total_pages} sidor)"
        page_note = ""

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
{page_note}"""

    # Steg 6: Kör Sonnet retry
    async with semaphore:
        try:
            full_response_text = ""
            input_tokens = 0
            output_tokens = 0

            print(f"\n   [RETRY] Kör Sonnet för {len(tables_to_fix)} tabeller{pages_info}...", flush=True)

            async with client.messages.stream(
                model=SONNET_MODEL,
                max_tokens=32000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": partial_pdf_base64
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

            # Steg 7: Uppdatera tabeller med resultat
            fixed_tables = result.get("tables", [])
            fixed_ids = {t.get("id") for t in fixed_tables}

            # Ta bort gamla versioner av fixade tabeller
            current_tables = [t for t in current_tables if t.get("id") not in fixed_ids]

            # Lägg till fixade tabeller
            current_tables.extend(fixed_tables)

            # Beräkna kostnad (Sonnet-priser)
            retry_cost = (input_tokens * SONNET_INPUT_PRICE + output_tokens * SONNET_OUTPUT_PRICE) / 1_000_000 * USD_TO_SEK

            print(f"      [RETRY KLAR] {len(fixed_tables)}/{len(tables_to_fix)} tabeller fixade "
                  f"({elapsed:.1f}s, {input_tokens:,}+{output_tokens:,} tokens, {retry_cost:.2f} SEK)", flush=True)

            # Validera igen (med struktur för kolumnjämförelse)
            final_validation = validate_tables(current_tables, structure_map)

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
            print(f"   [VARNING] Sonnet retry misslyckades: {e}", flush=True)

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
    company_name: str,
    progress_callback: Callable[[str, str, dict | None], None] | None = None,
    use_cache: bool = True,
    base_folder: str | None = None,
) -> dict:
    """
    Multi-pass extraktion av en PDF.

    Args:
        pdf_path: Sökväg till PDF
        client: Anthropic async-klient
        semaphore: För rate-limiting
        company_id: Bolagets UUID
        company_name: Bolagsnamn (för loggning och filflyttning)
        progress_callback: Callback för progress
        use_cache: Om True, använd cachad data
        base_folder: Basmapp för rapporter (för filflyttning efter extraktion)

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

            # === VALIDERING & RETRY (Sonnet med sidextraktion) ===
            if progress_callback:
                progress_callback(pdf_path, "validating", None)

            tables = pass_2["data"].get("tables", [])
            validated_tables, validation_result, retry_stats = await validate_and_retry_with_sonnet(
                pdf_bytes, tables, pass_1["data"], client, semaphore
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

            # === PASS 1 STATISTIK (för loggning) ===
            structure_map = pass_1["data"].get("structure_map", {})
            pass1_tables = structure_map.get("tables", [])
            pass1_sections = structure_map.get("sections", [])
            pass1_charts = structure_map.get("charts", [])

            # Hitta saknade tabeller (fanns i pass 1 men inte extraherade)
            expected_table_ids = {t["id"] for t in pass1_tables}
            extracted_table_ids = {t.get("id") for t in result["tables"]}
            missing_table_ids = expected_table_ids - extracted_table_ids

            missing_tables = []
            for tid in missing_table_ids:
                for t in pass1_tables:
                    if t["id"] == tid:
                        missing_tables.append({
                            "table_id": tid,
                            "table_title": t.get("title", "Okänd"),
                            "page": t.get("page", "?"),
                            "type": t.get("type", "other"),
                        })
                        break

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
                    "pass1_counts": {
                        "tables": len(pass1_tables),
                        "sections": len(pass1_sections),
                        "charts": len(pass1_charts),
                    },
                    "missing_tables": missing_tables,
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
                print(f"   Retry (Sonnet): {retry_stats['tables_retried']} tabeller ({retry_stats['cost_sek']:.2f} SEK)")
            print(f"   Tabeller: {len(result['tables'])} | Sektioner: {len(result['sections'])} | Grafer: {len(result['charts'])}")

            # Samla alla fel för explicit loggning
            all_errors = []
            final_status = "success"

            # Tabell-valideringsfel
            if validation_result.errors:
                final_status = "partial"
                for e in validation_result.errors:
                    all_errors.append({
                        "error_type": e.error_type,
                        "severity": "error",
                        "component": "tables",
                        "details": {
                            "table_id": e.table_id,
                            "table_title": e.table_title,
                            "message": e.message,
                            "row_index": e.row_index
                        }
                    })

            # Section-varningar (loggas som warnings)
            for w in section_validation.warnings:
                all_errors.append({
                    "error_type": w.error_type,
                    "severity": "warning",
                    "component": "sections",
                    "details": {
                        "section_id": w.table_id,
                        "section_title": w.table_title,
                        "message": w.message
                    }
                })

            # Spara till Supabase med atomisk sparning (async för att inte blockera)
            period_id, section_ids = await save_period_atomic_async(company_id, output, pdf_hash, str(pdf_path))

            # Generera embeddings med explicit felhantering (async för att inte blockera)
            embeddings_count = 0
            if section_ids:
                try:
                    from supabase_client import generate_embeddings_for_sections_async
                    embeddings_count = await generate_embeddings_for_sections_async(section_ids)
                    if embeddings_count > 0:
                        print(f"   [EMBEDDING] {embeddings_count} sections har fatt embeddings")
                    if embeddings_count < len(section_ids):
                        all_errors.append({
                            "error_type": "embeddings_incomplete",
                            "severity": "warning",
                            "component": "embeddings",
                            "details": {
                                "expected": len(section_ids),
                                "generated": embeddings_count
                            }
                        })
                except Exception as emb_err:
                    all_errors.append({
                        "error_type": "embeddings_failed",
                        "severity": "error",
                        "component": "embeddings",
                        "details": {"error": str(emb_err)}
                    })
                    if final_status == "success":
                        final_status = "partial"
                    print(f"   [EMBEDDING] Kunde inte generera embeddings: {emb_err}")

            # Uppdatera slutstatus
            update_period_status(
                period_id,
                status=final_status,
                errors=all_errors if all_errors else None,
                embeddings_count=embeddings_count
            )

            if final_status == "partial":
                error_count = len([e for e in all_errors if e["severity"] in ("error", "critical")])
                warning_count = len([e for e in all_errors if e["severity"] == "warning"])
                print(f"   [STATUS] partial - {error_count} fel, {warning_count} varningar")

            # Flytta fil och uppdatera logg (om base_folder är satt)
            if base_folder:
                try:
                    process_extraction_complete(pdf_path, company_name, base_folder)
                except Exception as log_err:
                    print(f"   [VARNING] Kunde inte flytta/logga: {log_err}")

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
                print(f"   Retry {attempt + 1}/{MAX_RETRIES}...")
                wait_time = 2 ** attempt
                print(f"   Väntar {wait_time}s innan retry...")
                await asyncio.sleep(wait_time)
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
    base_folder: str | None = None,
    batch_id: str | None = None,
    resume: bool = True,
    quiet: bool = False,
) -> tuple[list[dict], list[tuple[str, Exception]]]:
    """
    Multi-pass extraktion av alla PDFs med batch-processning och checkpointing.

    Processerar PDFs i batchar om BATCH_SIZE för att kontrollera minnesanvändning.
    Sparar progress efter varje fil för att möjliggöra återstart vid avbrott.

    Args:
        pdf_paths: Lista med sökvägar till PDF-filer
        company_name: Bolagsnamn för datalagring
        on_progress: Callback för progress-uppdateringar
        use_cache: Om True, använd cachad data från databasen
        base_folder: Basmapp för rapporter (för filflyttning efter extraktion)
        batch_id: Unikt ID för denna batch (genereras automatiskt om None)
        resume: Om True, skippa redan processade filer från tidigare körning
        quiet: Om True, undertryck progress-utskrifter (använd med progress-tracker)

    Returns:
        Tuple av (lyckade resultat, misslyckade med fel)
    """
    import gc

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

    # Generera batch-ID om inget anges
    if batch_id is None:
        batch_id = generate_batch_id(company_id)

    # Hämta redan processade filer om vi återupptar
    completed_files: set[str] = set()
    if resume:
        completed_files = get_completed_files(batch_id)
        if completed_files and not quiet:
            print(f"\n[CHECKPOINT] Återupptar batch {batch_id}")
            print(f"   Redan klara: {len(completed_files)}/{len(pdf_paths)} filer")

    # Filtrera bort redan processade
    remaining_paths = [p for p in pdf_paths if str(p) not in completed_files]

    if not remaining_paths:
        if not quiet:
            print(f"\n[CHECKPOINT] Alla {len(pdf_paths)} filer redan processade!")
        return [], []

    # Initiera checkpoint med total count
    save_checkpoint(
        batch_id=batch_id,
        completed=list(completed_files),
        failed=[],
        total_files=len(pdf_paths)
    )

    client = AsyncAnthropic(api_key=api_key, timeout=API_TIMEOUT)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    all_successful: list[dict] = []
    all_failed: list[tuple[str, Exception]] = []

    async def safe_extract(path: str) -> dict | tuple[str, Exception]:
        """Wrapper som fångar fel istället för att krascha"""
        try:
            return await extract_pdf_multi_pass(
                path, client, semaphore, company_id, company_name,
                on_progress, use_cache, base_folder
            )
        except Exception as e:
            return (path, e)

    # Processa i batchar för minneskontroll
    total_batches = (len(remaining_paths) + BATCH_SIZE - 1) // BATCH_SIZE
    if not quiet:
        print(f"\n[BATCH] Processerar {len(remaining_paths)} filer i {total_batches} batchar à {BATCH_SIZE}")

    for batch_num, i in enumerate(range(0, len(remaining_paths), BATCH_SIZE), 1):
        batch = remaining_paths[i:i + BATCH_SIZE]
        if not quiet:
            print(f"\n[BATCH {batch_num}/{total_batches}] Startar {len(batch)} filer...")

        # Kör denna batch parallellt med timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[safe_extract(p) for p in batch]),
                timeout=BATCH_TIMEOUT
            )
        except asyncio.TimeoutError:
            if not quiet:
                print(f"   [TIMEOUT] Batch {batch_num} tog över {BATCH_TIMEOUT}s - markerar som misslyckade")
            for path in batch:
                all_failed.append((path, TimeoutError(f"Batch timeout efter {BATCH_TIMEOUT}s")))
                add_failed_file(batch_id, str(path), f"Batch timeout efter {BATCH_TIMEOUT}s", len(pdf_paths))
            continue

        # Processa resultat och uppdatera checkpoint
        for path, result in zip(batch, results):
            if isinstance(result, dict):
                all_successful.append(result)
                add_completed_file(batch_id, str(path), len(pdf_paths))
            else:
                # result är tuple (path, exception)
                all_failed.append(result)
                _, error = result
                add_failed_file(batch_id, str(path), str(error), len(pdf_paths))

        # Progress-rapport
        completed, failed, total = get_batch_progress(batch_id)
        if not quiet:
            print(f"   Progress: {completed}/{total} klara, {failed} misslyckade")

        # Explicit minnesrensning mellan batchar
        gc.collect()

        # Kort paus mellan batchar för att undvika rate limits
        if i + BATCH_SIZE < len(remaining_paths):
            await asyncio.sleep(2)

    # Slutrapport
    if not quiet:
        print(f"\n[KLAR] Batch {batch_id} färdig:")
        print(f"   Lyckade: {len(all_successful)}")
        print(f"   Misslyckade: {len(all_failed)}")

    return all_successful, all_failed


async def retry_failed_extractions(
    batch_id: str,
    company_name: str,
    on_progress: Callable[[str, str], None] | None = None,
    use_cache: bool = False,
    base_folder: str | None = None,
    quiet: bool = False,
) -> tuple[list[dict], list[tuple[str, Exception]]]:
    """
    Kör om misslyckade extraktioner från en tidigare batch.

    Args:
        batch_id: ID för batchen att återförsöka
        company_name: Bolagsnamn
        on_progress: Progress callback
        use_cache: Om True, använd cache (default False för retry)
        base_folder: Basmapp för rapporter
        quiet: Om True, undertryck progress-utskrifter

    Returns:
        Tuple av (lyckade, misslyckade)
    """
    from checkpoint import get_failed_files, clear_checkpoint

    failed_files = get_failed_files(batch_id)
    if not failed_files:
        if not quiet:
            print(f"[RETRY] Inga misslyckade filer att köra om för batch {batch_id}")
        return [], []

    # Filtrera till filer som fortfarande existerar
    existing_paths = [
        f["path"] for f in failed_files
        if Path(f["path"]).exists()
    ]

    if not existing_paths:
        if not quiet:
            print(f"[RETRY] Inga misslyckade filer existerar längre")
        return [], []

    if not quiet:
        print(f"\n[RETRY] Kör om {len(existing_paths)} misslyckade filer från batch {batch_id}")

    # Skapa ny batch för retry
    retry_batch_id = f"retry_{batch_id}"

    return await extract_all_pdfs_multi_pass(
        pdf_paths=existing_paths,
        company_name=company_name,
        on_progress=on_progress,
        use_cache=use_cache,
        base_folder=base_folder,
        batch_id=retry_batch_id,
        resume=False,  # Kör alltid om vid retry
        quiet=quiet,
    )
