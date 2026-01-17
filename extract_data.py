"""
Extrahera finansiell data från PDF:er med Claude API (parallellt)
Enkel approach: Hitta tabeller och extrahera RAW data
"""

import asyncio
import base64
import os
from typing import Any
import json
import subprocess
import tempfile
from pathlib import Path

import anthropic


def _pdf_to_base64_images(pdf_path: str) -> list[str]:
    """
    Konvertera PDF till PNG-bilder och returnera som base64-listorrelated
    Använder 'pdftoppm' (från Poppler)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Konvertera PDF till PNG
        pdf_filename = Path(pdf_path).stem
        output_prefix = os.path.join(tmpdir, pdf_filename)
        
        try:
            subprocess.run(
                ["pdftoppm", "-png", pdf_path, output_prefix],
                check=True,
                capture_output=True
            )
        except FileNotFoundError:
            print("⚠️ pdftoppm inte installerat. Försöker alternativ...")
            # Fallback: använd ImageMagick om tillgängligt
            try:
                subprocess.run(
                    ["convert", "-density", "150", pdf_path, f"{output_prefix}.png"],
                    check=True,
                    capture_output=True
                )
            except FileNotFoundError:
                print("❌ Varken pdftoppm eller ImageMagick installerat. Installera: brew install poppler")
                raise
        
        # Hitta alla genererade PNG-filer
        png_files = sorted(Path(tmpdir).glob(f"{pdf_filename}-*.png"))
        
        print(f"📄 Konverterat {len(png_files)} sidor från PDF")
        
        # Konvertera till base64 (alla sidor för att hitta tabellerna)
        base64_images = []
        for png_file in png_files:
            with open(png_file, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
                base64_images.append(b64)
        
        return base64_images


async def extract_tables_from_pdf(
    pdf_path: str,
    company: str,
    quarter: str,
    year: int,
) -> dict[str, Any]:
    """
    Extrahera financiella tabeller från PDF med Claude vision
    Returnerar RAW tabeller (ingen mappning)
    """
    
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    
    # Konvertera PDF till base64 PNG-bilder
    try:
        base64_images = _pdf_to_base64_images(pdf_path)
    except Exception as e:
        print(f"❌ Kunde inte konvertera PDF: {e}")
        return {
            "company": company,
            "quarter": quarter,
            "year": year,
            "error": f"PDF konvertering misslyckades: {str(e)}",
            "data": None
        }
    
    extraction_prompt = """Du är en finansiell dataanalytiker. 

Din uppgift:
1. Hitta tabellen "Consolidated Income Statement" i denna rapport
2. Hitta tabellen "Consolidated Balance Sheet" (eller "Statement of Financial Position")
3. Hitta tabellen "Consolidated Statement of Cash Flows" (eller "Cash Flow Statement")
4. Extrahera ALL rader och värden från varje tabell - INGEN filtrering eller omstrukturering

Returnera ENDAST en JSON med denna struktur (inget annat text):
{
    "income_statement": [
        ["Item label", "Q1 value", "Q2 value", "Q3 value"],
        ["Item label", value, value, value],
        ...ALLA RADER från tabellen...
    ],
    "balance_sheet": [
        ["Item label", "31-Mar value", "30-Jun value", "30-Sep value"],
        ...ALLA RADER...
    ],
    "cash_flow": [
        ["Item label", "Jan-Mar value", "Apr-Jun value", "Jul-Sep value"],
        ...ALLA RADER...
    ]
}

VIKTIGT:
- Använd de exakta item-namn/labels från rapporten
- Numeriska värden som siffror (inte text), t.ex. 2926 eller -529
- Tomma celler som null
- Sektion-headers som ["Sektionsnamn", "", "", ""] (tom på värdekolumner)
- ALLA rader från tabellen, inklusive headers, sections, subtotals, totals
- Beställ i samma ordning som i rapporten
"""

    # Bygg message content med alla bilder
    content = []
    for img_base64 in base64_images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_base64,
            },
        })
    
    content.append({
        "type": "text",
        "text": extraction_prompt,
    })

    message = client.messages.create(
        model="claude-opus-4-1",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )
    
    response_text = message.content[0].text
    
    try:
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            json_str = response_text[json_start:json_end]
            data = json.loads(json_str)
        else:
            raise ValueError("Ingen JSON hittad i respons")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"⚠️ Fel vid parsing av JSON från {pdf_path}: {e}")
        print(f"Raw response: {response_text[:1000]}")
        return {
            "company": company,
            "quarter": quarter,
            "year": year,
            "error": str(e),
            "data": None
        }
    
    return {
        "company": company,
        "quarter": quarter,
        "year": year,
        "data": data
    }


async def extract_all_quarters(
    company: str,
    pdf_dir: str,
    year: int = 2025,
    quarters: list[str] = None,
) -> dict[str, Any]:
    """
    Extrahera tabeller från alla kvartalsrapporter parallellt
    """
    if quarters is None:
        quarters = ["q1", "q2", "q3"]
    
    tasks = []
    for quarter in quarters:
        pdf_filename = f"{company.lower()}-{quarter.lower()}-{year}.pdf"
        pdf_path = os.path.join(pdf_dir, pdf_filename)
        
        if os.path.exists(pdf_path):
            task = extract_tables_from_pdf(pdf_path, company, quarter, year)
            tasks.append(task)
        else:
            print(f"⚠️ PDF hittades inte: {pdf_path}")
    
    print(f"📊 Extraherar tabeller från {len(tasks)} PDFs för {company.upper()}...")
    results = await asyncio.gather(*tasks)
    
    combined = {
        "company": company,
        "year": year,
        "quarters": {}
    }
    
    for result in results:
        quarter = result["quarter"]
        if result.get("data"):
            combined["quarters"][quarter] = result["data"]
        else:
            print(f"⚠️ Extraction misslyckades för {quarter}: {result.get('error')}")
    
    return combined


if __name__ == "__main__":
    import sys
    
    company = sys.argv[1] if len(sys.argv) > 1 else "freemelt"
    pdf_dir = sys.argv[2] if len(sys.argv) > 2 else "reports"
    
    result = asyncio.run(extract_all_quarters(company, pdf_dir))
    
    output_file = f"{company}_extracted_tables.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)
    
    print(f"✅ Tabeller extraherade: {output_file}")
