"""
Huvudscript: Extrahera tabeller från PDF:er och skapa Excel-databok
Använder parallella API-anrop för snabb extraction
"""

import asyncio
import json
import os
import sys

from extract_data import extract_all_quarters
from create_databook import create_workbook_from_tables


async def main():
    """
    Huvudflöde:
    1. Extrahera tabeller från PDF:er (parallellt)
    2. Skapa Excel-arbetsbok från tabeller
    3. Spara resultat
    """
    
    if len(sys.argv) < 2:
        print("Användning: python main.py <company> [pdf_dir] [output_dir]")
        print("  Exempel: python main.py freemelt reports .")
        print("  Exempel: python main.py vitrolife vitrolife_reports .")
        sys.exit(1)
    
    company = sys.argv[1].lower()
    pdf_dir = sys.argv[2] if len(sys.argv) > 2 else ("vitrolife_reports" if company == "vitrolife" else "reports")
    output_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    year = 2025
    
    print(f"\n{'='*60}")
    print(f"📊 Skapar finansiell databok för {company.upper()}")
    print(f"{'='*60}\n")
    
    # 1. Extrahera tabeller från PDF:er (parallellt)
    print(f"⏱️  Extraherar tabeller från PDF:er...")
    import time
    start_time = time.time()
    
    extracted = await extract_all_quarters(company, pdf_dir, year)
    
    elapsed = time.time() - start_time
    print(f"✅ Extraction klar på {elapsed:.1f} sekunder\n")
    
    if not extracted.get('quarters'):
        print("❌ Ingen data extraherad. Kontrollera PDF-sökvägar och API-nyckel.")
        sys.exit(1)
    
    # 2. Förbered config
    print("🔄 Förbereder data...")
    
    company_config = {
        "freemelt": "FREEMELT HOLDING AB (PUBL)",
        "vitrolife": "VITROLIFE AB (PUBL)",
    }
    
    company_name = company_config.get(company.lower(), f"{company.upper()} AB")
    
    quarters_data = extracted['quarters']
    first_quarter = list(quarters_data.keys())[0] if quarters_data else None
    
    if not first_quarter:
        print("❌ Ingen kvartalsdata hittad.")
        sys.exit(1)
    
    first_data = quarters_data[first_quarter]
    
    config = {
        'company_name': company_name,
        'company_key': company,
        'year': year,
        'income_statement': first_data.get('income_statement', []),
        'balance_sheet': first_data.get('balance_sheet', []),
        'cash_flow': first_data.get('cash_flow', []),
    }
    
    # 3. Skapa Excel-arbetsbok
    print("📝 Skapar Excel-arbetsbok...")
    
    wb = create_workbook_from_tables(config)
    
    # 4. Spara resultat
    output_filename = f"{company}_{year}_quarterly_report.xlsx"
    output_path = os.path.join(output_dir, output_filename)
    
    wb.save(output_path)
    print(f"✅ Excel-fil sparad: {output_path}\n")
    
    # 5. Spara extracted tabeller som JSON
    json_output = os.path.join(output_dir, f"{company}_{year}_extracted_tables.json")
    with open(json_output, "w") as f:
        json.dump(extracted, f, indent=2, ensure_ascii=False)
    print(f"📋 Extraherade tabeller sparade: {json_output}\n")
    
    print(f"{'='*60}")
    print(f"✨ Databok skapad framgångsrikt!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
