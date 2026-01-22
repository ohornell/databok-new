"""
Mistral AI-baserad PDF-extraktion v2 - Sidvis bearbetning med Annotations.

Använder en optimerad pipeline som kringgår 8-sidorsbegränsningen:
  1. PDF delas upp i enskilda sidor med PyMuPDF
  2. Varje sida bearbetas med OCR + document_annotation/bbox_annotation
  3. Resultat sammanfogas och lagras i Supabase

Fördelar jämfört med v1:
  - Kringgår 8-sidorsbegränsningen för document_annotation
  - Ingen separat LLM-steg behövs
  - Snabbare: ~10s istället för 3-6 min
"""

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
from functools import partial
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF
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
    slugify,
)

# Mistral Modell-IDs
MISTRAL_OCR = "mistral-ocr-latest"

# Konfiguration
MAX_RETRIES = 3
MAX_PARALLEL_PAGES = 5  # Max antal sidor att bearbeta parallellt

# Priser (USD)
OCR_PRICE_PER_PAGE = 0.001  # $0.001 per sida
USD_TO_SEK = 10.50

# Miljöbaserad lagring
STORAGE_MODE = os.getenv("STORAGE_MODE", "local")  # "local" eller "cloud"


# ============================================
# PYDANTIC SCHEMAN FÖR ANNOTATIONS
# ============================================

class DocumentAnnotation(BaseModel):
    """Schema för document_annotation - metadata om hela dokumentet."""
    document_type: str = Field(
        default="quarterly_report",
        description="Dokumenttyp: quarterly_report, annual_report, interim_report"
    )
    company_name: str = Field(..., description="Bolagets fullständiga namn")
    period: str = Field(..., description="Rapportperiod, t.ex. 'Q3 2025' eller 'Q1 2024'")
    currency: str = Field(
        default="SEK",
        description="Valuta för finansiella siffror: SEK, MSEK, KSEK, NOK, EUR, USD"
    )
    language: str = Field(
        default="sv",
        description="Dokumentspråk: sv, en, no"
    )


class BBoxAnnotation(BaseModel):
    """Schema för bbox_annotation - klassificering av bilder/grafer."""
    element_type: str = Field(
        ...,
        description="Typ av element: chart, diagram, logo, photo, table_image, other"
    )
    chart_type: str | None = Field(
        None,
        description="Om element_type=chart: bar, line, pie, area, waterfall, other"
    )
    title: str = Field(..., description="Rubrik eller beskrivning av elementet")
    description: str = Field(..., description="Detaljerad beskrivning av innehållet")
    data_summary: str | None = Field(
        None,
        description="Sammanfattning av data som visas (trender, nyckeltal)"
    )


# ============================================
# TABELLKLASSIFICERING
# ============================================

# Nyckelord för att identifiera tabelltyper
TABLE_TYPE_KEYWORDS = {
    "income_statement": [
        "resultaträkning", "income statement", "profit and loss", "p&l",
        "revenue", "intäkter", "nettoomsättning", "net sales", "operating profit",
        "rörelseresultat", "ebit", "ebitda", "net profit", "nettoresultat",
        "cost of goods", "kostnad för sålda", "gross profit", "bruttovinst"
    ],
    "balance_sheet": [
        "balansräkning", "balance sheet", "financial position", "finansiell ställning",
        "assets", "tillgångar", "liabilities", "skulder", "equity", "eget kapital",
        "current assets", "omsättningstillgångar", "fixed assets", "anläggningstillgångar"
    ],
    "cash_flow": [
        "kassaflöde", "cash flow", "cash and cash equivalents", "likvida medel",
        "operating activities", "operativ verksamhet", "investing activities",
        "investeringsverksamhet", "financing activities", "finansieringsverksamhet"
    ],
    "kpi": [
        "nyckeltal", "key figures", "key metrics", "kpi", "highlights",
        "sammanfattning", "summary", "overview"
    ],
    "segment": [
        "segment", "division", "region", "geography", "affärsområde",
        "business area", "by region", "per segment"
    ],
    "growth": [
        "tillväxt", "growth", "förändring", "change", "development",
        "utveckling", "yoy", "year-over-year", "organic growth"
    ],
}


def classify_table_type(title: str, columns: list[str], rows: list[dict]) -> str:
    """
    Klassificera tabelltyp baserat på titel, kolumner och innehåll.

    Args:
        title: Tabellens titel
        columns: Lista med kolumnrubriker
        rows: Lista med rader

    Returns:
        Tabelltyp: income_statement, balance_sheet, cash_flow, kpi, segment, growth, other
    """
    # Samla all text för sökning
    search_text = title.lower()
    search_text += " " + " ".join(c.lower() for c in columns if c)

    # Lägg till rad-labels
    for row in rows[:10]:  # Kolla första 10 raderna
        label = row.get("label", "")
        if label:
            search_text += " " + label.lower()

    # Sök efter nyckelord
    scores = {}
    for table_type, keywords in TABLE_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in search_text)
        if score > 0:
            scores[table_type] = score

    # Returnera typen med högst poäng
    if scores:
        return max(scores, key=scores.get)

    return "other"


# ============================================
# SEKTIONSEXTRAKTION FRÅN MARKDOWN
# ============================================

# Nyckelord för att identifiera sektionstyper
SECTION_TYPE_KEYWORDS = {
    "narrative": [
        "vd har ordet", "ceo comment", "vd-kommentar", "ceo letter",
        "kommentar från vd", "message from ceo", "ord från vd"
    ],
    "summary": [
        "sammanfattning", "summary", "highlights", "overview",
        "i korthet", "in brief", "nyckeltal", "key figures"
    ],
    "outlook": [
        "utsikter", "outlook", "framtid", "future", "prognos",
        "forecast", "guidance", "framåtblick"
    ],
    "risk": [
        "risk", "osäkerhet", "uncertainty", "riskfaktorer"
    ],
}


def classify_section_type(title: str, content: str) -> str:
    """
    Klassificera sektionstyp baserat på titel och innehåll.
    """
    search_text = (title + " " + content[:500]).lower()

    for section_type, keywords in SECTION_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in search_text:
                return section_type

    return "other"


def extract_sections_from_markdown(markdown_pages: list[dict]) -> list[dict]:
    """
    Extrahera textsektioner från markdown-innehåll.

    Identifierar:
    - Rubriker (# ## ###)
    - Stycken med sammanhängande text
    - VD-kommentarer och andra narrativa sektioner

    Args:
        markdown_pages: Lista med {page_num, markdown} dicts

    Returns:
        Lista med sektioner {title, content, section_type, page}
    """
    import re

    sections = []
    current_section = None

    for page_data in markdown_pages:
        page_num = page_data.get("page_num", 0)
        markdown = page_data.get("markdown", "")

        if not markdown:
            continue

        lines = markdown.split("\n")

        for line in lines:
            line = line.strip()

            # Detektera rubriker
            header_match = re.match(r'^(#{1,3})\s+(.+)$', line)

            if header_match:
                # Spara föregående sektion om den har innehåll
                if current_section and current_section.get("content", "").strip():
                    content = current_section["content"].strip()
                    # Filtrera bort korta sektioner (mindre än 100 tecken)
                    if len(content) >= 100:
                        current_section["section_type"] = classify_section_type(
                            current_section["title"], content
                        )
                        sections.append(current_section)

                # Starta ny sektion
                level = len(header_match.group(1))
                title = header_match.group(2).strip()

                current_section = {
                    "title": title,
                    "content": "",
                    "page": page_num,
                    "header_level": level,
                }

            elif current_section is not None:
                # Lägg till text till nuvarande sektion
                # Skippa rader som ser ut som tabeller
                if not line.startswith("|") and not re.match(r'^[-|:\s]+$', line):
                    if line:
                        current_section["content"] += line + "\n"

            elif line and not line.startswith("|") and len(line) > 50:
                # Text utan rubrik - skapa anonym sektion
                # Endast om det är en längre rad (inte korta labels)
                current_section = {
                    "title": f"Text från sida {page_num}",
                    "content": line + "\n",
                    "page": page_num,
                    "header_level": 0,
                }

    # Spara sista sektionen
    if current_section and current_section.get("content", "").strip():
        content = current_section["content"].strip()
        if len(content) >= 100:
            current_section["section_type"] = classify_section_type(
                current_section["title"], content
            )
            sections.append(current_section)

    # Rensa upp och ta bort header_level
    for section in sections:
        section.pop("header_level", None)
        section["content"] = section["content"].strip()

    return sections


# ============================================
# MARKDOWN-TABELL PARSNING
# ============================================

def parse_markdown_table(markdown: str) -> dict:
    """
    Parsa en markdown-tabell till strukturerat format.

    Args:
        markdown: Markdown-tabell som sträng

    Returns:
        Dict med columns och rows
    """
    lines = [line.strip() for line in markdown.strip().split('\n') if line.strip()]

    if not lines:
        return {"columns": [], "rows": []}

    # Första raden är headers
    headers = []
    if lines:
        header_line = lines[0]
        headers = [cell.strip() for cell in header_line.split('|') if cell.strip()]

    # Skippa separator-raden (|---|---|...)
    data_lines = []
    for line in lines[1:]:
        # Skippa rader som bara innehåller --- (separator)
        if all(c in '-| ' for c in line):
            continue
        data_lines.append(line)

    # Parsa datarader
    rows = []
    for i, line in enumerate(data_lines):
        cells = [cell.strip() for cell in line.split('|') if cell.strip()]
        if cells:
            # Skapa rad med label och values
            rows.append({
                "label": cells[0] if cells else "",
                "values": cells[1:] if len(cells) > 1 else [],
                "order": i + 1,
            })

    return {
        "columns": headers,
        "rows": rows,
    }


# ============================================
# PDF-UPPDELNING MED PYMUPDF
# ============================================

def dela_upp_pdf(pdf_path: str, output_dir: str) -> list[str]:
    """
    Dela upp en PDF i ensidiga PDF-filer.

    Args:
        pdf_path: Sökväg till original-PDF
        output_dir: Mapp där ensidiga PDFs sparas

    Returns:
        Lista med sökvägar till ensidiga PDFs
    """
    doc = fitz.open(pdf_path)
    sidor = []

    for i in range(len(doc)):
        ny_pdf = fitz.open()
        ny_pdf.insert_pdf(doc, from_page=i, to_page=i)
        sida_path = os.path.join(output_dir, f"sida_{i+1:03d}.pdf")
        ny_pdf.save(sida_path)
        ny_pdf.close()
        sidor.append(sida_path)

    doc.close()
    return sidor


# ============================================
# OCR MED ANNOTATIONS PER SIDA
# ============================================

async def extrahera_sida(
    sida_path: str,
    page_num: int,
    client: Mistral,
    is_first_page: bool,
    document_schema,
    bbox_schema,
    quiet: bool = False,
) -> dict:
    """
    Extrahera data från en enskild sida med OCR och annotations.

    Args:
        sida_path: Sökväg till ensidig PDF
        page_num: Sidnummer (1-indexerat)
        client: Mistral-klient
        is_first_page: Om detta är första sidan (för document_annotation)
        document_schema: Schema för document_annotation
        bbox_schema: Schema för bbox_annotation
        quiet: Undertryck utskrifter

    Returns:
        Dict med extraherad data från sidan
    """
    loop = asyncio.get_event_loop()

    # Läs PDF-innehåll
    with open(sida_path, "rb") as f:
        pdf_content = f.read()

    filename = os.path.basename(sida_path)

    # Upload till Mistral Cloud
    uploaded_file = await loop.run_in_executor(
        None,
        partial(
            client.files.upload,
            file={"file_name": filename, "content": pdf_content},
            purpose="ocr"
        )
    )

    try:
        # Hämta signed URL
        signed_url = await loop.run_in_executor(
            None,
            partial(client.files.get_signed_url, file_id=uploaded_file.id)
        )

        # Bygg OCR-request
        ocr_kwargs = {
            "model": MISTRAL_OCR,
            "document": {"type": "document_url", "document_url": signed_url.url},
            "include_image_base64": True,
            "bbox_annotation_format": bbox_schema,
            "table_format": "markdown",
        }

        # Lägg till document_annotation endast för första sidan
        if is_first_page:
            ocr_kwargs["document_annotation_format"] = document_schema

        # Kör OCR
        ocr_response = await loop.run_in_executor(
            None,
            partial(client.ocr.process, **ocr_kwargs)
        )

        # Extrahera resultat
        result = {
            "page_num": page_num,
            "markdown": "",
            "tables": [],
            "charts": [],
            "document_annotation": None,
        }

        # Hämta siddata (bör bara vara en sida)
        if hasattr(ocr_response, 'pages') and ocr_response.pages:
            page = ocr_response.pages[0]

            # Markdown-text
            result["markdown"] = page.markdown if hasattr(page, 'markdown') else ""

            # Tabeller
            if hasattr(page, 'tables') and page.tables:
                for i, table in enumerate(page.tables):
                    markdown_content = table.content if hasattr(table, 'content') else str(table)
                    parsed = parse_markdown_table(markdown_content)

                    # Försök extrahera titel från första raden eller använd ID
                    title = ""
                    if parsed["columns"]:
                        # Använd första kolumnhuvudet om det finns
                        title = parsed["columns"][0] if parsed["columns"][0] else f"Tabell sida {page_num}"

                    # Klassificera tabelltyp
                    table_type = classify_table_type(title, parsed["columns"], parsed["rows"])

                    result["tables"].append({
                        "id": f"table_p{page_num}_{i+1}",
                        "title": title,
                        "page": page_num,
                        "type": table_type,
                        "columns": parsed["columns"],
                        "rows": parsed["rows"],
                        "markdown_content": markdown_content,  # Behåll original
                    })

            # Bilder/grafer med annotations
            if hasattr(page, 'images') and page.images:
                for i, img in enumerate(page.images):
                    if hasattr(img, 'image_annotation') and img.image_annotation:
                        try:
                            ann = json.loads(img.image_annotation)
                            if ann.get('element_type') in ('chart', 'diagram'):
                                result["charts"].append({
                                    "id": f"chart_p{page_num}_{i+1}",
                                    "page": page_num,
                                    "element_type": ann.get('element_type'),
                                    "chart_type": ann.get('chart_type', 'other'),
                                    "title": ann.get('title', ''),
                                    "description": ann.get('description', ''),
                                    "data_summary": ann.get('data_summary'),
                                    "image_base64": img.image_base64 if hasattr(img, 'image_base64') else None,
                                })
                        except json.JSONDecodeError:
                            pass

        # Document annotation (endast första sidan)
        if is_first_page and hasattr(ocr_response, 'document_annotation'):
            if ocr_response.document_annotation:
                try:
                    result["document_annotation"] = json.loads(ocr_response.document_annotation)
                except json.JSONDecodeError:
                    result["document_annotation"] = None

        return result

    finally:
        # Cleanup: Ta bort filen från Mistral Cloud
        try:
            await loop.run_in_executor(
                None,
                partial(client.files.delete, file_id=uploaded_file.id)
            )
        except Exception:
            pass


# ============================================
# PARALLELL BEARBETNING AV ALLA SIDOR
# ============================================

async def bearbeta_alla_sidor(
    sidor: list[str],
    client: Mistral,
    document_schema,
    bbox_schema,
    progress_callback: Callable | None = None,
    quiet: bool = False,
) -> list[dict]:
    """
    Bearbeta alla sidor parallellt med semaphore för rate limiting.

    Args:
        sidor: Lista med sökvägar till ensidiga PDFs
        client: Mistral-klient
        document_schema: Schema för document_annotation
        bbox_schema: Schema för bbox_annotation
        progress_callback: Callback för progress-uppdateringar
        quiet: Undertryck utskrifter

    Returns:
        Lista med resultat från varje sida (i sidordning)
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL_PAGES)

    async def bearbeta_med_semaphore(sida_path: str, page_num: int):
        async with semaphore:
            return await extrahera_sida(
                sida_path=sida_path,
                page_num=page_num,
                client=client,
                is_first_page=(page_num == 1),
                document_schema=document_schema,
                bbox_schema=bbox_schema,
                quiet=quiet,
            )

    # Skapa tasks för alla sidor
    tasks = [
        bearbeta_med_semaphore(sida_path, i + 1)
        for i, sida_path in enumerate(sidor)
    ]

    # Kör parallellt
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filtrera bort exceptions och logga fel
    valid_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            if not quiet:
                print(f"   [FEL] Sida {i+1}: {result}")
        else:
            valid_results.append(result)

    # Sortera efter sidnummer
    valid_results.sort(key=lambda x: x.get("page_num", 0))

    return valid_results


# ============================================
# NORMALISERA TABELLSTRUKTUR
# ============================================

def is_period_header(value: str) -> bool:
    """
    Detektera om ett värde ser ut som en period-header.
    Skalbar lösning som hanterar många format.

    Stödda format:
    - Kvartal: Q1 2024, Q3 24, 1Q24, 3Q 2024, Kv1 2024
    - Halvår: H1 2024, 1H 2024, H1'24
    - Helår: 2024, FY2024, FY 2024, Full year 2024
    - YTD: YTD 2024, 9M 2024, 9M24
    - LTM/TTM: LTM, TTM, LTM Q3 2024
    - Månader: Jan-Mar 2024, January-March 2024, Jul-Sep 2024
    - Nordiska: Kv1 2024, Kv 1 2024
    """
    import re

    if not value:
        return False

    v = str(value).strip()

    # Lista med mönster (ordnade efter specificitet)
    patterns = [
        # Kvartal: Q1 2024, Q3 24, Q1'24, Q1-24
        r'^Q[1-4]\s*[\'/-]?\s*\d{2,4}$',
        # Kvartal omvänt: 1Q24, 3Q 2024, 1Q'24
        r'^[1-4]Q\s*[\'/-]?\s*\d{2,4}$',
        # Halvår: H1 2024, H2 24, 1H 2024, 1H24
        r'^[12]?H[12]?\s*[\'/-]?\s*\d{2,4}$',
        # YTD med månad: 9M 2024, 9M24, 3M 2024
        r'^\d{1,2}M\s*[\'/-]?\s*\d{2,4}$',
        # YTD: YTD 2024, YTD24, YTD Q3 2024
        r'^YTD\s*[Q]?\d*\s*\d{2,4}$',
        # LTM/TTM: LTM, TTM, LTM 2024, LTM Q3 2024
        r'^[LT]TM(\s+Q?[1-4]?\s*\d{0,4})?$',
        # Helår: FY2024, FY 2024, FY24
        r'^FY\s*\d{2,4}$',
        # Helår text: Full year 2024, Helår 2024
        r'^(Full\s*year|Helår|Hele\s*året)\s+\d{4}$',
        # Bara årtal: 2020-2030
        r'^20[2-3]\d$',
        # Månad-intervall engelska: Jan-Mar 2024, January-March 2024
        r'^[A-Za-z]{3,9}[\s-]+[A-Za-z]{3,9}\s+\d{4}$',
        # Månad-intervall svenska/norska: Jan-mars 2024, Juli-september 2024
        r'^[A-Za-zåäöÅÄÖ]{3,10}[\s-]+[A-Za-zåäöÅÄÖ]{3,10}\s+\d{4}$',
        # Nordiska kvartal: Kv1 2024, Kv 1 2024, Kvartal 1 2024
        r'^Kv(artal)?\s*[1-4]\s+\d{4}$',
    ]

    for pattern in patterns:
        if re.match(pattern, v, re.IGNORECASE):
            return True

    return False


def normalize_table_structure(table: dict) -> dict:
    """
    Normalisera tabellstruktur där perioder ligger i första radens values
    istället för i columns.

    Problem: OCR extraherar ibland tabeller så här:
        columns = ['Income statement']
        rows[0] = {"label": "NOKm", "values": ["Q3 2023", "Q4 2023", ...]}

    Detta fixar till:
        columns = ['NOKm', 'Q3 2023', 'Q4 2023', ...]
        rows = [{"label": "Revenues", "values": ["356", "545", ...]}, ...]

    Returns:
        Normaliserad tabell
    """
    columns = table.get("columns", [])
    rows = table.get("rows", [])

    # Kolla om första raden innehåller period-headers
    if not rows:
        return table

    first_row = rows[0]
    first_values = first_row.get("values", [])

    if not first_values:
        return table

    # Räkna hur många values som ser ut som perioder
    period_matches = sum(1 for v in first_values if is_period_header(v))

    # Om minst 3 värden ELLER >50% av värdena matchar period-mönster
    min_matches = min(3, len(first_values) // 2 + 1)
    if period_matches >= min_matches:
        # Bygg nya columns från label + values i första raden
        new_columns = [first_row.get("label", "")] + list(first_values)

        # Ta bort första raden från rows
        new_rows = rows[1:]

        return {
            **table,
            "columns": new_columns,
            "rows": new_rows,
        }

    return table


# ============================================
# SAMMANFOGA RESULTAT FRÅN ALLA SIDOR
# ============================================

def sammanfoga_resultat(alla_resultat: list[dict]) -> dict:
    """
    Sammanfoga resultat från alla sidor till en enhetlig struktur.

    Args:
        alla_resultat: Lista med resultat från varje sida

    Returns:
        Sammanfogad dict med all data
    """
    if not alla_resultat:
        return {
            "metadata": {},
            "tables": [],
            "sections": [],
            "charts": [],
            "full_text": "",
        }

    # Hämta document_annotation från första sidan
    doc_ann = alla_resultat[0].get("document_annotation", {}) or {}

    # Bygg metadata
    metadata = {
        "bolag": doc_ann.get("company_name", "Okänt"),
        "period": doc_ann.get("period", ""),
        "valuta": doc_ann.get("currency", "SEK"),
        "sprak": doc_ann.get("language", "sv"),
        "antal_sidor": len(alla_resultat),
    }

    # Samla all text
    full_text_parts = []
    for result in alla_resultat:
        page_num = result.get("page_num", 0)
        markdown = result.get("markdown", "")
        if markdown:
            full_text_parts.append(f"--- Sida {page_num} ---\n{markdown}")

    # Samla alla tabeller och normalisera strukturen
    tables = []
    for result in alla_resultat:
        for table in result.get("tables", []):
            # Normalisera tabeller där perioder ligger i första radens values
            normalized_table = normalize_table_structure(table)
            tables.append(normalized_table)

    # Samla alla grafer
    charts = []
    for result in alla_resultat:
        for chart in result.get("charts", []):
            charts.append(chart)

    # Extrahera sektioner från markdown
    markdown_pages = [
        {"page_num": r.get("page_num", 0), "markdown": r.get("markdown", "")}
        for r in alla_resultat
    ]
    sections = extract_sections_from_markdown(markdown_pages)

    return {
        "metadata": metadata,
        "tables": tables,
        "sections": sections,
        "charts": charts,
        "full_text": "\n\n".join(full_text_parts),
    }


# ============================================
# PIXTRAL GRAF-DATAEXTRAKTION
# ============================================

# Pixtral modell för bildanalys
PIXTRAL_MODEL = "pixtral-12b-2409"  # Eller "pixtral-large-latest" för bättre precision
MAX_PARALLEL_PIXTRAL = 3  # Max parallella Pixtral-anrop


async def extract_chart_data_with_pixtral(
    client: Mistral,
    chart: dict,
    semaphore: asyncio.Semaphore,
    quiet: bool = False,
) -> dict:
    """
    Använd Pixtral för att extrahera datapunkter från en grafbild.

    Args:
        client: Mistral-klient
        chart: Dict med chart data inkl. image_base64
        semaphore: Semaphore för rate limiting
        quiet: Undertryck utskrifter

    Returns:
        chart dict med populerat data_points
    """
    image_base64 = chart.get("image_base64", "")
    if not image_base64:
        return chart

    # Formatera base64 för API
    if not image_base64.startswith("data:"):
        image_base64 = f"data:image/png;base64,{image_base64}"

    chart_type = chart.get("chart_type", "bar")
    title = chart.get("title", "")

    # Prompt anpassad för grafer
    system_prompt = """Du är en expert på att extrahera data från finansiella grafer.
Analysera grafbilden och returnera strukturerad JSON med datapunkter.

Returnera ENDAST giltig JSON i detta format:
{
  "data_points": [
    {"label": "Q1 2024", "value": 850},
    {"label": "Q2 2024", "value": 920}
  ],
  "x_axis": "Kvartal",
  "y_axis": "MSEK",
  "estimated": true
}

Regler:
- Extrahera ALLA synliga datapunkter
- Använd exakta värden om synliga, annars uppskatta från axeln
- Sätt estimated=true om värden är uppskattade
- För cirkeldiagram: label=kategori, value=procent
- För stapeldiagram: label=x-axel etikett, value=stapelhöjd
- För linjediagram: label=x-axel punkt, value=y-värde"""

    user_prompt = f"""Analysera denna {chart_type}-graf med titeln "{title}".
Extrahera alla datapunkter du kan se i grafen."""

    loop = asyncio.get_event_loop()

    async with semaphore:
        try:
            response = await loop.run_in_executor(
                None,
                partial(
                    client.chat.complete,
                    model=PIXTRAL_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_prompt},
                                {"type": "image_url", "image_url": image_base64}
                            ]
                        }
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=1000,
                )
            )

            # Parsa JSON-svar
            result = json.loads(response.choices[0].message.content)

            # Uppdatera chart med extraherade data
            chart["data_points"] = result.get("data_points", [])
            chart["x_axis"] = result.get("x_axis")
            chart["y_axis"] = result.get("y_axis")
            chart["estimated"] = result.get("estimated", True)

        except Exception as e:
            if not quiet:
                print(f"   [VARNING] Pixtral kunde inte analysera graf '{title}': {e}")
            # Behåll tom data_points vid fel
            chart["data_points"] = []

    return chart


async def analyze_charts_with_pixtral(
    client: Mistral,
    charts: list[dict],
    quiet: bool = False,
) -> list[dict]:
    """
    Analysera alla grafer med Pixtral för att extrahera datapunkter.

    Args:
        client: Mistral-klient
        charts: Lista med grafer (med image_base64)
        quiet: Undertryck utskrifter

    Returns:
        Lista med grafer med populerade data_points
    """
    if not charts:
        return charts

    if not quiet:
        print(f"   [PIXTRAL] Analyserar {len(charts)} grafer för datapunkter...", flush=True)

    semaphore = asyncio.Semaphore(MAX_PARALLEL_PIXTRAL)

    # Analysera alla grafer parallellt
    analyzed_charts = await asyncio.gather(*[
        extract_chart_data_with_pixtral(client, chart, semaphore, quiet)
        for chart in charts
    ])

    # Räkna lyckade extraktioner
    successful = sum(1 for c in analyzed_charts if c.get("data_points"))
    if not quiet:
        print(f"   [PIXTRAL] {successful}/{len(charts)} grafer fick datapunkter", flush=True)

    return list(analyzed_charts)


# ============================================
# GRAFLAGRING (MILJÖBASERAD)
# ============================================

def spara_graf(
    chart: dict,
    company_slug: str,
    period: str,
    period_id: str,
    base_folder: str = "alla_rapporter",
    supabase_client=None,
) -> str | None:
    """
    Spara grafbild och returnera sökväg/URL.

    Args:
        chart: Dict med grafdata inkl. image_base64
        company_slug: Bolagets slug
        period: Perioden, t.ex. "Q3 2025"
        period_id: Period-ID i databasen
        base_folder: Basmapp för lokal lagring
        supabase_client: Supabase-klient för molnlagring

    Returns:
        Sökväg (lokal) eller URL (cloud), eller None om ingen bild
    """
    graf_base64 = chart.get("image_base64", "")
    if not graf_base64:
        return None

    # Extrahera base64-data (ta bort eventuell header)
    if "," in graf_base64:
        graf_data = graf_base64.split(",")[1]
    else:
        graf_data = graf_base64

    try:
        binary_data = base64.b64decode(graf_data)
    except Exception:
        return None

    page_num = chart.get("page", 0)
    chart_id = chart.get("id", "unknown")
    filnamn = f"graf_sida_{page_num}_{chart_id}.png"

    if STORAGE_MODE == "local":
        # === LOKAL LAGRING ===
        graf_mapp = Path(base_folder) / company_slug / "grafer" / period.replace(" ", "_")
        graf_mapp.mkdir(parents=True, exist_ok=True)
        fil_path = graf_mapp / filnamn

        with open(fil_path, "wb") as f:
            f.write(binary_data)

        return str(fil_path)

    else:
        # === SUPABASE STORAGE ===
        if supabase_client is None:
            return None

        storage_path = f"grafer/{period_id}/{filnamn}"
        try:
            supabase_client.storage.from_("rapporter").upload(
                storage_path,
                binary_data,
                {"content-type": "image/png"}
            )
            return supabase_client.storage.from_("rapporter").get_public_url(storage_path)
        except Exception:
            return None


# ============================================
# HUVUDFUNKTION FÖR EXTRAKTION
# ============================================

async def extract_pdf_mistral_v2(
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
    Extrahera data från PDF med sidvis bearbetning och annotations.

    Args:
        pdf_path: Sökväg till PDF
        client: Mistral-klient
        semaphore: Semaphore för rate limiting
        company_id: Bolagets ID i databasen
        company_name: Bolagsnamn
        progress_callback: Callback för progress-uppdateringar
        use_cache: Använd cache om PDF redan extraherats
        base_folder: Basmapp för fillagring
        quiet: Undertryck utskrifter

    Returns:
        Dict med extraherad data
    """
    import re

    pdf_hash = get_pdf_hash(pdf_path)
    filename = Path(pdf_path).stem
    company_slug = slugify(company_name) if company_name else "unknown"

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

    # Skapa temporär mapp för ensidiga PDFs
    temp_dir = tempfile.mkdtemp(prefix="mistral_v2_")

    try:
        extraction_start = time.perf_counter()

        # === STEG 1: DELA UPP PDF ===
        if not quiet:
            print(f"\n   [SPLIT] Delar upp {filename}...", flush=True)

        sidor = dela_upp_pdf(pdf_path, temp_dir)
        num_pages = len(sidor)

        if not quiet:
            print(f"   Uppdelad i {num_pages} sidor", flush=True)

        # === STEG 2: SKAPA SCHEMAN ===
        document_schema = response_format_from_pydantic_model(DocumentAnnotation)
        bbox_schema = response_format_from_pydantic_model(BBoxAnnotation)

        # === STEG 3: BEARBETA ALLA SIDOR ===
        if not quiet:
            print(f"   [OCR] Bearbetar {num_pages} sidor med annotations...", flush=True)

        if progress_callback:
            progress_callback(pdf_path, "ocr", None)

        async with semaphore:
            alla_resultat = await bearbeta_alla_sidor(
                sidor=sidor,
                client=client,
                document_schema=document_schema,
                bbox_schema=bbox_schema,
                progress_callback=progress_callback,
                quiet=quiet,
            )

        ocr_elapsed = time.perf_counter() - extraction_start

        if not quiet:
            print(f"   OCR klar: {ocr_elapsed:.1f}s | {len(alla_resultat)} sidor bearbetade", flush=True)

        # === STEG 4: SAMMANFOGA RESULTAT ===
        sammanfogat = sammanfoga_resultat(alla_resultat)

        # Beräkna kostnad
        cost = num_pages * OCR_PRICE_PER_PAGE * USD_TO_SEK

        # === STEG 4.5: ANALYSERA GRAFER MED PIXTRAL ===
        if sammanfogat.get("charts"):
            pixtral_start = time.perf_counter()
            sammanfogat["charts"] = await analyze_charts_with_pixtral(
                client=client,
                charts=sammanfogat["charts"],
                quiet=quiet,
            )
            pixtral_elapsed = time.perf_counter() - pixtral_start
            if not quiet:
                print(f"   Pixtral klar: {pixtral_elapsed:.1f}s", flush=True)

        # === STEG 5: SPARA GRAFER ===
        period = sammanfogat["metadata"].get("period", "")
        charts_with_paths = []

        for chart in sammanfogat.get("charts", []):
            image_path = spara_graf(
                chart=chart,
                company_slug=company_slug,
                period=period,
                period_id="",  # Sätts efter save_period_atomic_async
                base_folder=base_folder or "alla_rapporter",
            )
            chart_copy = chart.copy()
            chart_copy["image_path"] = image_path
            # Ta bort base64 för att spara minne
            chart_copy.pop("image_base64", None)
            charts_with_paths.append(chart_copy)

        # === STEG 6: BYGG OUTPUT ===
        total_elapsed = time.perf_counter() - extraction_start

        output = {
            "metadata": sammanfogat["metadata"],
            "tables": sammanfogat["tables"],
            "sections": sammanfogat["sections"],
            "charts": charts_with_paths,
            "_source_file": str(pdf_path),
            "_pipeline_info": {
                "pipeline": "mistral-v2-annotations",
                "passes": [
                    {
                        "pass": 1,
                        "model": MISTRAL_OCR,
                        "type": "ocr+annotations",
                        "pages": num_pages,
                        "elapsed_seconds": round(ocr_elapsed, 2),
                    }
                ],
                "total_cost_sek": round(cost, 4),
                "total_elapsed_seconds": round(total_elapsed, 2),
            }
        }

        if not quiet:
            print(f"\n   [RESULTAT]", flush=True)
            print(f"      Tabeller: {len(output['tables'])} st", flush=True)
            print(f"      Grafer: {len(output['charts'])} st", flush=True)
            print(f"      Tid: {total_elapsed:.1f}s | Kostnad: {cost:.4f} SEK", flush=True)

        # === STEG 7: SPARA TILL SUPABASE ===
        period_id, section_ids = await save_period_atomic_async(company_id, output, pdf_hash, str(pdf_path))

        # Generera embeddings
        embeddings_count = 0
        if section_ids:
            try:
                from supabase_client import generate_embeddings_for_sections_async
                embeddings_count = await generate_embeddings_for_sections_async(section_ids)
                if embeddings_count > 0 and not quiet:
                    print(f"   [EMBEDDING] {embeddings_count} sections har fått embeddings")
            except Exception as emb_err:
                if not quiet:
                    print(f"   [EMBEDDING] Kunde inte generera embeddings: {emb_err}")

        # Uppdatera slutstatus
        update_period_status(
            period_id,
            status="success",
            errors=None,
            embeddings_count=embeddings_count
        )

        if progress_callback:
            progress_callback(pdf_path, "done", {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_sek": cost,
            })

        return output

    except Exception as e:
        if progress_callback:
            progress_callback(pdf_path, f"failed: {e}", None)
        raise

    finally:
        # Rensa temporär mapp
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


def get_mistral_client() -> Mistral:
    """Skapa Mistral-klient med API-nyckel från miljövariabler."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError(
            "MISTRAL_API_KEY saknas. "
            "Exportera den med: export MISTRAL_API_KEY='din-nyckel'"
        )
    return Mistral(api_key=api_key)
