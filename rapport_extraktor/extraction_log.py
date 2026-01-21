"""
Logghantering för PDF-extraktion.

Skapar och uppdaterar loggfiler per bolag som sammanfattar all extraktion.
Hanterar också filflyttning efter lyckad extraktion.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from supabase_client import get_client, get_company_by_slug, slugify


def get_extraction_log_path(company_folder: Path) -> Path:
    """Returnera sökväg till loggfilen för ett bolag."""
    db_folder = company_folder / "ligger_i_databasen"
    db_folder.mkdir(parents=True, exist_ok=True)
    return db_folder / "extraction_log.txt"


def format_table_row(values: list[str], widths: list[int], align: list[str] | None = None) -> str:
    """Formatera en tabellrad."""
    if align is None:
        align = ["<"] * len(values)

    cells = []
    for val, width, a in zip(values, widths, align):
        if a == ">":
            cells.append(f"{val:>{width}}")
        else:
            cells.append(f"{val:<{width}}")

    return "| " + " | ".join(cells) + " |"


def format_table_separator(widths: list[int]) -> str:
    """Formatera en tabellseparator."""
    parts = ["-" * (w + 2) for w in widths]
    return "+" + "+".join(parts) + "+"


def get_period_counts(client, period_id: str) -> dict:
    """
    Hämta antal tabeller, sektioner och grafer för en period.

    OBS: Denna funktion gör 3 DB-anrop per period.
    För batch-hämtning, använd get_period_counts_batch() istället.
    """
    tables = client.table("report_tables").select("id", count="exact").eq("period_id", period_id).execute()
    sections = client.table("sections").select("id", count="exact").eq("period_id", period_id).execute()

    # Charts kan saknas i äldre databaser
    try:
        charts = client.table("charts").select("id", count="exact").eq("period_id", period_id).execute()
        charts_count = charts.count or 0
    except Exception:
        charts_count = 0

    return {
        "tables": tables.count or 0,
        "sections": sections.count or 0,
        "charts": charts_count,
    }


def get_period_counts_batch(client, period_ids: list[str]) -> dict[str, dict]:
    """
    Hämta antal tabeller, sektioner och grafer för FLERA perioder effektivt.

    Gör 3 queries totalt istället för 3 * N queries.

    Args:
        client: Supabase-klient
        period_ids: Lista med period UUIDs

    Returns:
        Dict med {period_id: {"tables": X, "sections": Y, "charts": Z}}
    """
    if not period_ids:
        return {}

    # Initiera resultat
    result = {pid: {"tables": 0, "sections": 0, "charts": 0} for pid in period_ids}

    # Hämta tabeller grupperade per period
    tables = client.table("report_tables").select(
        "period_id"
    ).in_("period_id", period_ids).execute()

    for t in (tables.data or []):
        pid = t["period_id"]
        if pid in result:
            result[pid]["tables"] += 1

    # Hämta sektioner grupperade per period
    sections = client.table("sections").select(
        "period_id"
    ).in_("period_id", period_ids).execute()

    for s in (sections.data or []):
        pid = s["period_id"]
        if pid in result:
            result[pid]["sections"] += 1

    # Hämta grafer grupperade per period
    try:
        charts = client.table("charts").select(
            "period_id"
        ).in_("period_id", period_ids).execute()

        for c in (charts.data or []):
            pid = c["period_id"]
            if pid in result:
                result[pid]["charts"] += 1
    except Exception:
        pass  # charts-tabell kanske inte finns

    return result


def get_total_counts_from_db(client, company_id: str) -> dict:
    """Hämta totalt antal tabeller, sektioner och grafer för ett bolag direkt från DB."""
    # Hämta alla period_ids för bolaget
    periods = client.table("periods").select("id").eq("company_id", company_id).execute()
    period_ids = [p["id"] for p in periods.data] if periods.data else []

    if not period_ids:
        return {"tables": 0, "sections": 0, "charts": 0}

    # Räkna totalt för alla perioder
    tables = client.table("report_tables").select("id", count="exact").in_("period_id", period_ids).execute()
    sections = client.table("sections").select("id", count="exact").in_("period_id", period_ids).execute()

    try:
        charts = client.table("charts").select("id", count="exact").in_("period_id", period_ids).execute()
        charts_count = charts.count or 0
    except Exception:
        charts_count = 0

    return {
        "tables": tables.count or 0,
        "sections": sections.count or 0,
        "charts": charts_count,
    }


def get_embedding_stats(client, company_id: str) -> dict:
    """
    Hämta statistik om embeddings för ett bolag.

    Returns:
        Dict med sections_total, sections_with_embedding, embedding_model
    """
    from supabase_client import VOYAGE_MODEL

    # Hämta alla period_ids för bolaget
    periods = client.table("periods").select("id").eq("company_id", company_id).execute()
    period_ids = [p["id"] for p in periods.data] if periods.data else []

    if not period_ids:
        return {
            "sections_total": 0,
            "sections_with_embedding": 0,
            "embedding_model": VOYAGE_MODEL,
        }

    # Räkna totalt antal sections
    sections_total = client.table("sections").select("id", count="exact").in_("period_id", period_ids).execute()

    # Räkna sections med embedding (not null)
    sections_with_emb = client.table("sections").select("id", count="exact").in_("period_id", period_ids).not_.is_("embedding", "null").execute()

    return {
        "sections_total": sections_total.count or 0,
        "sections_with_embedding": sections_with_emb.count or 0,
        "embedding_model": VOYAGE_MODEL,
    }


def get_status_counts(report_data: dict) -> dict:
    """
    Beräkna extraherade/hittade för tabeller, sektioner och grafer.

    Baserat på:
    - pass1_counts: antal som hittades i pass 1 (identifierades i PDF)
    - Antal i databasen: antal som faktiskt extraherades
    """
    extraction_meta = report_data.get("extraction_meta") or {}
    pass1_counts = extraction_meta.get("pass1_counts")

    # Antal extraherade (finns i databasen)
    tables_extracted = report_data["tables"]
    sections_extracted = report_data["sections"]
    charts_extracted = report_data["charts"]

    # Antal hittade i pass 1 (identifierades i PDF)
    # Om pass1_counts saknas (äldre data), sätt None för att indikera okänt
    if pass1_counts:
        tables_found = pass1_counts.get("tables")
        sections_found = pass1_counts.get("sections")
        charts_found = pass1_counts.get("charts")
    else:
        tables_found = None
        sections_found = None
        charts_found = None

    return {
        "tables_extracted": tables_extracted,
        "tables_found": tables_found,
        "sections_extracted": sections_extracted,
        "sections_found": sections_found,
        "charts_extracted": charts_extracted,
        "charts_found": charts_found,
    }


def classify_error_severity(error_type: str) -> str:
    """
    Klassificera ett fel som Kritiskt, Medel eller Lag.
    """
    # Kritiska fel - data saknas helt
    if error_type in ("missing_table", "empty_table", "values_length_mismatch"):
        return "Kritiskt"

    # Medelfel - data kan vara inkomplett
    if error_type in ("invalid_label",):
        return "Medel"

    # Låga fel - kosmetiska eller minor issues
    if error_type in ("first_value_not_null", "missing_title", "empty_content"):
        return "Lag"

    return "Medel"  # Default


def collect_all_errors(report_data: list[dict], company_name: str) -> list[dict]:
    """
    Samla alla fel från alla rapporter i en lista.

    Inkluderar:
    - Saknade tabeller (hittades i pass 1 men extraherades inte)
    - Tabeller med valideringsfel (extraherades men har problem)
    - Sektionsvarningar

    Returns:
        Lista med dict: {rapport, beskrivning, bedomning}
    """
    all_errors = []

    for r in report_data:
        extraction_meta = r.get("extraction_meta") or {}
        validation = extraction_meta.get("validation", {})

        rapport = f"Q{r['quarter']} {r['year']} {company_name}"

        # 1. Saknade tabeller (hittades i pass 1 men extraherades inte)
        missing_tables = extraction_meta.get("missing_tables", [])
        for mt in missing_tables:
            all_errors.append({
                "rapport": rapport,
                "beskrivning": f"SAKNAD: '{mt.get('table_title', '?')}' (sida {mt.get('page', '?')})",
                "bedomning": classify_error_severity("missing_table"),
            })

        # 2. Tabeller med valideringsfel (extraherades men har problem)
        table_val = validation.get("tables", {})
        for err in table_val.get("errors", []):
            all_errors.append({
                "rapport": rapport,
                "beskrivning": f"FEL: '{err.get('table_title', '?')}' - {err.get('message', '?')}",
                "bedomning": classify_error_severity(err.get("error_type", "")),
            })

        # 3. Sektionsvarningar
        section_val = validation.get("sections", {})
        for warn in section_val.get("warnings", []):
            all_errors.append({
                "rapport": rapport,
                "beskrivning": f"VARNING: Sektion '{warn.get('section_title', '?')}' - {warn.get('message', '?')}",
                "bedomning": classify_error_severity(warn.get("warning_type", "")),
            })

    return all_errors


def update_extraction_log(company_slug: str, base_folder: Path | str) -> int:
    """
    Uppdatera loggfilen för ett bolag med data från databasen.

    Args:
        company_slug: Bolagets slug (t.ex. 'vitrolife')
        base_folder: Basmappen där bolagsmapparna finns (t.ex. 'alla_rapporter')

    Returns:
        Antal rapporter som loggades
    """
    base_folder = Path(base_folder)
    company_folder = base_folder / company_slug

    if not company_folder.exists():
        print(f"[!] Bolagsmappen hittades inte: {company_folder}")
        return 0

    # Hämta bolag från databasen
    company = get_company_by_slug(company_slug)
    if not company:
        print(f"[!] Bolaget finns inte i databasen: {company_slug}")
        return 0

    company_id = company["id"]
    company_name = company["name"]

    # Hämta alla perioder med extraction_meta och id
    client = get_client()
    periods = client.table("periods").select(
        "id, quarter, year, source_file, extraction_meta, created_at"
    ).eq("company_id", company_id).order("year", desc=True).order("quarter", desc=True).execute()

    if not periods.data:
        print(f"[!] Inga rapporter i databasen för: {company_name}")
        return 0

    # Hämta räkningar för ALLA perioder i en batch (3 queries istället för 3*N)
    period_ids = [p["id"] for p in periods.data]
    all_counts = get_period_counts_batch(client, period_ids)

    # Bygg upp data med batch-räkningar
    report_data = []
    for period in periods.data:
        counts = all_counts.get(period["id"], {"tables": 0, "sections": 0, "charts": 0})
        extraction_meta = period.get("extraction_meta") or {}

        report_data.append({
            "period": f"Q{period['quarter']} {period['year']}",
            "quarter": period["quarter"],
            "year": period["year"],
            "tables": counts["tables"],
            "sections": counts["sections"],
            "charts": counts["charts"],
            "cost": extraction_meta.get("total_cost_sek", 0),
            "time": extraction_meta.get("total_elapsed_seconds", 0),
            "source_file": period.get("source_file", ""),
            "extraction_meta": extraction_meta,
            "created_at": period.get("created_at", ""),
        })

    # Beräkna totaler
    total_reports = len(report_data)
    total_tables = sum(r["tables"] for r in report_data)
    total_sections = sum(r["sections"] for r in report_data)
    total_charts = sum(r["charts"] for r in report_data)
    total_cost = sum(r["cost"] for r in report_data)
    total_time = sum(r["time"] for r in report_data)

    # Skriv loggfil
    log_path = get_extraction_log_path(company_folder)

    with open(log_path, "w", encoding="utf-8") as f:
        # Header
        f.write("#" * 80 + "\n")
        f.write(f"# EXTRAKTIONSLOGG: {company_name.upper()}\n")
        f.write(f"# Genererad: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("#" * 80 + "\n\n")

        # Sammanfattning
        f.write("SAMMANFATTNING:\n")
        f.write(f"  Rapporter: {total_reports}\n")
        f.write(f"  Tabeller: {total_tables} | Sektioner: {total_sections} | Grafer: {total_charts}\n")
        f.write(f"  Kostnad: {total_cost:.2f} SEK | Tid: {total_time:.1f} sekunder\n\n")

        # ===== TABELL 1: ÖVERSIKT =====
        widths_overview = [9, 8, 9, 6, 10, 8]
        align_overview = ["<", ">", ">", ">", ">", ">"]

        f.write("RAPPORTER - OVERSIKT:\n")
        f.write(format_table_separator(widths_overview) + "\n")
        f.write(format_table_row(
            ["Period", "Tabeller", "Sektioner", "Grafer", "Kostnad", "Tid (s)"],
            widths_overview, align_overview
        ) + "\n")
        f.write(format_table_separator(widths_overview) + "\n")

        for r in report_data:
            row = [
                r["period"],
                str(r["tables"]),
                str(r["sections"]),
                str(r["charts"]),
                f"{r['cost']:.2f}",
                f"{r['time']:.1f}",
            ]
            f.write(format_table_row(row, widths_overview, align_overview) + "\n")

        # Totalrad
        f.write(format_table_separator(widths_overview) + "\n")
        total_row_overview = [
            "TOTALT",
            str(total_tables),
            str(total_sections),
            str(total_charts),
            f"{total_cost:.2f}",
            f"{total_time:.1f}",
        ]
        f.write(format_table_row(total_row_overview, widths_overview, align_overview) + "\n")
        f.write(format_table_separator(widths_overview) + "\n")

        # ===== TABELL 2: STATUS (extraherade/hittade) =====
        f.write("\n\nRAPPORTER - STATUS (extraherade/hittade):\n")
        widths_status = [9, 12, 14, 12]
        align_status = ["<", ">", ">", ">"]

        f.write(format_table_separator(widths_status) + "\n")
        f.write(format_table_row(
            ["Period", "Tabeller", "Sektioner", "Grafer"],
            widths_status, align_status
        ) + "\n")
        f.write(format_table_separator(widths_status) + "\n")

        # Räkna totaler för status
        total_tables_extracted = 0
        total_tables_found = 0
        total_sections_extracted = 0
        total_sections_found = 0
        total_charts_extracted = 0
        total_charts_found = 0
        has_pass1_data = False

        def format_status(extracted: int, found: int | None) -> str:
            """Formatera status som 'X/Y' eller 'X/?' om found är okänt."""
            if found is None:
                return f"{extracted}/?"
            return f"{extracted}/{found}"

        for r in report_data:
            status = get_status_counts(r)
            total_tables_extracted += status["tables_extracted"]
            total_sections_extracted += status["sections_extracted"]
            total_charts_extracted += status["charts_extracted"]

            # Summera found endast om vi har data
            if status["tables_found"] is not None:
                total_tables_found += status["tables_found"]
                has_pass1_data = True
            if status["sections_found"] is not None:
                total_sections_found += status["sections_found"]
            if status["charts_found"] is not None:
                total_charts_found += status["charts_found"]

            row = [
                r["period"],
                format_status(status["tables_extracted"], status["tables_found"]),
                format_status(status["sections_extracted"], status["sections_found"]),
                format_status(status["charts_extracted"], status["charts_found"]),
            ]
            f.write(format_table_row(row, widths_status, align_status) + "\n")

        # Totalrad för status
        f.write(format_table_separator(widths_status) + "\n")
        total_row_status = [
            "TOTALT",
            format_status(total_tables_extracted, total_tables_found if has_pass1_data else None),
            format_status(total_sections_extracted, total_sections_found if has_pass1_data else None),
            format_status(total_charts_extracted, total_charts_found if has_pass1_data else None),
        ]
        f.write(format_table_row(total_row_status, widths_status, align_status) + "\n")
        f.write(format_table_separator(widths_status) + "\n")

        # ===== FELLISTA =====
        all_errors = collect_all_errors(report_data, company_name)

        if all_errors:
            f.write("\n\nFEL OCH VARNINGAR:\n")
            widths_errors = [22, 55, 10]
            align_errors = ["<", "<", "<"]

            f.write(format_table_separator(widths_errors) + "\n")
            f.write(format_table_row(
                ["Rapport", "Beskrivning", "Bedomning"],
                widths_errors, align_errors
            ) + "\n")
            f.write(format_table_separator(widths_errors) + "\n")

            for err in all_errors:
                # Trunkera beskrivning om den är för lång
                beskrivning = err["beskrivning"]
                if len(beskrivning) > 55:
                    beskrivning = beskrivning[:52] + "..."

                row = [
                    err["rapport"][:22],
                    beskrivning,
                    err["bedomning"],
                ]
                f.write(format_table_row(row, widths_errors, align_errors) + "\n")

            f.write(format_table_separator(widths_errors) + "\n")
        else:
            f.write("\n\nINGA FEL REGISTRERADE.\n")

        # ===== VERIFIERING MOT DATABAS =====
        db_counts = get_total_counts_from_db(client, company_id)

        f.write("\n\nVERIFIERING (logg vs databas):\n")

        checks_passed = True
        if total_tables != db_counts["tables"]:
            f.write(f"  [AVVIKELSE] Tabeller: logg={total_tables}, databas={db_counts['tables']}\n")
            checks_passed = False
        if total_sections != db_counts["sections"]:
            f.write(f"  [AVVIKELSE] Sektioner: logg={total_sections}, databas={db_counts['sections']}\n")
            checks_passed = False
        if total_charts != db_counts["charts"]:
            f.write(f"  [AVVIKELSE] Grafer: logg={total_charts}, databas={db_counts['charts']}\n")
            checks_passed = False

        if checks_passed:
            f.write(f"  [OK] Tabeller: {total_tables} | Sektioner: {total_sections} | Grafer: {total_charts}\n")

        # ===== EMBEDDING-STATUS =====
        emb_stats = get_embedding_stats(client, company_id)

        f.write(f"\nEMBEDDINGS (modell: {emb_stats['embedding_model']}):\n")
        if emb_stats["sections_total"] == 0:
            f.write("  Inga sektioner att generera embeddings for.\n")
        elif emb_stats["sections_with_embedding"] == emb_stats["sections_total"]:
            f.write(f"  [OK] {emb_stats['sections_with_embedding']}/{emb_stats['sections_total']} sektioner har embeddings\n")
        else:
            missing = emb_stats["sections_total"] - emb_stats["sections_with_embedding"]
            f.write(f"  [SAKNAS] {emb_stats['sections_with_embedding']}/{emb_stats['sections_total']} sektioner har embeddings ({missing} saknas)\n")

    print(f"[OK] Logg uppdaterad: {log_path}")

    # Synkronisera filer - tvåvägssynk med databasen
    sync_result = sync_files_with_database(company_slug, base_folder)
    if sync_result["moved_to_db"] > 0:
        print(f"[OK] Flyttade {sync_result['moved_to_db']} fil(er) till ligger_i_databasen/")
    if sync_result["moved_to_extract"] > 0:
        print(f"[OK] Flyttade tillbaka {sync_result['moved_to_extract']} fil(er) till skall_extractas/")

    return len(periods.data)


def move_file_after_extraction(
    source_path: Path | str,
    company_slug: str,
    base_folder: Path | str
) -> Path | None:
    """
    Flytta en PDF från skall_extractas till ligger_i_databasen.

    Args:
        source_path: Sökväg till PDF:en som extraherats
        company_slug: Bolagets slug
        base_folder: Basmappen (t.ex. 'alla_rapporter')

    Returns:
        Ny sökväg om flytten lyckades, annars None
    """
    source_path = Path(source_path)
    base_folder = Path(base_folder)

    if not source_path.exists():
        print(f"[!] Filen hittades inte: {source_path}")
        return None

    # Bestäm målmapp
    company_folder = base_folder / company_slug
    target_folder = company_folder / "ligger_i_databasen"
    target_folder.mkdir(parents=True, exist_ok=True)

    target_path = target_folder / source_path.name

    # Kolla om filen redan ligger rätt
    if source_path.parent == target_folder:
        return source_path

    # Flytta filen
    try:
        shutil.move(str(source_path), str(target_path))
        print(f"[OK] Flyttade: {source_path.name} -> ligger_i_databasen/")
        return target_path
    except Exception as e:
        print(f"[!] Kunde inte flytta fil: {e}")
        return None


def sync_files_with_database(company_slug: str, base_folder: Path | str) -> dict:
    """
    Tvåvägssynkronisering av filer med databasen.

    - Filer i skall_extractas som finns i DB → flyttas till ligger_i_databasen
    - Filer i ligger_i_databasen som INTE finns i DB → flyttas tillbaka till skall_extractas

    Args:
        company_slug: Bolagets slug (t.ex. 'vitrolife')
        base_folder: Basmappen där bolagsmapparna finns (t.ex. 'alla_rapporter')

    Returns:
        Dict med {moved_to_db: int, moved_to_extract: int, already_correct: int, not_in_db: int}
    """
    from supabase_client import get_pdf_hash

    base_folder = Path(base_folder)
    company_folder = base_folder / company_slug
    skall_extractas = company_folder / "skall_extractas"
    ligger_i_db = company_folder / "ligger_i_databasen"

    result = {"moved_to_db": 0, "moved_to_extract": 0, "already_correct": 0, "not_in_db": 0}

    # Skapa mappar om de inte finns
    skall_extractas.mkdir(parents=True, exist_ok=True)
    ligger_i_db.mkdir(parents=True, exist_ok=True)

    # Hämta bolag från databasen
    company = get_company_by_slug(company_slug)
    if not company:
        return result

    company_id = company["id"]

    # Hämta alla perioder med pdf_hash
    client = get_client()
    periods = client.table("periods").select(
        "id, pdf_hash, source_file"
    ).eq("company_id", company_id).execute()

    # Bygg set med alla pdf_hash som finns i databasen
    db_hashes = set()
    if periods.data:
        db_hashes = {p["pdf_hash"] for p in periods.data if p.get("pdf_hash")}

    # 1. Flytta filer från skall_extractas → ligger_i_databasen (om de finns i DB)
    for pdf_file in list(skall_extractas.glob("*.pdf")):
        try:
            file_hash = get_pdf_hash(str(pdf_file))

            if file_hash in db_hashes:
                # Filen finns i databasen - flytta den
                new_path = move_file_after_extraction(pdf_file, company_slug, base_folder)
                if new_path:
                    result["moved_to_db"] += 1
            else:
                result["not_in_db"] += 1
        except Exception as e:
            print(f"[!] Fel vid kontroll av {pdf_file.name}: {e}")

    # 2. Flytta filer från ligger_i_databasen → skall_extractas (om de INTE finns i DB)
    for pdf_file in list(ligger_i_db.glob("*.pdf")):
        try:
            file_hash = get_pdf_hash(str(pdf_file))

            if file_hash in db_hashes:
                # Filen finns i databasen - ligger rätt
                result["already_correct"] += 1
            else:
                # Filen finns INTE i databasen - flytta tillbaka
                target_path = skall_extractas / pdf_file.name
                shutil.move(str(pdf_file), str(target_path))
                print(f"[OK] Flyttade tillbaka: {pdf_file.name} -> skall_extractas/")
                result["moved_to_extract"] += 1
        except Exception as e:
            print(f"[!] Fel vid kontroll av {pdf_file.name}: {e}")

    return result


def process_extraction_complete(
    pdf_path: str,
    company_name: str,
    base_folder: str | Path | None = None
) -> bool:
    """
    Kör efter lyckad extraktion: flytta fil och uppdatera logg.

    Args:
        pdf_path: Sökväg till extraherad PDF
        company_name: Bolagsnamn
        base_folder: Basmappen för rapporter (auto-detekteras om None)

    Returns:
        True om allt lyckades
    """
    pdf_path = Path(pdf_path)
    company_slug = slugify(company_name)

    # Auto-detektera base_folder från pdf_path
    if base_folder is None:
        # Förväntar sig struktur: base_folder/company/skall_extractas/fil.pdf
        # eller: base_folder/company/fil.pdf
        parent = pdf_path.parent
        if parent.name in ("skall_extractas", "ligger_i_databasen"):
            base_folder = parent.parent.parent
        else:
            base_folder = parent.parent

    base_folder = Path(base_folder)

    # Flytta fil
    new_path = move_file_after_extraction(pdf_path, company_slug, base_folder)

    # Uppdatera logg
    update_extraction_log(company_slug, base_folder)

    return new_path is not None


def regenerate_all_logs(base_folder: str | Path) -> dict:
    """
    Regenerera loggfiler för alla bolag i en mapp.

    Args:
        base_folder: Basmappen med bolagsmappar

    Returns:
        Dict med {company_slug: antal_rapporter}
    """
    base_folder = Path(base_folder)
    results = {}

    for company_folder in sorted(base_folder.iterdir()):
        if not company_folder.is_dir():
            continue
        if company_folder.name.startswith(".") or company_folder.name == "__pycache__":
            continue

        company_slug = company_folder.name
        count = update_extraction_log(company_slug, base_folder)
        results[company_slug] = count

    # Skapa summeringslogg
    create_summary_log(base_folder)

    return results


def create_summary_log(base_folder: str | Path) -> None:
    """
    Skapa en summeringslogg för alla bolag i databasen.

    Args:
        base_folder: Basmappen med bolagsmappar (t.ex. 'alla_rapporter')
    """
    base_folder = Path(base_folder)
    client = get_client()

    # Hämta alla bolag från databasen
    companies = client.table("companies").select("id, name, slug").execute()

    if not companies.data:
        print("[!] Inga bolag i databasen")
        return

    # Samla data per bolag
    company_data = []
    total_reports = 0
    total_tables = 0
    total_sections = 0
    total_charts = 0
    total_cost = 0.0
    total_time = 0.0

    for company in sorted(companies.data, key=lambda c: c["name"]):
        company_id = company["id"]
        company_name = company["name"]
        company_slug = company["slug"]

        # Hämta perioder för bolaget
        periods = client.table("periods").select(
            "id, extraction_meta"
        ).eq("company_id", company_id).execute()

        num_reports = len(periods.data) if periods.data else 0

        # Hämta räkningar
        db_counts = get_total_counts_from_db(client, company_id)

        # Summera kostnad och tid från extraction_meta
        cost = 0.0
        time_s = 0.0
        for p in (periods.data or []):
            meta = p.get("extraction_meta") or {}
            cost += meta.get("total_cost_sek", 0) or 0
            time_s += meta.get("total_elapsed_seconds", 0) or 0

        company_data.append({
            "name": company_name,
            "slug": company_slug,
            "reports": num_reports,
            "tables": db_counts["tables"],
            "sections": db_counts["sections"],
            "charts": db_counts["charts"],
            "cost": cost,
            "time": time_s,
        })

        total_reports += num_reports
        total_tables += db_counts["tables"]
        total_sections += db_counts["sections"]
        total_charts += db_counts["charts"]
        total_cost += cost
        total_time += time_s

    # Skriv summeringslogg
    log_path = base_folder / "SUMMARY_LOG.txt"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("#" * 80 + "\n")
        f.write("# SUMMERINGSLOGG - ALLA BOLAG\n")
        f.write(f"# Genererad: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("#" * 80 + "\n\n")

        # Sammanfattning
        f.write("SAMMANFATTNING:\n")
        f.write(f"  Bolag: {len(company_data)}\n")
        f.write(f"  Rapporter: {total_reports}\n")
        f.write(f"  Tabeller: {total_tables} | Sektioner: {total_sections} | Grafer: {total_charts}\n")
        f.write(f"  Kostnad: {total_cost:.2f} SEK | Tid: {total_time:.1f} sekunder\n\n")

        # Tabell per bolag
        widths = [20, 10, 10, 10, 8, 12, 10]
        align = ["<", ">", ">", ">", ">", ">", ">"]

        f.write("PER BOLAG:\n")
        f.write(format_table_separator(widths) + "\n")
        f.write(format_table_row(
            ["Bolag", "Rapporter", "Tabeller", "Sektioner", "Grafer", "Kostnad", "Tid (s)"],
            widths, align
        ) + "\n")
        f.write(format_table_separator(widths) + "\n")

        for c in company_data:
            row = [
                c["name"][:20],
                str(c["reports"]),
                str(c["tables"]),
                str(c["sections"]),
                str(c["charts"]),
                f"{c['cost']:.2f}",
                f"{c['time']:.1f}",
            ]
            f.write(format_table_row(row, widths, align) + "\n")

        # Totalrad
        f.write(format_table_separator(widths) + "\n")
        f.write(format_table_row(
            ["TOTALT", str(total_reports), str(total_tables), str(total_sections),
             str(total_charts), f"{total_cost:.2f}", f"{total_time:.1f}"],
            widths, align
        ) + "\n")
        f.write(format_table_separator(widths) + "\n")

        # Verifiering mot databas
        f.write("\n\nVERIFIERING (direkt fran databas):\n")

        # Hämta totaler direkt från tabellerna
        all_tables = client.table("report_tables").select("id", count="exact").execute()
        all_sections = client.table("sections").select("id", count="exact").execute()
        try:
            all_charts = client.table("charts").select("id", count="exact").execute()
            db_charts = all_charts.count or 0
        except Exception:
            db_charts = 0

        db_tables = all_tables.count or 0
        db_sections = all_sections.count or 0

        checks_passed = True
        if total_tables != db_tables:
            f.write(f"  [AVVIKELSE] Tabeller: summerat={total_tables}, databas={db_tables}\n")
            checks_passed = False
        if total_sections != db_sections:
            f.write(f"  [AVVIKELSE] Sektioner: summerat={total_sections}, databas={db_sections}\n")
            checks_passed = False
        if total_charts != db_charts:
            f.write(f"  [AVVIKELSE] Grafer: summerat={total_charts}, databas={db_charts}\n")
            checks_passed = False

        if checks_passed:
            f.write(f"  [OK] Tabeller: {total_tables} | Sektioner: {total_sections} | Grafer: {total_charts}\n")

        # ===== EMBEDDING-STATUS =====
        from supabase_client import VOYAGE_MODEL

        # Räkna sections med embedding
        sections_with_emb = client.table("sections").select("id", count="exact").not_.is_("embedding", "null").execute()
        emb_count = sections_with_emb.count or 0

        f.write(f"\nEMBEDDINGS (modell: {VOYAGE_MODEL}):\n")
        if db_sections == 0:
            f.write("  Inga sektioner att generera embeddings for.\n")
        elif emb_count == db_sections:
            f.write(f"  [OK] {emb_count}/{db_sections} sektioner har embeddings\n")
        else:
            missing = db_sections - emb_count
            f.write(f"  [SAKNAS] {emb_count}/{db_sections} sektioner har embeddings ({missing} saknas)\n")

    print(f"[OK] Summeringslogg skapad: {log_path}")


# === CLI ===

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hantera extraktionsloggar")
    parser.add_argument("--regenerate", "-r", metavar="FOLDER",
                       help="Regenerera loggar för alla bolag i mappen")
    parser.add_argument("--company", "-c", metavar="SLUG",
                       help="Uppdatera logg för specifikt bolag")
    parser.add_argument("--base", "-b", metavar="FOLDER", default=".",
                       help="Basmapp (default: nuvarande)")
    parser.add_argument("--sync", "-s", action="store_true",
                       help="Synkronisera filer med databasen (flytta filer som redan finns i DB)")

    args = parser.parse_args()

    if args.regenerate:
        print(f"\nRegenererar loggar för: {args.regenerate}")
        results = regenerate_all_logs(args.regenerate)
        print(f"\nResultat:")
        for slug, count in results.items():
            print(f"  {slug}: {count} rapporter")
    elif args.company:
        if args.sync:
            # Bara synkronisera filer utan att uppdatera logg
            print(f"\nSynkroniserar filer för: {args.company}")
            result = sync_files_with_database(args.company, args.base)
            print(f"  Flyttade till ligger_i_databasen: {result['moved_to_db']}")
            print(f"  Flyttade till skall_extractas: {result['moved_to_extract']}")
            print(f"  Redan rätt: {result['already_correct']}")
            print(f"  Ej i DB (skall extraheras): {result['not_in_db']}")
        else:
            # Uppdatera logg (inkluderar synkronisering)
            update_extraction_log(args.company, args.base)
    else:
        parser.print_help()
