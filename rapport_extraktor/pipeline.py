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

# Modell-IDs
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-5-20250929"

# Konfiguration
MAX_CONCURRENT = 5
MAX_RETRIES = 3

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


class PipelineResult(TypedDict):
    metadata: dict
    tables: list[dict]
    sections: list[dict]
    charts: list[dict]
    pass_info: list[PassResult]
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
                print(f"\n⚠️  JSON-parsningsfel: {e}")
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
            pass_number=2,
            model="sonnet",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
            data=result
        )


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
    Kombinera resultat från alla tre pass till slutgiltig struktur.
    """
    total_cost = sum(calculate_pass_cost(p) for p in [pass_1, pass_2, pass_3])

    return PipelineResult(
        metadata=pass_1["data"].get("metadata", {}),
        tables=pass_2["data"].get("tables", []),
        sections=pass_3["data"].get("sections", []),
        charts=pass_2["data"].get("charts", []),
        pass_info=[pass_1, pass_2, pass_3],
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
        period_match = re.search(r'[qQ](\d)[_-]?(\d{4})', filename)
        if period_match:
            quarter = int(period_match.group(1))
            year = int(period_match.group(2))
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
            # === PASS 1: Strukturidentifiering ===
            if progress_callback:
                progress_callback(pdf_path, "pass_1", None)

            pass_1 = await run_pass_1(pdf_base64, client, semaphore)

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

            # Kombinera resultat
            result = merge_results(pass_1, pass_2, pass_3)

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
                    "total_cost_sek": round(result["total_cost_sek"], 2)
                }
            }

            # Spara till Supabase
            save_period(company_id, output, pdf_hash, str(pdf_path))

            # Token-info för progress callback
            total_input = sum(p["input_tokens"] for p in result["pass_info"])
            total_output = sum(p["output_tokens"] for p in result["pass_info"])

            if progress_callback:
                progress_callback(pdf_path, "done", {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                })

            return output

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                print(f"\n⚠️  Fel vid extraktion av {filename}:")
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

    client = AsyncAnthropic(api_key=api_key)
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
