"""
Async PDF-extraktion via Claude API.
Skalbar för 30+ PDF-filer med Supabase-caching och progress-tracking.
"""

import asyncio
import base64
import json
import os
import re
from pathlib import Path
from typing import Callable

from anthropic import AsyncAnthropic

from prompts import EXTRACTION_PROMPT, FULL_EXTRACTION_PROMPT, FULL_EXTRACTION_PROMPT_NO_CHARTS
from supabase_client import (
    get_or_create_company,
    period_exists,
    save_period,
    load_all_periods,
    get_pdf_hash,
    parse_period_string,
)

# Konfiguration
MAX_CONCURRENT = 5  # Max samtidiga API-anrop (rate limit-vänlig)
MAX_RETRIES = 3     # Antal retry vid fel

# Loggning
VERBOSE_TOKEN_LOG = os.environ.get("VERBOSE_TOKENS", "").lower() in ("1", "true", "yes")
VERBOSE_TIMING = os.environ.get("VERBOSE_TIMING", "").lower() in ("1", "true", "yes")


import time


class TimingLogger:
    """Enkel tidsmätare för att logga hur lång tid varje steg tar."""

    def __init__(self, filename: str, enabled: bool = True):
        self.filename = filename
        self.enabled = enabled
        self.start_time = time.time()
        self.last_step = self.start_time
        self.steps = []

    def step(self, name: str):
        """Logga ett steg med tid sedan förra steget."""
        if not self.enabled:
            return
        now = time.time()
        elapsed = now - self.last_step
        self.steps.append((name, elapsed))
        self.last_step = now

    def print_summary(self):
        """Skriv ut sammanfattning av alla steg."""
        if not self.enabled or not self.steps:
            return

        total = time.time() - self.start_time

        print(f"\n{'─' * 60}")
        print(f"⏱️  TIDSMÄTNING: {self.filename}")
        print(f"{'─' * 60}")

        for name, elapsed in self.steps:
            pct = (elapsed / total) * 100 if total > 0 else 0
            bar_len = int(pct / 5)  # Max 20 tecken
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"   {name:<25} {elapsed:>6.1f}s  {bar} {pct:>5.1f}%")

        print(f"{'─' * 60}")
        print(f"   {'TOTALT':<25} {total:>6.1f}s")
        print(f"{'─' * 60}\n")


def format_bytes(size: int) -> str:
    """Formatera bytes till läsbar storlek."""
    for unit in ["B", "KB", "MB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def log_token_usage(filename: str, pdf_size: int, prompt_len: int,
                    input_tokens: int, output_tokens: int, full_extraction: bool):
    """Logga detaljerad token-användning för felsökning."""
    # Priser (Claude Sonnet 4)
    PRICE_INPUT = 3.00 / 1_000_000   # $3 per 1M
    PRICE_OUTPUT = 15.00 / 1_000_000  # $15 per 1M
    USD_TO_SEK = 10.50

    input_cost = input_tokens * PRICE_INPUT * USD_TO_SEK
    output_cost = output_tokens * PRICE_OUTPUT * USD_TO_SEK
    total_cost = input_cost + output_cost

    # Uppskatta PDF-tokens (input minus prompt)
    estimated_pdf_tokens = input_tokens - (prompt_len // 4)  # ~4 chars per token

    print(f"\n{'─' * 60}")
    print(f"📊 TOKEN-LOGG: {filename}")
    print(f"{'─' * 60}")
    print(f"   Extraktionstyp: {'FULL' if full_extraction else 'STANDARD'}")
    print(f"   PDF-storlek:    {format_bytes(pdf_size)}")
    print(f"")
    print(f"   INPUT TOKENS:   {input_tokens:,}")
    print(f"     └─ PDF (~):   {estimated_pdf_tokens:,} ({input_cost:.2f} kr)")
    print(f"     └─ Prompt:    ~{prompt_len // 4:,}")
    print(f"")
    print(f"   OUTPUT TOKENS:  {output_tokens:,} ({output_cost:.2f} kr)")
    print(f"")
    print(f"   TOTAL KOSTNAD:  {total_cost:.2f} kr")
    print(f"{'─' * 60}\n")


def validate_extraction(data: dict, pdf_path: str) -> list[str]:
    """
    Validera extraherad data och returnera lista med varningar.
    Skriver ut varningar till konsolen.
    """
    warnings = []

    # Kolla om det är full extraktion (har tables)
    tables = data.get("tables", [])
    if not tables:
        return warnings  # Legacy-format, hoppa över validering

    table_types = {t.get("type") for t in tables}

    # Kontrollera att grundläggande tabelltyper finns
    required = ["income_statement", "balance_sheet", "cash_flow"]
    for req in required:
        if req not in table_types:
            warnings.append(f"SAKNAS: {req}")

    # Kolla att tabeller har data och korrekta kolumner
    for table in tables:
        title = table.get("title", "Okänd tabell")

        # Kolla för tomma tabeller
        if not table.get("rows"):
            warnings.append(f"TOM TABELL: {title}")

        # Kolla för None/tomma kolumner
        cols = table.get("columns", [])
        if not cols:
            warnings.append(f"INGA KOLUMNER: {title}")
        elif None in cols or "" in cols:
            warnings.append(f"FELAKTIG KOLUMN (null/tom): {title}")

    # Skriv ut varningar om det finns några
    if warnings:
        from pathlib import Path
        filename = Path(pdf_path).name
        print(f"\n⚠️  Varningar för {filename}:")
        for w in warnings:
            print(f"   - {w}")

    return warnings


def parse_json_response(response_text: str) -> dict:
    """
    Extrahera JSON från Claude's svar.
    Hanterar fall där JSON är inbäddat i markdown-block.
    """
    # Ta bort markdown code blocks om de finns
    text = response_text.strip()
    if text.startswith("```"):
        # Hitta första och sista ```
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])

    # Hitta JSON-objekt
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            raise ValueError(f"Ogiltig JSON: {e}")

    raise ValueError("Ingen JSON hittad i svaret")


async def extract_pdf(
    pdf_path: str,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    company_id: str,
    progress_callback: Callable[[str, str, dict | None], None] | None = None,
    use_cache: bool = True,
    full_extraction: bool = False,
    skip_charts: bool = False,
    use_streaming: bool = False,
    model: str = "sonnet"
) -> dict:
    """
    Extrahera data från en PDF.

    Args:
        pdf_path: Sökväg till PDF-filen
        client: Anthropic async-klient
        semaphore: Begränsar antal samtidiga anrop
        company_id: Bolagets UUID i Supabase
        progress_callback: Callback för progress (pdf_path, status, token_info)
        use_cache: Om True, använd cachad data om tillgänglig
        full_extraction: Om True, extrahera ALL data (sections + tables)
        skip_charts: Om True, extrahera inte grafer/diagram
        use_streaming: Om True, använd streaming API (långsammare)
        model: Vilken modell att använda ("sonnet" eller "haiku")

    Returns:
        Dict med extraherad data
    """
    pdf_hash = get_pdf_hash(pdf_path)

    # Kontrollera Supabase-cache
    if use_cache:
        # Försök hitta befintlig period med matchande hash
        # Vi behöver först extrahera för att veta period, så vi gör en snabb check
        # baserat på filnamnet om möjligt
        filename = Path(pdf_path).stem
        period_match = re.search(r'[qQ](\d)[_-]?(\d{4})', filename)
        if period_match:
            quarter = int(period_match.group(1))
            year = int(period_match.group(2))
            if period_exists(company_id, quarter, year, pdf_hash):
                if progress_callback:
                    progress_callback(pdf_path, "cached", None)
                # Ladda från Supabase
                from supabase_client import load_period
                data = load_period(company_id, quarter, year)
                if data:
                    data["_source_file"] = str(pdf_path)
                    return data

    async with semaphore:
        if progress_callback:
            progress_callback(pdf_path, "extracting", None)

        # Tidsmätning
        timer = TimingLogger(Path(pdf_path).name, enabled=VERBOSE_TIMING)

        # Läs PDF som base64
        pdf_bytes = Path(pdf_path).read_bytes()
        timer.step("Läs PDF från disk")

        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode()
        timer.step("Base64-kodning")

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                # Välj prompt baserat på extraktionstyp och skip_charts
                if full_extraction:
                    prompt = FULL_EXTRACTION_PROMPT_NO_CHARTS if skip_charts else FULL_EXTRACTION_PROMPT
                else:
                    prompt = EXTRACTION_PROMPT
                max_tokens = 24000 if full_extraction else 8192

                # Välj modell
                model_id = "claude-sonnet-4-20250514" if model == "sonnet" else "claude-haiku-3-5-20241022"

                full_response_text = ""
                input_tokens = 0
                output_tokens = 0

                # Bygg meddelande
                messages = [{
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

                if use_streaming:
                    # Streaming API (långsammare men visar progress)
                    async with client.messages.stream(
                        model=model_id,
                        max_tokens=max_tokens,
                        messages=messages
                    ) as stream:
                        async for text in stream.text_stream:
                            full_response_text += text

                        final_message = await stream.get_final_message()
                        input_tokens = final_message.usage.input_tokens
                        output_tokens = final_message.usage.output_tokens

                    timer.step("Claude API (streaming)")
                else:
                    # Non-streaming API (snabbare)
                    response = await client.messages.create(
                        model=model_id,
                        max_tokens=max_tokens,
                        messages=messages
                    )

                    full_response_text = response.content[0].text
                    input_tokens = response.usage.input_tokens
                    output_tokens = response.usage.output_tokens

                    timer.step("Claude API")

                # Parsa JSON från svaret
                result = parse_json_response(full_response_text)
                result["_source_file"] = str(pdf_path)
                timer.step("JSON-parsing")

                # Validera extraktionen (loggar varningar om något saknas)
                if full_extraction:
                    validate_extraction(result, pdf_path)
                timer.step("Validering")

                # Token-info från API-svaret
                token_info = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }

                # Detaljerad token-loggning om aktiverad
                if VERBOSE_TOKEN_LOG:
                    log_token_usage(
                        filename=Path(pdf_path).name,
                        pdf_size=len(pdf_bytes),
                        prompt_len=len(prompt),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        full_extraction=full_extraction
                    )

                # Spara till Supabase
                save_period(company_id, result, pdf_hash, str(pdf_path))
                timer.step("Spara till Supabase")

                # Skriv ut tidsmätning
                timer.print_summary()

                if progress_callback:
                    progress_callback(pdf_path, "done", token_info)

                return result

            except Exception as e:
                last_error = e
                filename = Path(pdf_path).name
                if attempt < MAX_RETRIES - 1:
                    # Fråga användaren om retry
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


async def extract_all_pdfs(
    pdf_paths: list[str],
    company_name: str,
    on_progress: Callable[[str, str], None] | None = None,
    use_cache: bool = True,
    full_extraction: bool = False,
    skip_charts: bool = False,
    use_streaming: bool = False,
    model: str = "sonnet"
) -> tuple[list[dict], list[tuple[str, Exception]]]:
    """
    Extrahera data från alla PDFs parallellt.

    Args:
        pdf_paths: Lista med sökvägar till PDF-filer
        company_name: Bolagsnamn för datalagring
        on_progress: Callback för progress-uppdateringar
        use_cache: Om True, använd cachad data
        full_extraction: Om True, extrahera ALL data (sections + tables)
        skip_charts: Om True, extrahera inte grafer/diagram
        use_streaming: Om True, använd streaming API
        model: Vilken modell att använda ("sonnet" eller "haiku")

    Returns:
        Tuple av (lyckade resultat, misslyckade med fel)
    """
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
            return await extract_pdf(
                path, client, semaphore, company_id,
                on_progress, use_cache, full_extraction,
                skip_charts, use_streaming, model
            )
        except Exception as e:
            return (path, e)

    # Kör alla extraktioner parallellt
    results = await asyncio.gather(*[safe_extract(p) for p in pdf_paths])

    # Separera lyckade och misslyckade
    successful = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, tuple)]

    return successful, failed


def load_cached_extractions(company_name: str) -> list[dict]:
    """
    Ladda alla tidigare extraktioner för ett bolag från Supabase.
    """
    company = get_or_create_company(company_name)
    return load_all_periods(company["id"])
