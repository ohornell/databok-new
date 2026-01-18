"""
Supabase-klient för finansiell datalagring.
Hanterar bolag, perioder och finansiell data.
"""

import hashlib
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

# Ladda miljövariabler
load_dotenv()

# Supabase-klient (lazy initialization)
_client: Client | None = None


def get_client() -> Client:
    """Hämta eller skapa Supabase-klient."""
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError(
                "SUPABASE_URL och SUPABASE_KEY måste vara satta.\n"
                "Kopiera .env.example till .env och fyll i dina värden."
            )
        _client = create_client(url, key)
    return _client


def check_database_setup() -> tuple[bool, str]:
    """
    Kontrollera om databasen är korrekt uppsatt.
    Returnerar (ok, meddelande).
    """
    client = get_client()
    url = os.environ.get("SUPABASE_URL", "")

    missing_tables = []

    # Kontrollera varje tabell (inkl. nya tabeller för full extraktion)
    for table in ["companies", "periods", "financial_data", "sections", "report_tables"]:
        try:
            client.table(table).select("*").limit(1).execute()
        except Exception:
            missing_tables.append(table)

    if not missing_tables:
        return True, "Databasen är korrekt uppsatt."

    # Generera hjälpmeddelande
    project_id = url.split("//")[1].split(".")[0] if "//" in url else "xxx"
    sql_editor_url = f"https://supabase.com/dashboard/project/{project_id}/sql/new"

    message = f"""
Databasen saknar tabeller: {', '.join(missing_tables)}

Kör följande steg:

1. Öppna Supabase SQL Editor:
   {sql_editor_url}

2. Kopiera och klistra in innehållet från schema.sql

3. Klicka "Run" för att skapa tabellerna

4. Kör detta kommando igen
"""
    return False, message


def verify_or_exit():
    """Verifiera databassetup, avsluta med instruktioner om den saknas."""
    ok, message = check_database_setup()
    if not ok:
        print(message)
        raise SystemExit(1)
    return True


def slugify(name: str) -> str:
    """Konvertera bolagsnamn till URL-vänlig slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)  # Ta bort specialtecken
    slug = re.sub(r'[\s_-]+', '-', slug)  # Ersätt mellanslag med bindestreck
    slug = slug.strip('-')
    return slug


def get_pdf_hash(pdf_path: str) -> str:
    """Generera hash av PDF-innehåll för cache-validering."""
    pdf_bytes = Path(pdf_path).read_bytes()
    return hashlib.md5(pdf_bytes).hexdigest()[:12]


# === BOLAG ===

def get_or_create_company(name: str) -> dict:
    """
    Hämta eller skapa ett bolag.

    Returns:
        Dict med id, name, slug
    """
    client = get_client()
    slug = slugify(name)

    # Försök hämta befintligt
    result = client.table("companies").select("*").eq("slug", slug).execute()
    if result.data:
        return result.data[0]

    # Skapa nytt
    result = client.table("companies").insert({
        "name": name,
        "slug": slug
    }).execute()
    return result.data[0]


def list_companies() -> list[dict]:
    """Lista alla bolag i databasen."""
    client = get_client()
    result = client.table("companies").select("*").order("name").execute()
    return result.data


def get_company_by_slug(slug: str) -> dict | None:
    """Hämta bolag via slug."""
    client = get_client()
    result = client.table("companies").select("*").eq("slug", slug).execute()
    return result.data[0] if result.data else None


# === PERIODER ===

def period_exists(company_id: str, quarter: int, year: int, pdf_hash: str | None = None) -> bool:
    """
    Kontrollera om en period redan finns.
    Om pdf_hash anges, kontrollera även att hashen matchar (för cache-validering).
    """
    client = get_client()
    query = client.table("periods").select("id, pdf_hash").eq(
        "company_id", company_id
    ).eq("quarter", quarter).eq("year", year)

    result = query.execute()

    if not result.data:
        return False

    # Om pdf_hash anges, kontrollera att den matchar
    if pdf_hash:
        return result.data[0].get("pdf_hash") == pdf_hash

    return True


def get_period(company_id: str, quarter: int, year: int) -> dict | None:
    """Hämta en specifik period."""
    client = get_client()
    result = client.table("periods").select("*").eq(
        "company_id", company_id
    ).eq("quarter", quarter).eq("year", year).execute()
    return result.data[0] if result.data else None


def save_period(company_id: str, data: dict, pdf_hash: str | None = None, source_file: str | None = None) -> str:
    """
    Spara en period med all finansiell data.

    Args:
        company_id: Bolagets UUID
        data: Extraherad data med metadata, resultatrakning, etc.
        pdf_hash: Hash av PDF för cache-validering
        source_file: Sökväg till käll-PDF

    Returns:
        Period-ID (UUID)
    """
    client = get_client()
    metadata = data.get("metadata", {})

    # Parsa period (Q1 2025 -> quarter=1, year=2025)
    period_str = metadata.get("period", "")
    match = re.search(r'Q(\d)\s*(\d{4})', period_str)
    if not match:
        raise ValueError(f"Ogiltigt periodformat: {period_str}")

    quarter = int(match.group(1))
    year = int(match.group(2))

    # Kontrollera om period redan finns
    existing = get_period(company_id, quarter, year)
    if existing:
        # Ta bort befintlig data för att uppdatera (inkl. sections och report_tables)
        client.table("financial_data").delete().eq("period_id", existing["id"]).execute()
        client.table("sections").delete().eq("period_id", existing["id"]).execute()
        client.table("report_tables").delete().eq("period_id", existing["id"]).execute()
        client.table("periods").delete().eq("id", existing["id"]).execute()

    # Skapa period
    period_result = client.table("periods").insert({
        "company_id": company_id,
        "quarter": quarter,
        "year": year,
        "valuta": metadata.get("valuta"),
        "pdf_hash": pdf_hash,
        "source_file": source_file
    }).execute()

    period_id = period_result.data[0]["id"]

    # Spara finansiell data
    financial_rows = []
    for statement_type in ["resultatrakning", "balansrakning", "kassaflodesanalys"]:
        rows = data.get(statement_type, [])
        for order, row in enumerate(rows):
            financial_rows.append({
                "period_id": period_id,
                "statement_type": statement_type,
                "row_order": order,
                "row_name": row.get("rad") or row.get("namn", ""),
                "value": row.get("varde"),
                "row_type": row.get("typ")
            })

    if financial_rows:
        # Batch insert för snabbhet
        client.table("financial_data").insert(financial_rows).execute()

    # Spara sektioner (full extraktion)
    sections = data.get("sections", [])
    if sections:
        save_sections(period_id, sections)

    # Spara tabeller (full extraktion)
    tables = data.get("tables", [])
    if tables:
        save_tables(period_id, tables)

    # Spara grafer (full extraktion) - om tabellen finns
    charts = data.get("charts", [])
    if charts:
        try:
            save_charts(period_id, charts)
        except Exception:
            pass  # charts-tabellen finns inte ännu

    return period_id


def load_period(company_id: str, quarter: int, year: int) -> dict | None:
    """
    Ladda en period med all finansiell data.
    Returnerar samma format som extractor.py för kompatibilitet.
    Stödjer både legacy-format och nya full-extraktion formatet.
    """
    client = get_client()

    # Hämta period
    period = get_period(company_id, quarter, year)
    if not period:
        return None

    # Hämta bolagsnamn
    company = client.table("companies").select("name").eq("id", company_id).execute()
    company_name = company.data[0]["name"] if company.data else "Okänt"

    # Bygg resultat
    result = {
        "metadata": {
            "bolag": company_name,
            "period": f"Q{period['quarter']} {period['year']}",
            "valuta": period.get("valuta", "TSEK")
        },
        "_source_file": period.get("source_file", "")
    }

    # Kolla om det finns nya tabeller (full extraktion)
    tables_data = client.table("report_tables").select("*").eq(
        "period_id", period["id"]
    ).order("page_number").execute()

    sections_data = client.table("sections").select("*").eq(
        "period_id", period["id"]
    ).order("page_number").execute()

    if tables_data.data or sections_data.data:
        # Nytt format - full extraktion
        result["tables"] = []
        for t in tables_data.data:
            result["tables"].append({
                "title": t["title"],
                "page": t["page_number"],
                "type": t["table_type"],
                "columns": t["columns"] or [],
                "rows": t["rows"] or []
            })

        result["sections"] = []
        for s in sections_data.data:
            result["sections"].append({
                "title": s["title"],
                "page": s["page_number"],
                "type": s["section_type"],
                "content": s["content"]
            })

        # Ladda grafer (om tabellen finns)
        result["charts"] = []
        try:
            charts_data = client.table("charts").select("*").eq(
                "period_id", period["id"]
            ).order("page_number").execute()

            for c in charts_data.data:
                result["charts"].append({
                    "title": c["title"],
                    "page": c["page_number"],
                    "chart_type": c["chart_type"],
                    "x_axis": c["x_axis"],
                    "y_axis": c["y_axis"],
                    "estimated": c["estimated"],
                    "data_points": c["data_points"] or []
                })
        except Exception:
            pass  # charts-tabellen finns inte ännu
    else:
        # Legacy-format
        result["resultatrakning"] = []
        result["balansrakning"] = []
        result["kassaflodesanalys"] = []

        fin_data = client.table("financial_data").select("*").eq(
            "period_id", period["id"]
        ).order("row_order").execute()

        for row in fin_data.data:
            statement_type = row["statement_type"]
            if statement_type in result:
                row_data = {
                    "rad": row["row_name"],
                    "varde": row["value"]
                }
                if row.get("row_type"):
                    row_data["typ"] = row["row_type"]
                result[statement_type].append(row_data)

    return result


def load_all_periods(company_id: str) -> list[dict]:
    """
    Ladda alla perioder för ett bolag.
    Returnerar lista i samma format som extractor.py.
    """
    client = get_client()

    # Hämta alla perioder för bolaget
    periods = client.table("periods").select("quarter, year").eq(
        "company_id", company_id
    ).order("year").order("quarter").execute()

    results = []
    for p in periods.data:
        data = load_period(company_id, p["quarter"], p["year"])
        if data:
            results.append(data)

    return results


# === SECTIONS OCH TABLES (FULL EXTRAKTION) ===

def save_sections(period_id: str, sections: list[dict]) -> None:
    """
    Spara textsektioner till Supabase.

    Args:
        period_id: Period-UUID
        sections: Lista med sektioner från full extraktion
    """
    client = get_client()

    rows = []
    for section in sections:
        rows.append({
            "period_id": period_id,
            "title": section.get("title", ""),
            "page_number": section.get("page"),
            "section_type": section.get("type"),
            "content": section.get("content", ""),
            # embedding sätts separat via generate_embedding()
        })

    if rows:
        client.table("sections").insert(rows).execute()


def save_tables(period_id: str, tables: list[dict]) -> None:
    """
    Spara tabeller till Supabase som JSONB.

    Args:
        period_id: Period-UUID
        tables: Lista med tabeller från full extraktion
    """
    client = get_client()

    rows = []
    for table in tables:
        rows.append({
            "period_id": period_id,
            "title": table.get("title", ""),
            "page_number": table.get("page"),
            "table_type": table.get("type"),
            "columns": table.get("columns", []),
            "rows": table.get("rows", []),
        })

    if rows:
        client.table("report_tables").insert(rows).execute()


def load_sections(period_id: str) -> list[dict]:
    """Ladda alla sektioner för en period."""
    client = get_client()
    result = client.table("sections").select("*").eq(
        "period_id", period_id
    ).order("page_number").execute()
    return result.data


def load_tables(period_id: str) -> list[dict]:
    """Ladda alla tabeller för en period."""
    client = get_client()
    result = client.table("report_tables").select("*").eq(
        "period_id", period_id
    ).order("page_number").execute()
    return result.data


def save_charts(period_id: str, charts: list[dict]) -> None:
    """
    Spara grafer/diagram till Supabase.

    Args:
        period_id: Period-UUID
        charts: Lista med grafer från full extraktion
    """
    client = get_client()

    rows = []
    for chart in charts:
        rows.append({
            "period_id": period_id,
            "title": chart.get("title", ""),
            "page_number": chart.get("page"),
            "chart_type": chart.get("chart_type"),
            "x_axis": chart.get("x_axis"),
            "y_axis": chart.get("y_axis"),
            "estimated": chart.get("estimated", True),
            "data_points": chart.get("data_points", []),
        })

    if rows:
        client.table("charts").insert(rows).execute()


def load_charts(period_id: str) -> list[dict]:
    """Ladda alla grafer för en period."""
    client = get_client()
    result = client.table("charts").select("*").eq(
        "period_id", period_id
    ).order("page_number").execute()
    return result.data


# === HJÄLPFUNKTIONER ===

def parse_period_string(period_str: str) -> tuple[int, int] | None:
    """Parsa periodstring till (quarter, year)."""
    match = re.search(r'Q(\d)\s*(\d{4})', period_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None
