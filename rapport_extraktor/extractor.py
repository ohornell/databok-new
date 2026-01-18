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

from prompts import EXTRACTION_PROMPT
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
    use_cache: bool = True
) -> dict:
    """
    Extrahera finansiell data från en PDF.

    Args:
        pdf_path: Sökväg till PDF-filen
        client: Anthropic async-klient
        semaphore: Begränsar antal samtidiga anrop
        company_id: Bolagets UUID i Supabase
        progress_callback: Callback för progress (pdf_path, status, token_info)
        use_cache: Om True, använd cachad data om tillgänglig

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

        # Läs PDF som base64
        pdf_bytes = Path(pdf_path).read_bytes()
        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode()

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=8192,
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
                                "text": EXTRACTION_PROMPT
                            }
                        ]
                    }]
                )

                # Parsa JSON från svaret
                result = parse_json_response(response.content[0].text)
                result["_source_file"] = str(pdf_path)

                # Token-info från API-svaret
                token_info = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }

                # Spara till Supabase
                save_period(company_id, result, pdf_hash, str(pdf_path))

                if progress_callback:
                    progress_callback(pdf_path, "done", token_info)

                return result

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    # Exponentiell backoff
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                else:
                    if progress_callback:
                        progress_callback(pdf_path, f"failed: {e}", None)
                    raise

        raise last_error  # type: ignore


async def extract_all_pdfs(
    pdf_paths: list[str],
    company_name: str,
    on_progress: Callable[[str, str], None] | None = None,
    use_cache: bool = True
) -> tuple[list[dict], list[tuple[str, Exception]]]:
    """
    Extrahera data från alla PDFs parallellt.

    Args:
        pdf_paths: Lista med sökvägar till PDF-filer
        company_name: Bolagsnamn för datalagring
        on_progress: Callback för progress-uppdateringar
        use_cache: Om True, använd cachad data

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
            return await extract_pdf(path, client, semaphore, company_id, on_progress, use_cache)
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
