"""
Supabase-klient för finansiell datalagring.
Hanterar bolag, perioder och finansiell data.
"""

import asyncio
import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

from logger import get_logger, log_embedding_progress

# Thread pool för parallella DB-operationer
_db_executor = ThreadPoolExecutor(max_workers=4)

# Ladda miljövariabler
load_dotenv()

# Supabase-klient (lazy initialization)
_client: Client | None = None

# Voyage API för embeddings
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
VOYAGE_MODEL = "voyage-4"


def _create_pooled_client() -> Client:
    """
    Skapa Supabase-klient.

    Supabase SDK hanterar connection pooling internt.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL och SUPABASE_KEY måste vara satta.\n"
            "Kopiera .env.example till .env och fyll i dina värden."
        )

    return create_client(url, key)


def get_client() -> Client:
    """
    Hämta eller skapa Supabase-klient med connection pooling.

    Använder lazy initialization med singleton-pattern för att
    återanvända connections mellan anrop.
    """
    global _client
    if _client is None:
        _client = _create_pooled_client()
        print("   [DB] Supabase-klient skapad")
    return _client


def reset_client() -> None:
    """
    Återställ Supabase-klienten.

    Användbart vid connection-problem eller för att frigöra resurser
    efter en stor batch-körning.
    """
    global _client
    if _client is not None:
        # Supabase-klienten har ingen explicit close-metod,
        # men vi kan släppa referensen så GC kan städa
        _client = None
        print("   [DB] Supabase-klient återställd")


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
    Wrapper som anropar save_period_atomic() för bakåtkompatibilitet.

    Args:
        company_id: Bolagets UUID
        data: Extraherad data med metadata, resultatrakning, etc.
        pdf_hash: Hash av PDF för cache-validering
        source_file: Sökväg till käll-PDF

    Returns:
        Period-ID (UUID)
    """
    period_id, section_ids = save_period_atomic(company_id, data, pdf_hash, source_file)

    # Generera embeddings för sections automatiskt
    logger = get_logger('supabase')
    if section_ids:
        try:
            num_embeddings = generate_embeddings_for_sections(section_ids)
            # Uppdatera embeddings_count
            update_period_status(period_id, embeddings_count=num_embeddings)
        except Exception as e:
            logger.warning(f"[EMBEDDING] Kunde inte generera embeddings: {e}")
            # Fortsätt ändå - embeddings kan genereras senare

    return period_id


def save_period_atomic(
    company_id: str,
    data: dict,
    pdf_hash: str | None = None,
    source_file: str | None = None
) -> tuple[str, list[str]]:
    """
    Atomisk sparning av period via RPC-funktion.
    Använder PostgreSQL advisory locks för att förhindra race conditions.

    Args:
        company_id: Bolagets UUID
        data: Extraherad data med metadata, tabeller, sektioner, etc.
        pdf_hash: Hash av PDF för cache-validering
        source_file: Sökväg till käll-PDF

    Returns:
        Tuple av (period_id, lista med section_ids för embeddings)
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

    # Hämta pipeline-info om den finns
    pipeline_info = data.get("_pipeline_info")
    extraction_meta = None
    if pipeline_info:
        extraction_meta = {
            "retry_stats": pipeline_info.get("retry_stats"),
            "validation": pipeline_info.get("validation"),
            "total_cost_sek": pipeline_info.get("total_cost_sek"),
            "total_elapsed_seconds": pipeline_info.get("total_elapsed_seconds"),
            "passes": pipeline_info.get("passes"),
            "pass1_counts": pipeline_info.get("pass1_counts"),
            "missing_tables": pipeline_info.get("missing_tables"),
        }

    # Räkna innehåll för denormalisering
    tables = data.get("tables", [])
    sections = data.get("sections", [])
    charts = data.get("charts", [])

    tables_count = len(tables)
    sections_count = len(sections)
    charts_count = len(charts)
    cost_sek = pipeline_info.get("total_cost_sek", 0) if pipeline_info else 0
    extraction_time = pipeline_info.get("total_elapsed_seconds", 0) if pipeline_info else 0

    # Anropa atomisk RPC-funktion för period-skapande
    # Detta använder advisory lock för att förhindra race conditions
    try:
        result = client.rpc("upsert_period_atomic", {
            "p_company_id": company_id,
            "p_quarter": quarter,
            "p_year": year,
            "p_valuta": metadata.get("valuta"),
            "p_language": metadata.get("sprak", "sv"),
            "p_pdf_hash": pdf_hash,
            "p_source_file": source_file,
            "p_extraction_meta": extraction_meta,
            "p_tables_count": tables_count,
            "p_sections_count": sections_count,
            "p_charts_count": charts_count,
            "p_cost_sek": cost_sek,
            "p_extraction_time_seconds": extraction_time,
        }).execute()

        period_id = result.data
    except Exception as e:
        # Fallback till icke-atomisk sparning om RPC-funktionen inte finns
        # (för bakåtkompatibilitet innan migration körs)
        if "function" in str(e).lower() and "does not exist" in str(e).lower():
            print("   [VARNING] RPC-funktion saknas, använder fallback (kör migration 003)")
            return _save_period_legacy(company_id, data, pdf_hash, source_file)
        raise

    # Spara relaterade tabeller med rollback vid fel
    # Om någon insert misslyckas, ta bort perioden för att undvika ofullständig data
    section_ids = []

    try:
        # Förbered finansiell data (legacy-format)
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

        # Parallella DB-writes med ThreadPoolExecutor
        # Sections måste köras först då vi behöver section_ids
        if sections:
            section_ids = save_sections(period_id, sections)

        # Kör resterande inserts parallellt (financial_data, tables, charts)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _insert_financial():
            if financial_rows:
                client.table("financial_data").insert(financial_rows).execute()

        def _insert_tables():
            if tables:
                save_tables(period_id, tables)

        def _insert_charts():
            if charts:
                save_charts(period_id, charts)

        # Kör parallellt med thread pool
        futures = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            if financial_rows:
                futures.append(executor.submit(_insert_financial))
            if tables:
                futures.append(executor.submit(_insert_tables))
            if charts:
                futures.append(executor.submit(_insert_charts))

            # Vänta på alla och samla eventuella fel
            errors = []
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(e)

            # Om charts misslyckades, logga varning men fortsätt
            # Om financial_data eller tables misslyckades, kasta fel
            if errors:
                # Kolla om det bara är chart-fel
                chart_errors = [e for e in errors if "charts" in str(e).lower()]
                other_errors = [e for e in errors if "charts" not in str(e).lower()]

                for e in chart_errors:
                    print(f"   [VARNING] Kunde inte spara grafer: {e}")

                if other_errors:
                    raise other_errors[0]

    except Exception as e:
        # Rollback: Ta bort perioden om relaterad data inte kunde sparas
        # CASCADE tar bort all relaterad data (financial_data, sections, etc.)
        print(f"   [ROLLBACK] Fel vid sparning av relaterad data: {e}")
        print(f"   [ROLLBACK] Tar bort period {period_id}...")
        try:
            client.table("periods").delete().eq("id", period_id).execute()
        except Exception as rollback_error:
            print(f"   [ROLLBACK] Kunde inte ta bort period: {rollback_error}")
        raise  # Kasta vidare ursprungligt fel

    return period_id, section_ids


async def save_period_atomic_async(
    company_id: str,
    data: dict,
    pdf_hash: str | None = None,
    source_file: str | None = None
) -> tuple[str, list[str]]:
    """
    Async-version av save_period_atomic som inte blockerar event loop.

    Kör den synkrona save_period_atomic i en thread pool för att
    inte blockera andra async-operationer under batch-extraktion.

    Args:
        company_id: Bolagets UUID
        data: Extraherad data med metadata, tabeller, sektioner, etc.
        pdf_hash: Hash av PDF för cache-validering
        source_file: Sökväg till käll-PDF

    Returns:
        Tuple av (period_id, lista med section_ids för embeddings)
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _db_executor,
        lambda: save_period_atomic(company_id, data, pdf_hash, source_file)
    )


# TODO: Ta bort legacy-funktionerna (_save_period_legacy, _get_global_stats_legacy,
#       _get_company_stats_legacy) när migration 003 har körts i alla miljöer.
#       De behövs endast som fallback om RPC-funktionerna saknas i databasen.


def _save_period_legacy(
    company_id: str,
    data: dict,
    pdf_hash: str | None = None,
    source_file: str | None = None
) -> tuple[str, list[str]]:
    """
    Legacy-version av save_period för bakåtkompatibilitet.
    Används som fallback om RPC-funktionen inte finns.
    OBS: Denna version har race condition-risk vid parallella extraktioner.
    """
    client = get_client()
    metadata = data.get("metadata", {})

    period_str = metadata.get("period", "")
    match = re.search(r'Q(\d)\s*(\d{4})', period_str)
    if not match:
        raise ValueError(f"Ogiltigt periodformat: {period_str}")

    quarter = int(match.group(1))
    year = int(match.group(2))

    # Kontrollera om period redan finns
    existing = get_period(company_id, quarter, year)
    if existing:
        # Ta bort befintlig data (VARNING: icke-atomiskt!)
        client.table("financial_data").delete().eq("period_id", existing["id"]).execute()
        client.table("sections").delete().eq("period_id", existing["id"]).execute()
        client.table("report_tables").delete().eq("period_id", existing["id"]).execute()
        try:
            client.table("charts").delete().eq("period_id", existing["id"]).execute()
        except Exception:
            pass  # charts-tabell kanske inte finns
        client.table("periods").delete().eq("id", existing["id"]).execute()

    # Hämta pipeline-info
    pipeline_info = data.get("_pipeline_info")
    extraction_meta = None
    if pipeline_info:
        extraction_meta = {
            "retry_stats": pipeline_info.get("retry_stats"),
            "validation": pipeline_info.get("validation"),
            "total_cost_sek": pipeline_info.get("total_cost_sek"),
            "total_elapsed_seconds": pipeline_info.get("total_elapsed_seconds"),
            "passes": pipeline_info.get("passes"),
            "pass1_counts": pipeline_info.get("pass1_counts"),
            "missing_tables": pipeline_info.get("missing_tables"),
        }

    # Räkna innehåll
    tables = data.get("tables", [])
    sections = data.get("sections", [])
    charts = data.get("charts", [])

    # Skapa period
    period_result = client.table("periods").insert({
        "company_id": company_id,
        "quarter": quarter,
        "year": year,
        "valuta": metadata.get("valuta"),
        "language": metadata.get("sprak", "sv"),
        "pdf_hash": pdf_hash,
        "source_file": source_file,
        "extraction_meta": extraction_meta,
        "extraction_status": "extracting",
        "tables_count": len(tables),
        "sections_count": len(sections),
        "charts_count": len(charts),
        "cost_sek": pipeline_info.get("total_cost_sek", 0) if pipeline_info else 0,
        "extraction_time_seconds": pipeline_info.get("total_elapsed_seconds", 0) if pipeline_info else 0,
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
        client.table("financial_data").insert(financial_rows).execute()

    # Spara sektioner
    section_ids = []
    if sections:
        section_ids = save_sections(period_id, sections)

    # Spara tabeller
    if tables:
        save_tables(period_id, tables)

    # Spara grafer
    if charts:
        try:
            save_charts(period_id, charts)
        except Exception as e:
            print(f"   [VARNING] Kunde inte spara grafer: {e}")

    return period_id, section_ids


def update_period_status(
    period_id: str,
    status: str | None = None,
    errors: list[dict] | None = None,
    embeddings_count: int | None = None
) -> None:
    """
    Uppdatera extraktionsstatus och logga fel.

    Args:
        period_id: Period UUID
        status: 'success', 'partial', eller 'failed' (None = behåll nuvarande)
        errors: Lista med feldetaljer att logga
        embeddings_count: Antal genererade embeddings (None = behåll nuvarande)
    """
    client = get_client()

    # Bygg update-payload
    update_data = {}

    if status is not None:
        update_data["extraction_status"] = status

    if errors is not None:
        error_count = len([e for e in errors if e.get("severity") in ("critical", "error")])
        warning_count = len([e for e in errors if e.get("severity") == "warning"])
        update_data["error_count"] = error_count
        update_data["warning_count"] = warning_count

    if embeddings_count is not None:
        update_data["embeddings_count"] = embeddings_count

    # Uppdatera period om det finns något att uppdatera
    if update_data:
        try:
            client.table("periods").update(update_data).eq("id", period_id).execute()
        except Exception as e:
            # Nya kolumner kanske inte finns ännu
            if "column" not in str(e).lower():
                print(f"   [VARNING] Kunde inte uppdatera period-status: {e}")

    # Logga individuella fel till extraction_errors-tabellen
    if errors:
        error_rows = []
        for e in errors:
            error_rows.append({
                "period_id": period_id,
                "error_type": e.get("error_type", "unknown"),
                "severity": e.get("severity", "error"),
                "component": e.get("component"),
                "details": e.get("details"),
            })

        try:
            client.table("extraction_errors").insert(error_rows).execute()
        except Exception as ex:
            # extraction_errors-tabellen kanske inte finns ännu
            if "relation" not in str(ex).lower():
                print(f"   [VARNING] Kunde inte logga fel: {ex}")


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
            "valuta": period.get("valuta", "TSEK"),
            "sprak": period.get("language", "sv")  # sv, no, eller en
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
        except Exception as e:
            # Tysta bara om det är PostgrestAPIError (tabell saknas), logga övriga fel
            if "relation" not in str(e).lower() and "does not exist" not in str(e).lower():
                print(f"   [VARNING] Kunde inte ladda grafer: {e}")
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

def save_sections(period_id: str, sections: list[dict]) -> list[str]:
    """
    Spara textsektioner till Supabase.

    Args:
        period_id: Period-UUID
        sections: Lista med sektioner från full extraktion

    Returns:
        Lista med section UUIDs som skapades
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
            # embedding sätts automatiskt efteråt
        })

    if rows:
        result = client.table("sections").insert(rows).execute()
        return [r["id"] for r in result.data]

    return []


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
            "image_path": chart.get("image_path"),  # Sökväg till grafbild
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


# === EMBEDDINGS ===

# Proaktiv rate limit-tracker för Voyage AI
# Voyage AI har typiskt 300 requests/min, vi håller oss under med marginal
_voyage_request_times: list[float] = []
VOYAGE_RPM_LIMIT = 280  # Konservativ gräns (300 officiellt)
VOYAGE_WINDOW_SECONDS = 60


def _check_voyage_rate_limit() -> None:
    """
    Proaktiv rate limit-kontroll för Voyage AI.
    Väntar om vi närmar oss gränsen istället för att få 429.
    """
    global _voyage_request_times
    import time as time_module

    now = time_module.time()

    # Ta bort requests äldre än 60 sekunder
    _voyage_request_times = [t for t in _voyage_request_times if now - t < VOYAGE_WINDOW_SECONDS]

    # Om vi närmar oss gränsen, vänta
    if len(_voyage_request_times) >= VOYAGE_RPM_LIMIT:
        oldest = _voyage_request_times[0]
        wait_time = VOYAGE_WINDOW_SECONDS - (now - oldest) + 1
        if wait_time > 0:
            print(f"    [RATE LIMIT] Proaktiv paus {wait_time:.0f}s (nått {VOYAGE_RPM_LIMIT} req/min)")
            time_module.sleep(wait_time)
            # Rensa efter väntan
            now = time_module.time()
            _voyage_request_times = [t for t in _voyage_request_times if now - t < VOYAGE_WINDOW_SECONDS]

    # Registrera denna request
    _voyage_request_times.append(now)


def get_voyage_embeddings(texts: list[str], max_retries: int = 5) -> list[list[float]]:
    """
    Hämta embeddings från Voyage AI API med retry-logik.

    Args:
        texts: Lista med texter att generera embeddings för
        max_retries: Max antal retry vid rate limiting

    Returns:
        Lista med embedding-vektorer (1024 dimensioner)
    """
    # Proaktiv rate limit-kontroll - vänta om vi närmar oss gränsen
    _check_voyage_rate_limit()

    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {VOYAGE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": VOYAGE_MODEL,
                    "input": texts,
                    "input_type": "document"
                },
                timeout=30
            )

            if response.status_code == 429:
                wait_time = 2 ** attempt * 5  # 5, 10, 20, 40, 80 sekunder
                print(f"    [EMBEDDING] Rate limited, vantar {wait_time}s...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"    [EMBEDDING] Fel: {e}, retry om {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise

    raise Exception("Max retries exceeded for embeddings")


def generate_embeddings_for_sections(section_ids: list[str]) -> int:
    """
    Generera embeddings för specifika sections med loggning.

    Args:
        section_ids: Lista med section UUIDs

    Returns:
        Antal uppdaterade sections
    """
    if not section_ids:
        return 0

    logger = get_logger('embeddings')
    client = get_client()

    # Hämta sections
    result = client.table("sections").select("id, title, content").in_("id", section_ids).execute()
    sections = result.data

    if not sections:
        logger.warning("[EMBEDDING] Inga sektioner hittades för angivna IDs")
        return 0

    # Processa i batchar om 10
    batch_size = 10
    total_processed = 0
    total_failed = 0
    num_batches = (len(sections) + batch_size - 1) // batch_size

    logger.info(f"[EMBEDDING] Genererar embeddings för {len(sections)} sektioner ({num_batches} batchar)")

    for i in range(0, len(sections), batch_size):
        batch = sections[i:i + batch_size]
        batch_num = i // batch_size + 1

        # Kombinera title + content för bättre embedding
        texts = [f"{s['title']}\n\n{s['content']}" for s in batch]

        try:
            embeddings = get_voyage_embeddings(texts)

            # Uppdatera varje section med sin embedding
            for section, embedding in zip(batch, embeddings):
                client.table("sections").update({
                    "embedding": embedding
                }).eq("id", section["id"]).execute()
                total_processed += 1

            logger.debug(f"[EMBEDDING] Batch {batch_num}/{num_batches}: {len(batch)} sektioner OK")
            log_embedding_progress(total_processed, len(sections), batch_num, success=True)

            # Paus mellan batchar för att undvika rate limit
            if i + batch_size < len(sections):
                time.sleep(1)

        except Exception as e:
            total_failed += len(batch)
            logger.warning(f"[EMBEDDING] Batch {batch_num}/{num_batches} FEL: {e}")
            log_embedding_progress(total_processed, len(sections), batch_num, success=False)
            # Fortsätt med nästa batch istället för att avbryta
            continue

    # Slutrapport
    if total_failed > 0:
        logger.warning(f"[EMBEDDING] Klart med varningar: {total_processed} OK, {total_failed} misslyckade")
    else:
        logger.info(f"[EMBEDDING] Klart: {total_processed}/{len(sections)} sektioner fick embeddings")

    return total_processed


async def generate_embeddings_for_sections_async(section_ids: list[str]) -> int:
    """
    Async-version av embedding-generering som inte blockerar event loop.

    Kör den synkrona embedding-genereringen i en thread pool för att
    inte blockera andra async-operationer.

    Args:
        section_ids: Lista med section UUIDs

    Returns:
        Antal uppdaterade sections
    """
    if not section_ids:
        return 0

    # Kör synkron funktion i thread pool
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _db_executor,
        generate_embeddings_for_sections,
        section_ids
    )
