"""
Mistral AI-baserad PDF-extraktion för svenska kvartalsrapporter.

Använder en 2-stegs pipeline:
  1. OCR: mistral-ocr-latest extraherar text från PDF till markdown
  2. LLM: mistral-large-latest strukturerar data med Pydantic schema

Denna approach har ingen sidbegränsning (till skillnad från document_annotation_format
som är begränsad till 8 sidor).
"""

import asyncio
import json
import os
import re
import time
from functools import partial
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from mistralai import Mistral
from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel, Field

# Ladda .env-fil
load_dotenv()

from supabase_client import (
    save_period_atomic_async,
    update_period_status,
    get_pdf_hash,
    period_exists,
    load_period,
)
from extraction_log import process_extraction_complete
from logger import (
    get_logger,
    log_extraction_start,
    log_extraction_complete,
    log_api_request,
)

# Mistral Modell-IDs
MISTRAL_OCR = "mistral-ocr-latest"
MISTRAL_LLM = "mistral-large-latest"

# Konfiguration
MAX_RETRIES = 3

# Priser (USD)
OCR_PRICE_PER_PAGE = 0.001  # $0.001 per sida
MISTRAL_LARGE_INPUT_PRICE = 2.0 / 1_000_000  # $2 per 1M input tokens
MISTRAL_LARGE_OUTPUT_PRICE = 6.0 / 1_000_000  # $6 per 1M output tokens
USD_TO_SEK = 10.50


# ============================================
# PYDANTIC SCHEMAN FÖR STRUKTURERAD OUTPUT
# ============================================

class TableRow(BaseModel):
    """En rad i en finansiell tabell."""
    label: str = Field(..., description="Radens etikett/namn exakt som i dokumentet")
    values: list[float | int | None] = Field(..., description="Värden för varje kolumn. null för label-kolumnen och tomma celler. Antal måste matcha antal columns.")
    order: int = Field(..., description="Radordning (1, 2, 3...)")


class Table(BaseModel):
    """En finansiell tabell från kvartalsrapporten."""
    id: str = Field(..., description="Unikt ID för tabellen (t.ex. table_1)")
    title: str = Field(..., description="Tabellens rubrik")
    type: str = Field(..., description="Tabelltyp: income_statement, balance_sheet, cash_flow, kpi, quarterly, segment, note, other")
    page: int = Field(..., description="Sidnummer där tabellen finns")
    columns: list[str] = Field(..., description="Kolumnrubriker. Första kolumnen är ofta tom (för labels)")
    rows: list[TableRow] = Field(..., description="Alla rader i tabellen")


class Section(BaseModel):
    """En textsektion från kvartalsrapporten."""
    id: str = Field(..., description="Unikt ID för sektionen (t.ex. section_1)")
    title: str = Field(..., description="Sektionens rubrik")
    type: str = Field(..., description="Sektionstyp: narrative, summary, outlook, notes, other")
    page: int = Field(..., description="Sidnummer där sektionen börjar")
    content: str = Field(..., description="Full text för sektionen - förkorta INTE")


class Chart(BaseModel):
    """En graf/diagram från kvartalsrapporten."""
    id: str = Field(..., description="Unikt ID för grafen (t.ex. chart_1)")
    title: str = Field(..., description="Grafens rubrik")
    type: str = Field(..., description="Graftyp: bar, line, pie, other")
    page: int = Field(..., description="Sidnummer")
    description: str = Field(..., description="Beskrivning av vad grafen visar")


class BBoxChartAnnotation(BaseModel):
    """Schema för bbox annotation av grafer/bilder."""
    image_type: str = Field(..., description="Typ: chart, table, logo, photo, diagram, other")
    chart_type: str | None = Field(None, description="Om chart: bar, line, pie, area, other")
    title: str = Field(..., description="Rubrik eller bildtext")
    description: str = Field(..., description="Detaljerad beskrivning av innehållet")
    data_summary: str | None = Field(None, description="Sammanfattning av data som visas (trender, värden)")


class Metadata(BaseModel):
    """Metadata om kvartalsrapporten."""
    bolag: str = Field(..., description="Bolagsnamn")
    period: str = Field(..., description="Period (t.ex. Q4 2024)")
    valuta: str = Field(..., description="Valuta (t.ex. MSEK, KSEK)")
    sprak: str = Field(..., description="Språk: sv eller en")
    antal_sidor: int = Field(..., description="Antal sidor i dokumentet")


class QuarterlyReport(BaseModel):
    """Strukturerad data från en kvartalsrapport."""
    metadata: Metadata = Field(..., description="Metadata om rapporten")
    tables: list[Table] = Field(default_factory=list, description="Alla finansiella tabeller")
    sections: list[Section] = Field(default_factory=list, description="Alla textsektioner")
    charts: list[Chart] = Field(default_factory=list, description="Alla grafer/diagram")


# ============================================
# SYSTEM PROMPT FÖR EXTRAKTION
# ============================================

EXTRACTION_SYSTEM_PROMPT = """Du är en expert på finansiell analys av svenska kvartalsrapporter.

Din uppgift är att extrahera ALL strukturerad data från kvartalsrapporten.

TABELLTYPER att identifiera:
- income_statement (resultaträkning)
- balance_sheet (balansräkning)
- cash_flow (kassaflöde)
- kpi (nyckeltal)
- quarterly (kvartalssammanställning)
- segment (segmentdata)
- note (noter)
- other (övriga)

KRITISKT FÖR TABELLRADER:
- values: [null, tal1, tal2, ...] - ALLTID null först för label-kolumnen
- Antal values MÅSTE matcha antal columns exakt
- Negativa tal: "-373" eller "(373)" → -373
- Svenska tal: "1 234,56" → 1234.56
- Tomma celler → null

SEKTIONSTYPER:
- narrative (VD-kommentar, löpande text)
- summary (sammanfattning)
- outlook (framtidsutsikter)
- notes (noter)
- other (övriga)

VIKTIGT:
- Extrahera ALLA tabeller, inte bara de viktigaste
- Behåll EXAKT text från dokumentet i labels
- Förkorta INTE textsektioner
- Sidnummer ska vara korrekt för varje element"""


def calculate_cost(num_pages: int = 0, input_tokens: int = 0, output_tokens: int = 0) -> float:
    """Beräkna total kostnad i SEK."""
    ocr_cost_usd = num_pages * OCR_PRICE_PER_PAGE
    llm_input_cost_usd = input_tokens * MISTRAL_LARGE_INPUT_PRICE
    llm_output_cost_usd = output_tokens * MISTRAL_LARGE_OUTPUT_PRICE
    total_usd = ocr_cost_usd + llm_input_cost_usd + llm_output_cost_usd
    return total_usd * USD_TO_SEK


async def extract_pdf_multi_pass_mistral(
    pdf_path: str,
    client: Mistral,
    semaphore: asyncio.Semaphore,
    company_id: str,
    company_name: str = "",
    progress_callback: Callable[[str, str, dict | None], None] | None = None,
    use_cache: bool = True,
    base_folder: str | None = None,
    quiet: bool = False,
) -> dict:
    """
    Extraktion med Mistral 2-stegs pipeline:
    1. OCR: Extrahera text från PDF
    2. LLM: Strukturera data med Mistral Large
    """
    pdf_hash = get_pdf_hash(pdf_path)
    filename = Path(pdf_path).stem

    # Cache-kontroll
    if use_cache:
        period_match = re.search(r'[qQ](\d)[_-]?(\d{4})', filename)
        if period_match:
            quarter = int(period_match.group(1))
            year = int(period_match.group(2))
        else:
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

    # Hämta logger
    logger = get_logger('pipeline_mistral')
    log_extraction_start(pdf_path, company_name, "mistral-2-step")

    uploaded_file_id = None  # För cleanup efter OCR

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            extraction_start = time.perf_counter()
            loop = asyncio.get_event_loop()

            # === STEG 1: UPLOAD PDF TILL MISTRAL CLOUD ===
            if progress_callback:
                progress_callback(pdf_path, "ocr", None)

            logger.debug(f"[UPLOAD] Laddar upp {filename} till Mistral Cloud...")

            # Ladda upp PDF till Mistral Cloud
            # Läs hela filen först för att undvika problem med run_in_executor
            with open(pdf_path, "rb") as pdf_file:
                pdf_content = pdf_file.read()

            uploaded_file = await loop.run_in_executor(
                None,
                partial(
                    client.files.upload,
                    file={
                        "file_name": filename,
                        "content": pdf_content,
                    },
                    purpose="ocr"
                )
            )
            uploaded_file_id = uploaded_file.id

            # Hämta signed URL
            signed_url = await loop.run_in_executor(
                None,
                partial(client.files.get_signed_url, file_id=uploaded_file.id)
            )

            logger.debug("[OCR] Extraherar text med bbox annotations...")

            # === STEG 2: OCR MED SIGNED URL ===
            # Notera: document_annotation_format har 8-sidors begränsning
            # så vi använder endast bbox_annotation_format + Mistral Large
            bbox_format = response_format_from_pydantic_model(BBoxChartAnnotation)

            async with semaphore:
                ocr_response = await loop.run_in_executor(
                    None,
                    partial(
                        client.ocr.process,
                        model=MISTRAL_OCR,
                        document={
                            "type": "document_url",
                            "document_url": signed_url.url
                        },
                        include_image_base64=True,
                        bbox_annotation_format=bbox_format,
                        table_format="markdown",
                        extract_header=True,
                        extract_footer=True,
                    )
                )

            ocr_elapsed = time.perf_counter() - extraction_start

            # Hämta antal sidor och samla markdown
            ocr_pages = ocr_response.pages if hasattr(ocr_response, 'pages') else []
            num_pages = len(ocr_pages)

            # Samla graf-annotationer från alla sidor
            extracted_charts = []
            chart_counter = 0

            # Bygg markdown text från alla sidor
            pages_markdown = []
            for page in ocr_pages:
                page_num = page.index + 1 if hasattr(page, 'index') else len(pages_markdown) + 1

                # Bygg sidtext med header, content och footer
                page_parts = [f"--- Sida {page_num} ---"]

                # Header (om extraherad)
                if hasattr(page, 'header') and page.header:
                    page_parts.append(f"[HEADER]\n{page.header}")

                # Huvudinnehåll
                page_text = page.markdown if hasattr(page, 'markdown') else str(page)
                page_parts.append(page_text)

                # Tabeller (om separat extraherade)
                if hasattr(page, 'tables') and page.tables:
                    for i, table in enumerate(page.tables):
                        page_parts.append(f"[TABELL {i+1}]\n{table}")

                # Footer (om extraherad)
                if hasattr(page, 'footer') and page.footer:
                    page_parts.append(f"[FOOTER]\n{page.footer}")

                pages_markdown.append("\n".join(page_parts))

                # Extrahera bilder med annotationer
                if hasattr(page, 'images') and page.images:
                    for img in page.images:
                        # Korrekt fältnamn: image_annotation (JSON-sträng)
                        if hasattr(img, 'image_annotation') and img.image_annotation:
                            try:
                                # Parsa JSON-strängen till dict
                                ann = json.loads(img.image_annotation)
                                img_type = ann.get('image_type', 'other')
                                if img_type in ('chart', 'diagram', 'graph'):
                                    chart_counter += 1
                                    extracted_charts.append({
                                        "id": f"chart_{chart_counter}",
                                        "title": ann.get('title', ''),
                                        "type": ann.get('chart_type', 'other'),
                                        "page": page_num,
                                        "description": ann.get('description', ''),
                                        "data_summary": ann.get('data_summary')
                                    })
                            except json.JSONDecodeError:
                                pass  # Skippa ogiltig JSON

            full_text = "\n\n".join(pages_markdown)

            chart_info = f" | {len(extracted_charts)} grafer" if extracted_charts else ""
            logger.info(f"[OCR] Klar: {ocr_elapsed:.1f}s | {num_pages} sidor | {len(full_text)} tecken{chart_info}")
            log_api_request(MISTRAL_OCR, "ocr", 0, 0)

            # === STEG 3: LLM STRUKTURERING ===
            if progress_callback:
                progress_callback(pdf_path, "llm", None)

            logger.debug(f"[LLM] Strukturerar med {MISTRAL_LLM}...")

            llm_start = time.perf_counter()

            # Kör synkront LLM-anrop i executor för att inte blockera event loop
            async with semaphore:
                llm_response = await loop.run_in_executor(
                    None,
                    partial(
                        client.chat.parse,
                        model=MISTRAL_LLM,
                        messages=[
                            {
                                "role": "system",
                                "content": EXTRACTION_SYSTEM_PROMPT
                            },
                            {
                                "role": "user",
                                "content": f"Extrahera all finansiell data från denna kvartalsrapport:\n\n{full_text}"
                            }
                        ],
                        response_format=QuarterlyReport,
                        temperature=0
                    )
                )

            llm_elapsed = time.perf_counter() - llm_start

            # Hämta tokens för kostnadsberäkning
            usage = llm_response.usage if hasattr(llm_response, 'usage') else None
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            logger.info(f"[LLM] Klar: {llm_elapsed:.1f}s | {input_tokens} in / {output_tokens} ut tokens")
            log_api_request(MISTRAL_LLM, "structure", input_tokens, output_tokens)

            # Hämta strukturerad output
            parsed_result = llm_response.choices[0].message.parsed
            if parsed_result is None:
                # Fallback till raw content
                raw_content = llm_response.choices[0].message.content
                raise ValueError(f"Ingen parsed output. Raw: {raw_content[:500]}")

            # Konvertera till dict
            result = parsed_result.model_dump()

            total_elapsed = time.perf_counter() - extraction_start

            # Kostnad
            cost = calculate_cost(num_pages, input_tokens, output_tokens)

            # Extrahera data från resultat
            tables = result.get("tables", [])
            sections = result.get("sections", [])
            # Använd grafer från bbox annotation (OCR-steget), inte LLM
            charts = extracted_charts if extracted_charts else result.get("charts", [])

            table_count = len(tables)
            section_count = len(sections)
            chart_count = len(charts)

            logger.info(f"[RESULTAT] Tabeller: {table_count} | Sektioner: {section_count} | Grafer: {chart_count}")

            # Hämta metadata
            metadata = result.get("metadata", {})

            # Output-format kompatibelt med Claude-pipelinen
            output = {
                "metadata": metadata,
                "tables": tables,
                "sections": sections,
                "charts": charts,
                "_source_file": str(pdf_path),
                "_pipeline_info": {
                    "pipeline": "mistral-2-step",
                    "passes": [
                        {
                            "pass": 1,
                            "model": MISTRAL_OCR,
                            "type": "ocr",
                            "pages": num_pages,
                            "elapsed_seconds": round(ocr_elapsed, 2),
                        },
                        {
                            "pass": 2,
                            "model": MISTRAL_LLM,
                            "type": "structure",
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "elapsed_seconds": round(llm_elapsed, 2),
                        }
                    ],
                    "total_cost_sek": round(cost, 4),
                    "total_elapsed_seconds": round(total_elapsed, 2),
                }
            }

            # Sammanfattning
            log_extraction_complete(pdf_path, table_count, section_count, chart_count, cost, total_elapsed)

            # Ingen validering i Mistral-pipelinen (saknar Pass 1-struktur)
            all_errors = []
            final_status = "success"

            # Spara till Supabase
            period_id, section_ids = await save_period_atomic_async(company_id, output, pdf_hash, str(pdf_path))

            # Generera embeddings
            embeddings_count = 0
            if section_ids:
                try:
                    from supabase_client import generate_embeddings_for_sections_async
                    embeddings_count = await generate_embeddings_for_sections_async(section_ids)
                    if embeddings_count > 0:
                        logger.info(f"[EMBEDDING] {embeddings_count}/{len(section_ids)} sektioner fick embeddings")
                except Exception as emb_err:
                    logger.warning(f"[EMBEDDING] Kunde inte generera embeddings: {emb_err}")

            # Uppdatera slutstatus
            update_period_status(
                period_id,
                status=final_status,
                errors=all_errors if all_errors else None,
                embeddings_count=embeddings_count
            )

            # Flytta fil och uppdatera logg
            if base_folder:
                try:
                    process_extraction_complete(pdf_path, company_name, base_folder)
                    logger.info(f"[FIL] PDF flyttad till ligger_i_databasen/")
                except Exception as move_err:
                    logger.warning(f"[FIL] Kunde inte flytta PDF: {move_err}")

            if progress_callback:
                progress_callback(pdf_path, "done", {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_sek": cost,
                })

            # Cleanup: Ta bort filen från Mistral Cloud
            if uploaded_file_id:
                try:
                    await loop.run_in_executor(
                        None,
                        partial(client.files.delete, file_id=uploaded_file_id)
                    )
                except Exception:
                    pass  # Ignorera cleanup-fel

            return output

        except Exception as e:
            last_error = e

            # Cleanup vid fel
            if uploaded_file_id:
                try:
                    await loop.run_in_executor(
                        None,
                        partial(client.files.delete, file_id=uploaded_file_id)
                    )
                except Exception:
                    pass

            if attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt
                logger.warning(f"[RETRY] Fel vid extraktion av {filename}: {type(e).__name__}: {e}")
                logger.warning(f"[RETRY] Försök {attempt + 1}/{MAX_RETRIES}, väntar {wait_time}s...")
                uploaded_file_id = None  # Reset för retry
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"[FEL] Extraktion misslyckades efter {MAX_RETRIES} försök: {e}")
                if progress_callback:
                    progress_callback(pdf_path, f"failed: {e}", None)
                raise

    raise last_error  # type: ignore


def get_mistral_client() -> Mistral:
    """Skapa Mistral-klient med API-nyckel från miljövariabler."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError(
            "MISTRAL_API_KEY saknas. "
            "Exportera den med: export MISTRAL_API_KEY='din-nyckel'"
        )
    return Mistral(api_key=api_key)
