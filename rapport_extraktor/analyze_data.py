"""
Analysera Vitrolife-data i Supabase för Q2 och Q3 2025.
Jämför konsistens i radnamn, struktur och datakvalitet.
"""

import os
import json
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Anslut till Supabase
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
client = create_client(url, key)

print("=" * 70)
print("DATABASANALYS: Vitrolife Q2 & Q3 2025")
print("=" * 70)

# Hämta Vitrolife
company = client.table("companies").select("*").eq("slug", "vitrolife").execute()
if not company.data:
    print("❌ Vitrolife finns inte i databasen!")
    exit()

company_id = company.data[0]["id"]
company_name = company.data[0]["name"]
print(f"\n📊 Bolag: {company_name} (ID: {company_id})")

# Hämta alla perioder för Vitrolife
periods = client.table("periods").select("*").eq("company_id", company_id).order("year").order("quarter").execute()
print(f"\n📅 Perioder i databasen: {len(periods.data)} st")

for p in periods.data:
    print(f"   - Q{p['quarter']} {p['year']} (valuta: {p.get('valuta', 'N/A')})")

print("\n" + "=" * 70)
print("DETALJERAD ANALYS PER PERIOD")
print("=" * 70)

all_tables_by_period = {}
all_sections_by_period = {}

for period in periods.data:
    period_id = period["id"]
    period_name = f"Q{period['quarter']} {period['year']}"

    print(f"\n{'─' * 70}")
    print(f"📄 {period_name}")
    print(f"{'─' * 70}")

    # Hämta tabeller
    tables = client.table("report_tables").select("*").eq("period_id", period_id).order("page_number").execute()
    print(f"\n   📊 TABELLER ({len(tables.data)} st):")

    all_tables_by_period[period_name] = []

    for t in tables.data:
        table_info = {
            "title": t["title"],
            "type": t["table_type"],
            "page": t["page_number"],
            "columns": t["columns"],
            "row_count": len(t["rows"]) if t["rows"] else 0
        }
        all_tables_by_period[period_name].append(table_info)

        print(f"\n      📋 {t['title']}")
        print(f"         Typ: {t['table_type']} | Sida: {t['page_number']}")
        print(f"         Kolumner: {t['columns']}")
        print(f"         Antal rader: {len(t['rows']) if t['rows'] else 0}")

        # Visa första 5 radnamn
        if t["rows"]:
            print(f"         Rader (första 10):")
            for row in t["rows"][:10]:
                row_name = row.get("rad") or row.get("namn") or row.get("name") or str(list(row.keys())[0] if row else "N/A")
                row_value = row.get("varde") or row.get("value") or row.get(list(row.keys())[1] if len(row) > 1 else "N/A", "N/A")
                print(f"            - {row_name}: {row_value}")

    # Hämta sektioner
    sections = client.table("sections").select("*").eq("period_id", period_id).order("page_number").execute()
    print(f"\n   📝 SEKTIONER ({len(sections.data)} st):")

    all_sections_by_period[period_name] = []

    for s in sections.data:
        section_info = {
            "title": s["title"],
            "type": s["section_type"],
            "page": s["page_number"],
            "content_length": len(s["content"]) if s["content"] else 0
        }
        all_sections_by_period[period_name].append(section_info)

        content_preview = s["content"][:100] + "..." if s["content"] and len(s["content"]) > 100 else s["content"]
        print(f"\n      📄 {s['title']}")
        print(f"         Typ: {s['section_type']} | Sida: {s['page_number']}")
        print(f"         Innehåll ({len(s['content']) if s['content'] else 0} tecken): {content_preview}")

    # Hämta grafer
    try:
        charts = client.table("charts").select("*").eq("period_id", period_id).execute()
        if charts.data:
            print(f"\n   📈 GRAFER ({len(charts.data)} st):")
            for c in charts.data:
                print(f"\n      📊 {c['title']}")
                print(f"         Typ: {c['chart_type']} | Sida: {c['page_number']}")
    except:
        pass

    # Hämta legacy financial_data (om det finns)
    fin_data = client.table("financial_data").select("*").eq("period_id", period_id).order("row_order").execute()
    if fin_data.data:
        print(f"\n   💰 LEGACY FINANCIAL_DATA ({len(fin_data.data)} st rader):")

        # Gruppera per statement_type
        by_type = {}
        for row in fin_data.data:
            st = row["statement_type"]
            if st not in by_type:
                by_type[st] = []
            by_type[st].append(row)

        for st, rows in by_type.items():
            print(f"\n      {st} ({len(rows)} rader):")
            for row in rows[:5]:
                print(f"         - {row['row_name']}: {row['value']}")

print("\n" + "=" * 70)
print("KONSISTENSANALYS")
print("=" * 70)

# Jämför tabelltyper mellan perioder
print("\n📊 TABELLTYPER PER PERIOD:")
period_names = sorted(all_tables_by_period.keys())
for period_name in period_names:
    tables = all_tables_by_period[period_name]
    table_types = [t["type"] for t in tables]
    print(f"   {period_name}: {table_types}")

# Jämför tabellnamn
print("\n📋 TABELLNAMN PER PERIOD:")
for period_name in period_names:
    tables = all_tables_by_period[period_name]
    table_names = [t["title"] for t in tables]
    print(f"\n   {period_name}:")
    for name in table_names:
        print(f"      - {name}")

# Jämför sektionstyper
print("\n📝 SEKTIONSTYPER PER PERIOD:")
for period_name in period_names:
    sections = all_sections_by_period.get(period_name, [])
    section_types = [s["type"] for s in sections]
    print(f"   {period_name}: {section_types}")

# Specifik jämförelse av resultaträkning
print("\n" + "=" * 70)
print("DETALJERAD JÄMFÖRELSE: RESULTATRÄKNING")
print("=" * 70)

for period_name in period_names:
    tables = all_tables_by_period[period_name]
    for t in tables:
        if t["type"] in ["income_statement", "resultatrakning"] or "resultat" in t["title"].lower():
            # Hämta full data igen
            period = [p for p in periods.data if f"Q{p['quarter']} {p['year']}" == period_name][0]
            full_table = client.table("report_tables").select("*").eq("period_id", period["id"]).eq("title", t["title"]).execute()

            if full_table.data:
                print(f"\n   {period_name} - {t['title']}:")
                rows = full_table.data[0]["rows"]
                if rows:
                    for row in rows:
                        print(f"      {row}")

print("\n" + "=" * 70)
print("ANALYS KLAR")
print("=" * 70)
