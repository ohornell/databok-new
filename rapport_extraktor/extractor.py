"""
Async PDF-extraktion via Claude API.
Skalbar för 30+ PDF-filer med caching och progress-tracking.
"""

import asyncio
import base64
import json
import hashlib
import os
import re
from pathlib import Path
from typing import Callable

from anthropic import AsyncAnthropic

from prompts import EXTRACTION_PROMPT

# Konfiguration
MAX_CONCURRENT = 5  # Max samtidiga API-anrop (rate limit-vänlig)
MAX_RETRIES = 3     # Antal retry vid fel
CACHE_DIR = Path(".cache")


def get_cache_path(pdf_path: str) -> Path:
    """
    Generera unik cache-sökväg baserad på PDF-innehållets hash.
    Detta säkerställer att ändrade PDFs får ny cache.
    """
    pdf_bytes = Path(pdf_path).read_bytes()
    pdf_hash = hashlib.md5(pdf_bytes).hexdigest()[:12]
    return CACHE_DIR / f"{Path(pdf_path).stem}_{pdf_hash}.json"


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
    progress_callback: Callable[[str, str], None] | None = None,
    use_cache: bool = True
) -> dict:
    """
    Extrahera finansiell data från en PDF.

    Args:
        pdf_path: Sökväg till PDF-filen
        client: Anthropic async-klient
        semaphore: Begränsar antal samtidiga anrop
        progress_callback: Callback för progress (pdf_path, status)
        use_cache: Om True, använd cachad data om tillgänglig

    Returns:
        Dict med extraherad data
    """
    cache_path = get_cache_path(pdf_path)

    # Returnera cachad data om den finns
    if use_cache and cache_path.exists():
        if progress_callback:
            progress_callback(pdf_path, "cached")
        data = json.loads(cache_path.read_text())
        data["_source_file"] = str(pdf_path)
        return data

    async with semaphore:
        if progress_callback:
            progress_callback(pdf_path, "extracting")

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

                # Spara till cache
                CACHE_DIR.mkdir(exist_ok=True)
                cache_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2)
                )

                if progress_callback:
                    progress_callback(pdf_path, "done")

                return result

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    # Exponentiell backoff
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                else:
                    if progress_callback:
                        progress_callback(pdf_path, f"failed: {e}")
                    raise

        raise last_error  # type: ignore


async def extract_all_pdfs(
    pdf_paths: list[str],
    on_progress: Callable[[str, str], None] | None = None,
    use_cache: bool = True
) -> tuple[list[dict], list[tuple[str, Exception]]]:
    """
    Extrahera data från alla PDFs parallellt.

    Args:
        pdf_paths: Lista med sökvägar till PDF-filer
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
    client = AsyncAnthropic(api_key=api_key)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def safe_extract(path: str):
        """Wrapper som fångar fel istället för att krascha"""
        try:
            return await extract_pdf(path, client, semaphore, on_progress, use_cache)
        except Exception as e:
            return (path, e)

    # Kör alla extraktioner parallellt
    results = await asyncio.gather(*[safe_extract(p) for p in pdf_paths])

    # Separera lyckade och misslyckade
    successful = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, tuple)]

    return successful, failed


def load_cached_extractions() -> list[dict]:
    """
    Ladda alla tidigare cachade extraktioner.
    Användbart för --update läge.
    """
    if not CACHE_DIR.exists():
        return []

    cached = []
    for cache_file in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(cache_file.read_text())
            cached.append(data)
        except (json.JSONDecodeError, IOError):
            # Hoppa över korrupt cache
            pass

    return cached


def clear_cache():
    """Rensa all cachad data"""
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
