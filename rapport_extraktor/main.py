#!/usr/bin/env python3
"""
Finansiell Rapportextraktor - CLI

Extraherar finansiell data från PDF-kvartalsrapporter och
skapar professionella Excel-databöcker.

Användning:
    # Skapa ny databok från alla PDFs i en mapp
    python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx

    # Full extraktion - extrahera ALL text och alla tabeller
    python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx --full

    # Lägg till nya rapporter till befintlig databok
    python main.py --company "Freemelt" --add ny_rapport.pdf -o databok.xlsx

    # Generera Excel från databas (utan ny extraktion)
    python main.py --company "Freemelt" --from-db -o databok.xlsx

    # Lista alla bolag i databasen
    python main.py --list-companies
"""

import argparse
import asyncio
import gc
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from pipeline import extract_all_pdfs_multi_pass
from pipeline_mistral_v2 import extract_pdf_mistral_v2, get_mistral_client
from excel_builder import build_databook
from supabase_client import list_companies, get_or_create_company, slugify, check_database_setup, load_all_periods
from logger import setup_logger, get_logger
from checkpoint import (
    generate_batch_id,
    save_checkpoint,
    get_completed_files,
    add_completed_file,
    add_failed_file,
    get_batch_progress,
)

# Ladda miljövariabler
load_dotenv()


async def extract_all_pdfs_mistral(
    pdf_paths: list[str],
    company_name: str,
    progress_callback=None,
    use_cache: bool = True,
    base_folder: str | None = None,
    quiet: bool = False,
    resume: bool = False,
    batch_size: int = 5,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Wrapper för Mistral v2-pipelinen med checkpoint-stöd.
    Snabbare än v1 - kringgår 8-sidorsbegränsningen.

    Args:
        pdf_paths: Lista med PDF-sökvägar
        company_name: Bolagsnamn
        progress_callback: Callback för progress-uppdateringar
        use_cache: Använd cache om PDF redan extraherats
        base_folder: Basmapp för fillagring
        quiet: Undertryck utskrifter
        resume: Återuppta från checkpoint om True
        batch_size: Antal PDFs per batch
    """
    from supabase_client import get_or_create_company

    company = get_or_create_company(company_name)
    logger = get_logger('batch_mistral')

    # Setup logger om base_folder finns
    if base_folder:
        setup_logger(company_name, base_folder)

    # Checkpoint-hantering
    batch_id = generate_batch_id(company["id"])
    if resume:
        completed = get_completed_files(batch_id)
        original_count = len(pdf_paths)
        pdf_paths = [p for p in pdf_paths if str(p) not in completed]
        if original_count != len(pdf_paths):
            logger.info(f"[CHECKPOINT] Återupptar batch - hoppar över {original_count - len(pdf_paths)} redan extraherade")

    client = get_mistral_client()
    semaphore = asyncio.Semaphore(2)  # Max 2 parallella PDFs

    successful = []
    failed = []

    logger.info(f"[BATCH] Startar extraktion av {len(pdf_paths)} PDFs med Mistral")

    for i, pdf_path in enumerate(pdf_paths):
        try:
            result = await extract_pdf_mistral_v2(
                pdf_path=pdf_path,
                client=client,
                semaphore=semaphore,
                company_id=company["id"],
                company_name=company_name,
                progress_callback=progress_callback,
                use_cache=use_cache,
                base_folder=base_folder,
                quiet=quiet,
            )
            successful.append(result)
            add_completed_file(batch_id, str(pdf_path))
            logger.info(f"[BATCH] {i+1}/{len(pdf_paths)} klar: {Path(pdf_path).name}")
        except Exception as e:
            failed.append((pdf_path, str(e)))
            add_failed_file(batch_id, str(pdf_path), str(e))
            logger.error(f"[BATCH] {i+1}/{len(pdf_paths)} FEL: {Path(pdf_path).name} - {e}")
            if progress_callback:
                progress_callback(pdf_path, f"failed: {e}", None)

        # Minnesrensning var 5:e fil
        if (i + 1) % batch_size == 0:
            gc.collect()

    # Spara slutlig checkpoint
    save_checkpoint(batch_id, [str(p) for p in pdf_paths if any(r.get("_source_file") == str(p) for r in successful)],
                   [{"path": p, "error": e} for p, e in failed], len(pdf_paths))

    logger.info(f"[BATCH] Klart! {len(successful)} lyckade, {len(failed)} misslyckade")
    return successful, failed


# Token-priser (USD per 1M tokens)
HAIKU_INPUT_PRICE = 0.80   # $0.80 per 1M input tokens
HAIKU_OUTPUT_PRICE = 4.00  # $4.00 per 1M output tokens
SONNET_INPUT_PRICE = 3.00  # $3.00 per 1M input tokens
SONNET_OUTPUT_PRICE = 15.00  # $15.00 per 1M output tokens
USD_TO_SEK = 10.50   # Ungefärlig växelkurs


def calculate_cost(input_tokens: int, output_tokens: int, model: str = "sonnet") -> float:
    """Beräkna kostnad i SEK baserat på modell."""
    if model == "haiku":
        usd = (input_tokens * HAIKU_INPUT_PRICE + output_tokens * HAIKU_OUTPUT_PRICE) / 1_000_000
    else:
        usd = (input_tokens * SONNET_INPUT_PRICE + output_tokens * SONNET_OUTPUT_PRICE) / 1_000_000
    return usd * USD_TO_SEK


def get_databook_path(
    filename: str,
    company_name: str,
    base_folder: str | Path | None = None
) -> Path:
    """
    Beräkna sökväg för databok-fil i bolagets ligger_i_databasen-mapp.

    Args:
        filename: Filnamn för databoken (t.ex. "ABGSC Q2 25 - Q3 25.xlsx")
        company_name: Bolagsnamn (används för att hitta mappen)
        base_folder: Basmapp där alla_rapporter finns (default: alla_rapporter/)

    Returns:
        Path till där databoken ska sparas
    """
    if base_folder is None:
        # Anta att vi kör från rapport_extraktor, gå upp en nivå till alla_rapporter
        base_folder = Path(__file__).parent.parent / "alla_rapporter"
    else:
        base_folder = Path(base_folder)

    # Skapa slug från bolagsnamn
    company_slug = slugify(company_name)

    # Sökväg till ligger_i_databasen
    target_folder = base_folder / company_slug / "ligger_i_databasen"

    # Skapa mappen om den inte finns
    target_folder.mkdir(parents=True, exist_ok=True)

    return target_folder / filename


def print_pipeline_details(results: list[dict]):
    """Visa detaljerad timing och kostnad per pass for multi-pass extraktion."""
    for result in results:
        pipeline_info = result.get("_pipeline_info")
        if not pipeline_info:
            continue

        period = result.get("metadata", {}).get("period", "?")
        print(f"\n[i] {period} - Pipeline detaljer:")
        print(f"   {'Pass':<8} {'Modell':<8} {'Tid':<8} {'Input':<10} {'Output':<10} {'Kostnad':<10}")
        print(f"   {'-'*54}")

        total_time = 0
        for p in pipeline_info.get("passes", []):
            pass_num = p.get("pass", "?")
            model = p.get("model", "?")
            elapsed = p.get("elapsed_seconds", 0)
            input_tok = p.get("input_tokens", 0)
            output_tok = p.get("output_tokens", 0)
            cost = p.get("cost_sek", 0)
            total_time += elapsed

            print(f"   Pass {pass_num:<3} {model:<8} {elapsed:>5.1f}s   {input_tok:>8,}   {output_tok:>8,}   {cost:>7.4f} kr")

        # Visa retry-statistik om det finns
        retry_stats = pipeline_info.get("retry_stats", {})
        if retry_stats.get("retry_count", 0) > 0:
            retry_time = retry_stats.get("elapsed_seconds", 0)
            retry_input = retry_stats.get("input_tokens", 0)
            retry_output = retry_stats.get("output_tokens", 0)
            retry_cost = retry_stats.get("cost_sek", 0)
            retry_count = retry_stats.get("retry_count", 0)
            total_time += retry_time
            print(f"   Retry({retry_count}) {'haiku':<8} {retry_time:>5.1f}s   {retry_input:>8,}   {retry_output:>8,}   {retry_cost:>7.4f} kr")

        total_cost = pipeline_info.get("total_cost_sek", 0)
        print(f"   {'-'*54}")
        print(f"   {'Totalt':<17} {total_time:>5.1f}s   {'':>8}   {'':>8}   {total_cost:>7.2f} kr")


def format_time(seconds: float) -> str:
    """Formatera sekunder till läsbar tid."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs}s"


def create_progress_tracker(pdf_paths: list[str]):
    """
    Skapa progress-callback för terminal-output med en rad per fil.
    Visar tokens, kostnad och tid för varje fil.
    Inkluderar bakgrundstimer som uppdaterar UI var 0.5 sekund.
    """
    import threading

    # Behåll ordning med lista av sökvägar
    path_order = [str(p) for p in pdf_paths]
    files = {str(p): {
        "name": Path(p).name,
        "status": "pending",
        "pass_info": None,  # "pass_1", "pass_2_3", "validating"
        "input": 0,
        "output": 0,
        "cost": 0.0,
        "start_time": None,
        "elapsed": 0,
    } for p in pdf_paths}

    state = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,
        "cached": 0,
        "failed": 0,
        "start_time": time.time(),
        "running": True,  # För att stoppa bakgrundstimern
    }

    def render():
        # Flytta cursor upp till första progress-raden
        num_lines = len(files) + 1  # +1 för total-rad
        sys.stdout.write(f"\033[{num_lines}A")

        for path in path_order:
            info = files[path]
            if info["status"] == "pending":
                icon = "[ ]"
                details = ""
            elif info["status"] == "extracting":
                icon = "[~]"
                elapsed = time.time() - info["start_time"] if info["start_time"] else 0
                # Visa aktuellt pass om tillgängligt
                pass_label = ""
                if info["pass_info"] == "pass_1":
                    pass_label = "Pass 1/3 (struktur) "
                elif info["pass_info"] == "pass_2_3":
                    pass_label = "Pass 2/3 (data) "
                elif info["pass_info"] == "ocr":
                    pass_label = "OCR (1/2) "
                elif info["pass_info"] == "llm":
                    pass_label = "LLM (2/2) "
                elif info["pass_info"] == "validating":
                    pass_label = "Validerar "
                details = f"{pass_label}{format_time(elapsed)}"
            elif info["status"] == "cached":
                icon = "[C]"
                details = "(cachad)"
            elif info["status"] == "done":
                icon = "[X]"
                tokens = info["input"] + info["output"]
                cost = info["cost"]
                details = f"{tokens:,} tok | {cost:.2f} kr | {format_time(info['elapsed'])}"
            elif info["status"] == "failed":
                icon = "[!]"
                details = "fel"
            else:
                icon = "[?]"
                details = ""

            # Rensa ENDAST denna rad, skriv sedan innehåll
            sys.stdout.write(f"\033[2K{icon} {info['name']:<35} {details}\n")

        # Totalt - rensa endast denna rad
        total_tokens = state["total_input_tokens"] + state["total_output_tokens"]
        total_cost = state["total_cost"]
        elapsed = time.time() - state["start_time"]
        sys.stdout.write(f"\033[2K    Totalt: {total_tokens:,} tokens | {total_cost:.2f} kr | {format_time(elapsed)}\n")
        sys.stdout.flush()

    def on_progress(pdf_path: str, status: str, token_info: dict | None = None):
        path_key = str(pdf_path)
        if path_key not in files:
            return

        if status == "cached":
            files[path_key]["status"] = "cached"
            state["cached"] += 1
        elif status == "done":
            files[path_key]["status"] = "done"
            if files[path_key]["start_time"]:
                files[path_key]["elapsed"] = time.time() - files[path_key]["start_time"]
            if token_info:
                files[path_key]["input"] = token_info["input_tokens"]
                files[path_key]["output"] = token_info["output_tokens"]
                files[path_key]["cost"] = token_info.get("cost_sek", 0.0)
                state["total_input_tokens"] += token_info["input_tokens"]
                state["total_output_tokens"] += token_info["output_tokens"]
                state["total_cost"] += token_info.get("cost_sek", 0.0)
        elif status.startswith("failed"):
            files[path_key]["status"] = "failed"
            state["failed"] += 1
        elif status == "extracting":
            files[path_key]["status"] = "extracting"
            files[path_key]["start_time"] = time.time()
        elif status in ("pass_1", "pass_2_3", "validating"):
            # Uppdatera pass-info utan att ändra status
            files[path_key]["pass_info"] = status

        render()

    # Initial render - skapa plats för alla rader (files + 1 total-rad)
    for _ in range(len(files) + 1):
        print()
    render()

    # Bakgrundstimer för regelbundna uppdateringar
    def timer_loop():
        while state["running"]:
            time.sleep(0.5)
            if state["running"]:  # Kolla igen efter sleep
                # Bara rendera om något pågår
                any_extracting = any(f["status"] == "extracting" for f in files.values())
                if any_extracting:
                    render()

    timer_thread = threading.Thread(target=timer_loop, daemon=True)
    timer_thread.start()

    def stop_timer():
        state["running"] = False

    return on_progress, state, stop_timer


def guess_company_name(pdf_path: str) -> str:
    """Försök gissa bolagsnamn från filnamn."""
    filename = Path(pdf_path).stem.lower()
    # Ta bort vanliga suffix som q1, q2, 2024, 2025, etc.
    import re
    name = re.sub(r'[-_]?q\d[-_]?\d{4}', '', filename)
    name = re.sub(r'[-_]\d{4}', '', name)
    name = re.sub(r'[-_]', ' ', name).strip()
    # Kapitalisera första bokstaven i varje ord
    return name.title() if name else "Okänt"


def run_interactive_mode(pdf_path: str | None = None, model: str = "claude"):
    """
    Kör interaktivt läge med nytt flöde:
    1. START - Välj bolag från databasen
    2. Välj läge: Skapa databok (alla perioder) eller Extrahera kvartal
    3. Om kvartal - välj från lista eller extrahera ny PDF

    Args:
        pdf_path: Valfri PDF-fil att extrahera
        model: "claude" eller "mistral" för val av extraktionspipeline
    """
    import re
    from supabase_client import get_or_create_company, period_exists, get_pdf_hash, load_all_periods
    from extraction_log import sync_files_with_database

    # Verifiera databas först
    ok, message = check_database_setup()
    if not ok:
        print(message)
        sys.exit(1)

    # === START ===
    print(f"\n{'═' * 50}")
    print("                     START")
    print(f"{'═' * 50}")

    # Hämta alla bolag från databasen
    companies = list_companies()

    if not companies:
        print("\n❌ Inga bolag finns i databasen.")
        print("   Använd kommandoradsläge för att extrahera första rapporten:")
        print("   python main.py ./rapport.pdf --company 'Bolagsnamn' --full")
        return

    # Visa bolag att välja mellan
    print("\nVälj bolag:")
    for i, company in enumerate(companies, 1):
        # Hämta antal perioder för detta bolag
        periods = load_all_periods(company["id"])
        period_count = len(periods)
        period_names = [p.get("metadata", {}).get("period", "?") for p in periods]
        period_str = ", ".join(period_names) if period_names else "inga perioder"
        print(f"   {i}) {company['name']} ({period_str})")

    print(f"   {len(companies) + 1}) Lägg till nytt bolag")

    company_choice = input("\n> ").strip()

    # Hantera val
    try:
        choice_num = int(company_choice)
        if choice_num == len(companies) + 1:
            # Lägg till nytt bolag
            new_name = input("\nBolagsnamn: ").strip()
            if not new_name:
                print("❌ Inget namn angivet.")
                return
            company = get_or_create_company(new_name)
            company_name = new_name
            all_periods = []
        elif 1 <= choice_num <= len(companies):
            company = companies[choice_num - 1]
            company_name = company["name"]
            all_periods = load_all_periods(company["id"])
        else:
            print("❌ Ogiltigt val.")
            return
    except ValueError:
        print("❌ Ange ett nummer.")
        return

    # === SYNKRONISERA FILER I BAKGRUNDEN ===
    # Automatiskt flytta filer baserat på databasstatus
    company_slug = slugify(company_name)
    base_folder = Path(__file__).parent.parent / "alla_rapporter"
    if base_folder.exists():
        sync_result = sync_files_with_database(company_slug, str(base_folder))
        if sync_result["moved_to_db"] > 0:
            print(f"   [SYNC] Flyttade {sync_result['moved_to_db']} fil(er) till ligger_i_databasen/")
        if sync_result["moved_to_extract"] > 0:
            print(f"   [SYNC] Flyttade {sync_result['moved_to_extract']} fil(er) till skall_extractas/")

    # === VÄLJ LÄGE ===
    print(f"\n{'═' * 50}")
    print(f"  Bolag: {company_name}")
    print(f"{'═' * 50}")

    if not all_periods:
        print("\n[!] Inga perioder finns for detta bolag.")
        print("   Vill du extrahera en ny rapport?")
        extract_new = input("   [Y/n] > ").strip().upper()
        if extract_new == "N":
            return
        # Gå till extraktion
        mode_choice = "3"
    else:
        print("\nVad vill du gora?")
        print("   1) Skapa fullstandig databok (alla perioder)")
        print("   2) Skapa databok for ett specifikt kvartal")
        print("   3) Extrahera nytt kvartal fran PDF")
        print("   4) Batch-extraktion (mapp med flera PDFs)")
        mode_choice = input("\n> ").strip()

    # === VÄLJ PIPELINE (endast för extraktion) ===
    if mode_choice in ("3", "4"):
        print("\nVälj extraktionsmodell:")
        print("   1) Claude (Haiku + Sonnet + Haiku)")
        print("   2) Mistral (OCR + Pixtral) - Snabbast!")
        pipeline_choice = input("\n> ").strip()
        if pipeline_choice == "2":
            model = "mistral"
        else:
            model = "claude"

    # === LÄGE 1: FULLSTÄNDIG DATABOK ===
    if mode_choice == "1":
        data_to_export = all_periods
        period_names = [p.get("metadata", {}).get("period", "?") for p in all_periods]

        # Generera filnamn
        periods_sorted = sorted(
            data_to_export,
            key=lambda x: (
                int(re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")).group(1)) if re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")) else 0,
                int(re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")).group(1)) if re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")) else 0
            )
        )
        first_period = periods_sorted[0].get("metadata", {}).get("period", "")
        last_period = periods_sorted[-1].get("metadata", {}).get("period", "")
        first_short = re.sub(r'(\d{2})(\d{2})$', r'\2', first_period)
        last_short = re.sub(r'(\d{2})(\d{2})$', r'\2', last_period)
        default_output = f"{company_name} {first_short} - {last_short}.xlsx"

        output_input = input(f"\nOutput-fil (Enter för [{default_output}]): ").strip()
        output_filename = output_input if output_input else default_output

        # Spara databok i bolagets ligger_i_databasen-mapp
        output_path = get_databook_path(output_filename, company_name, base_folder)

        # Bygg Excel
        print("\n📊 Skapar databok...")
        normalize_tokens = build_databook(data_to_export, str(output_path))

        print(f"\n✅ Databok skapad: {output_path}")
        print(f"   Innehåller {len(data_to_export)} period(er): {', '.join(period_names)}")

        if normalize_tokens:
            norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
            print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")
        return

    # === LÄGE 2: SPECIFIKT KVARTAL ===
    elif mode_choice == "2":
        if not all_periods:
            print("\n❌ Inga perioder finns att välja.")
            return

        print("\nVälj kvartal:")
        # Sortera perioder kronologiskt
        periods_sorted = sorted(
            all_periods,
            key=lambda x: (
                int(re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")).group(1)) if re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")) else 0,
                int(re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")).group(1)) if re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")) else 0
            )
        )

        for i, period_data in enumerate(periods_sorted, 1):
            period_name = period_data.get("metadata", {}).get("period", "?")
            print(f"   {i}) {period_name}")

        period_choice = input("\n> ").strip()

        try:
            period_num = int(period_choice)
            if 1 <= period_num <= len(periods_sorted):
                selected_period = periods_sorted[period_num - 1]
                data_to_export = [selected_period]
                period_name = selected_period.get("metadata", {}).get("period", "")

                default_output = f"{company_name} {period_name}.xlsx"
                output_input = input(f"\nOutput-fil (Enter för [{default_output}]): ").strip()
                output_filename = output_input if output_input else default_output

                # Spara databok i bolagets ligger_i_databasen-mapp
                output_path = get_databook_path(output_filename, company_name, base_folder)

                # Bygg Excel
                print("\n📊 Skapar databok...")
                normalize_tokens = build_databook(data_to_export, str(output_path))

                print(f"\n✅ Databok skapad: {output_path}")
                print(f"   Innehåller: {period_name}")

                if normalize_tokens:
                    norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
                    print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")
            else:
                print("❌ Ogiltigt val.")
        except ValueError:
            print("❌ Ange ett nummer.")
        return

    # === LÄGE 3: EXTRAHERA NYTT KVARTAL ===
    elif mode_choice == "3":
        print(f"\n{'═' * 50}")
        print("                  EXTRAKTION")
        print(f"{'═' * 50}\n")

        # Fråga om PDF-sökväg
        if pdf_path:
            path = Path(pdf_path)
            print(f"PDF: {path.name}")
        else:
            pdf_input = input("Sökväg till PDF: ").strip()
            if not pdf_input:
                print("❌ Ingen sökväg angiven.")
                return
            path = Path(pdf_input)

        if not path.exists():
            print(f"❌ Fil hittades inte: {path}")
            return

        # Visa vilken pipeline som används
        if model == "mistral":
            print("\nAnvänder Mistral pipeline (OCR + Pixtral)")
        else:
            print("\nAnvänder Claude pipeline (Haiku + Sonnet + Haiku)")

        # === KONTROLLERA CACHE ===
        pdf_hash = get_pdf_hash(str(path))

        # Försök hitta period från filnamn (stöd både "q1-2025" och "2025-q1")
        period_match = re.search(r'[qQ](\d)[_-]?(\d{4})', path.stem)
        skip_extraction = False
        extracted_period = None
        quarter = None
        year = None

        if period_match:
            quarter = int(period_match.group(1))
            year = int(period_match.group(2))
        else:
            # Alternativt format: 2025-q1
            period_match = re.search(r'(\d{4})[_-]?[qQ](\d)', path.stem)
            if period_match:
                year = int(period_match.group(1))
                quarter = int(period_match.group(2))

        if quarter and year:
            extracted_period = f"Q{quarter} {year}"

            if period_exists(company["id"], quarter, year, pdf_hash):
                print(f"\nℹ️  Denna rapport finns redan i databasen ({extracted_period})")
                rerun = input("   Extrahera om? [y/N]: ").strip().upper()
                if rerun != "Y":
                    skip_extraction = True
                    print("   ✓ Använder befintlig data från databasen")

        # === KÖR EXTRAKTION ===
        extraction_cost = 0.0
        if not skip_extraction:
            print("\n📊 Startar extraktion...\n")

            # Automatisk streaming för stora filer
            pdf_size = path.stat().st_size
            use_streaming = pdf_size > 1_000_000

            on_progress, state, stop_timer = create_progress_tracker([str(path)])

            # Beräkna base_folder för filflyttning
            # Strukturen är: base_folder/company/skall_extractas/fil.pdf
            # eller: base_folder/company/fil.pdf
            if path.parent.name == "skall_extractas":
                base_folder = str(path.parent.parent.parent)
            else:
                base_folder = str(path.parent.parent)

            if model == "mistral":
                successful, failed = asyncio.run(
                    extract_all_pdfs_mistral(
                        [str(path)],
                        company_name,
                        on_progress,
                        use_cache=False,
                        base_folder=base_folder,
                        quiet=True,
                    )
                )
            else:
                successful, failed = asyncio.run(
                    extract_all_pdfs_multi_pass(
                        [str(path)],
                        company_name,
                        on_progress,
                        use_cache=False,
                        base_folder=base_folder,
                        quiet=True,
                    )
                )
            stop_timer()
            print()

            if successful:
                extracted_period = successful[0].get("metadata", {}).get("period", "?")
                print(f"\n✅ Extraktion klar! Data sparad till databasen.")
                print(f"   Bolag:  {company_name}")
                print(f"   Period: {extracted_period}")

                # Visa extraktionssammanfattning
                result = successful[0]
                tables = result.get("tables", [])
                sections = result.get("sections", [])
                charts = result.get("charts", [])
                print(f"\n   📊 Extraherat:")
                print(f"      Tabeller: {len(tables)} st")
                print(f"      Sektioner: {len(sections)} st")
                print(f"      Grafer: {len(charts)} st")

                # Visa valideringsinfo
                pipeline_info = result.get("_pipeline_info", {})
                validation = pipeline_info.get("validation", {})
                table_validation = validation.get("tables", {})
                if table_validation:
                    error_count = table_validation.get("error_count", 0)
                    warning_count = table_validation.get("warning_count", 0)
                    if error_count == 0:
                        print(f"      Validering: ✓ OK")
                    else:
                        print(f"      Validering: {error_count} fel")
                        for err in table_validation.get("errors", []):
                            print(f"         - {err.get('table_title', '?')}: {err.get('message', '?')}")

                # Visa retry-info
                retry_stats = pipeline_info.get("retry_stats", {})
                if retry_stats.get("retry_count", 0) > 0:
                    print(f"      Retry: {retry_stats.get('tables_retried', 0)} tabeller fixade")

                # Visa pipeline-detaljer
                print_pipeline_details(successful)
                # Hämta kostnad från pipeline_info
                extraction_cost = successful[0].get("_pipeline_info", {}).get("total_cost_sek", 0)
            else:
                print("\n❌ Extraktion misslyckades")
                for path_str, error in failed:
                    print(f"   {Path(path_str).name}: {error}")
                return

        # Fråga om databok
        print("\nVill du skapa en databok?")
        print("   1) Ja, endast detta kvartal")
        print("   2) Ja, fullständig databok (alla perioder)")
        print("   3) Nej")
        databok_choice = input("> ").strip()

        if databok_choice == "3":
            print("\n✓ Klar! Data finns sparad i databasen.")
            return

        # Ladda perioder på nytt
        all_periods = load_all_periods(company["id"])

        if databok_choice == "1":
            # Endast detta kvartal
            data_to_export = [p for p in all_periods
                             if p.get("metadata", {}).get("period") == extracted_period]
            default_output = f"{company_name} {extracted_period}.xlsx"
        else:
            # Fullständig databok
            data_to_export = all_periods
            periods_sorted = sorted(
                data_to_export,
                key=lambda x: (
                    int(re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")).group(1)) if re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")) else 0,
                    int(re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")).group(1)) if re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")) else 0
                )
            )
            first_period = periods_sorted[0].get("metadata", {}).get("period", "")
            last_period = periods_sorted[-1].get("metadata", {}).get("period", "")
            first_short = re.sub(r'(\d{2})(\d{2})$', r'\2', first_period)
            last_short = re.sub(r'(\d{2})(\d{2})$', r'\2', last_period)
            default_output = f"{company_name} {first_short} - {last_short}.xlsx"

        output_input = input(f"\nOutput-fil (Enter för [{default_output}]): ").strip()
        output_filename = output_input if output_input else default_output

        # Spara databok i bolagets ligger_i_databasen-mapp
        output_path = get_databook_path(output_filename, company_name, base_folder)

        # Bygg Excel
        print("\n📊 Skapar databok...")
        normalize_tokens = build_databook(data_to_export, str(output_path))

        print(f"\n✅ Databok skapad: {output_path}")
        print(f"   Innehåller {len(data_to_export)} period(er)")

        total_cost = extraction_cost
        if normalize_tokens:
            norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
            total_cost += norm_cost
            print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")

        if total_cost > 0:
            print(f"[$$] Total kostnad: {total_cost:.2f} kr")

    # === LAGE 4: BATCH-EXTRAKTION ===
    elif mode_choice == "4":
        print(f"\n{'=' * 50}")
        print("               BATCH-EXTRAKTION")
        print(f"{'=' * 50}\n")

        # Fraga om mappsokväg
        folder_input = input("Sokvag till mapp med PDF-filer: ").strip()
        if not folder_input:
            print("[!] Ingen sokvag angiven.")
            return

        folder_path = Path(folder_input)
        if not folder_path.exists():
            print(f"[!] Mappen hittades inte: {folder_path}")
            return

        if not folder_path.is_dir():
            print(f"[!] Sokvagen ar inte en mapp: {folder_path}")
            return

        # Hitta alla PDFs i mappen
        pdf_files = sorted(folder_path.glob("*.pdf"))
        if not pdf_files:
            print(f"[!] Inga PDF-filer hittades i {folder_path}")
            return

        pdf_path_strs = [str(p) for p in pdf_files]

        print(f"\nHittade {len(pdf_files)} PDF-fil(er):")
        for p in pdf_files[:10]:  # Visa max 10 filer
            print(f"   - {p.name}")
        if len(pdf_files) > 10:
            print(f"   ... och {len(pdf_files) - 10} till")

        # Bekrafta
        confirm = input(f"\nStarta batch-extraktion for {company_name}? [Y/n]: ").strip().upper()
        if confirm == "N":
            print("Avbruten.")
            return

        # Extraktion
        if model == "mistral":
            print("\n[~] Startar batch-extraktion (Mistral)...\n")
        else:
            print("\n[~] Startar batch-extraktion (Claude)...\n")

        on_progress, state, stop_timer = create_progress_tracker(pdf_path_strs)

        # Beräkna base_folder för filflyttning
        # Strukturen är: base_folder/company/skall_extractas/fil.pdf
        # eller: base_folder/company/fil.pdf
        if folder_path.name == "skall_extractas":
            base_folder = str(folder_path.parent.parent)
        else:
            base_folder = str(folder_path.parent)

        if model == "mistral":
            successful, failed = asyncio.run(
                extract_all_pdfs_mistral(
                    pdf_path_strs,
                    company_name,
                    on_progress,
                    use_cache=True,
                    base_folder=base_folder,
                    quiet=True,
                )
            )
        else:
            successful, failed = asyncio.run(
                extract_all_pdfs_multi_pass(
                    pdf_path_strs,
                    company_name,
                    on_progress,
                    use_cache=True,
                    base_folder=base_folder,
                    quiet=True,
                )
            )
        stop_timer()
        print()

        # Sammanfattning
        print(f"\n{'=' * 50}")
        print(f"[OK] Lyckades:  {len(successful)}")
        if state["cached"] > 0:
            print(f"[C]  Cachade:   {state['cached']} (0 kr)")
        if failed:
            print(f"[!]  Fel:       {len(failed)}")
            print("\nMisslyckade filer:")
            for path_str, error in failed:
                print(f"   - {Path(path_str).name}: {error}")

        # Visa extraktionssammanfattning per fil
        if successful:
            print(f"\n📊 Extraktionsresultat:")
            for result in successful:
                period = result.get("metadata", {}).get("period", "?")
                tables = result.get("tables", [])
                sections = result.get("sections", [])
                pipeline_info = result.get("_pipeline_info", {})
                validation = pipeline_info.get("validation", {}).get("tables", {})
                error_count = validation.get("error_count", 0)
                retry_stats = pipeline_info.get("retry_stats", {})

                status = "✓" if error_count == 0 else f"{error_count} fel"
                retry_info = f" (retry: {retry_stats.get('tables_retried', 0)} fixade)" if retry_stats.get("retry_count", 0) > 0 else ""
                print(f"   {period}: {len(tables)} tabeller, {len(sections)} sektioner - {status}{retry_info}")

        # Visa pipeline-detaljer
        if successful:
            print_pipeline_details(successful)

        # Kostnadssammanfattning - summera fran pipeline_info (korrekt per modell)
        if successful:
            total_cost = sum(
                r.get("_pipeline_info", {}).get("total_cost_sek", 0)
                for r in successful
            )
            total_tokens = state["total_input_tokens"] + state["total_output_tokens"]
            print(f"\n[$$] Total kostnad: {total_cost:.2f} kr ({total_tokens:,} tokens)")

        # Fraga om databok
        if successful:
            print("\nVill du skapa en databok?")
            print("   1) Ja, fullstandig databok (alla perioder)")
            print("   2) Nej")
            databok_choice = input("> ").strip()

            if databok_choice == "1":
                all_periods_updated = load_all_periods(company["id"])
                periods_sorted = sorted(
                    all_periods_updated,
                    key=lambda x: (
                        int(re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")).group(1)) if re.search(r'(\d{4})', x.get("metadata", {}).get("period", "0")) else 0,
                        int(re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")).group(1)) if re.search(r'Q(\d)', x.get("metadata", {}).get("period", "Q0")) else 0
                    )
                )
                first_period = periods_sorted[0].get("metadata", {}).get("period", "")
                last_period = periods_sorted[-1].get("metadata", {}).get("period", "")
                first_short = re.sub(r'(\d{2})(\d{2})$', r'\2', first_period)
                last_short = re.sub(r'(\d{2})(\d{2})$', r'\2', last_period)
                default_output = f"{company_name} {first_short} - {last_short}.xlsx"

                output_input = input(f"\nOutput-fil (Enter for [{default_output}]): ").strip()
                output_filename = output_input if output_input else default_output

                # Spara databok i bolagets ligger_i_databasen-mapp
                output_path = get_databook_path(output_filename, company_name, base_folder)

                print("\n[~] Skapar databok...")
                normalize_tokens = build_databook(all_periods_updated, str(output_path))

                print(f"\n[OK] Databok skapad: {output_path}")
                print(f"   Innehaller {len(all_periods_updated)} period(er)")

                if normalize_tokens:
                    norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
                    print(f"\n[$$] Normaliseringskostnad: {norm_cost:.2f} kr")

    else:
        print("[!] Ogiltigt val.")


def main():
    parser = argparse.ArgumentParser(
        description="Extrahera finansiell data från PDF-rapporter till Excel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exempel:
  python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx
  python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx --full
  python main.py --company "Freemelt" --add q4_rapport.pdf -o databok.xlsx
  python main.py --company "Freemelt" --from-db -o databok.xlsx
  python main.py --list-companies
        """
    )

    # Skapa ny databok
    parser.add_argument(
        "pdf_dir",
        nargs="?",
        help="Mapp med PDF-rapporter"
    )
    parser.add_argument(
        "--output", "-o",
        default="databok.xlsx",
        help="Output Excel-fil (default: databok.xlsx)"
    )

    # Bolag (obligatoriskt för extraktion)
    parser.add_argument(
        "--company", "-c",
        help="Bolagsnamn för datalagring i Supabase"
    )

    # Lägg till nya rapporter
    parser.add_argument(
        "--add",
        nargs="+",
        metavar="PDF",
        help="PDF-filer att lägga till"
    )

    # Generera från databas
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Generera Excel från databas utan ny extraktion"
    )

    # Lista bolag
    parser.add_argument(
        "--list-companies",
        action="store_true",
        help="Lista alla bolag i databasen"
    )

    # Databassetup
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Verifiera att databasen är korrekt uppsatt"
    )

    # Övriga flaggor
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignorera cache, extrahera allt på nytt"
    )
    parser.add_argument(
        "--period", "-p",
        nargs="+",
        metavar="PERIOD",
        help="Filtrera på specifika perioder (t.ex. 'Q1 2025' 'Q2 2025')"
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Interaktivt läge - guidat flöde för att skapa databöcker"
    )
    parser.add_argument(
        "--model", "-m",
        choices=["claude", "mistral"],
        default="claude",
        help="Välj AI-modell: claude (default) eller mistral (OCR + Pixtral)"
    )

    args = parser.parse_args()

    # === VERIFIERA DATABAS ===
    if args.check_db:
        ok, message = check_database_setup()
        if ok:
            print("✅ " + message)
        else:
            print(message)
            sys.exit(1)
        return

    # === INTERAKTIVT LÄGE ===
    if args.interactive:
        # PDF-fil är valfritt i interaktivt läge
        pdf_file = None
        if args.pdf_dir:
            pdf_file = args.pdf_dir
        elif args.add:
            pdf_file = args.add[0]

        run_interactive_mode(pdf_file, model=args.model)
        return

    # === LISTA BOLAG ===
    if args.list_companies:
        ok, message = check_database_setup()
        if not ok:
            print(message)
            sys.exit(1)

        companies = list_companies()
        if not companies:
            print("Inga bolag i databasen än.")
        else:
            print(f"{'Bolag':<30} {'Slug':<20}")
            print("=" * 50)
            for c in companies:
                print(f"{c['name']:<30} {c['slug']:<20}")
        return

    # === GENERERA FRÅN DATABAS ===
    if args.from_db:
        if not args.company:
            print("❌ Ange bolag med --company")
            sys.exit(1)

        ok, message = check_database_setup()
        if not ok:
            print(message)
            sys.exit(1)

        print(f"📊 Laddar data för {args.company} från Supabase...")
        company = get_or_create_company(args.company)
        data = load_all_periods(company["id"])

        if not data:
            print(f"❌ Ingen data hittades för {args.company}")
            sys.exit(1)

        # Filtrera på perioder om --period angetts
        if args.period:
            periods_filter = [p.upper().replace(" ", "") for p in args.period]
            data = [d for d in data if d.get("metadata", {}).get("period", "").upper().replace(" ", "") in periods_filter]
            if not data:
                print(f"❌ Inga perioder matchade: {', '.join(args.period)}")
                sys.exit(1)
            print(f"   Filtrerar på: {', '.join(args.period)}")

        # Spara databok i bolagets ligger_i_databasen-mapp
        output_path = get_databook_path(args.output, args.company)
        normalize_tokens = build_databook(data, str(output_path))
        print(f"✅ Databok skapad: {output_path}")
        print(f"   Innehåller {len(data)} period(er)")

        # Visa normaliseringskostnad
        if normalize_tokens:
            norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
            print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")
        return

    # === LÄGG TILL NYA RAPPORTER ===
    if args.add:
        if not args.company:
            print("❌ Ange bolag med --company")
            sys.exit(1)

        ok, message = check_database_setup()
        if not ok:
            print(message)
            sys.exit(1)

        # Verifiera att PDFs finns
        add_paths = []
        for pdf in args.add:
            path = Path(pdf)
            if not path.exists():
                print(f"❌ Fil hittades inte: {pdf}")
                sys.exit(1)
            add_paths.append(str(path))

        print(f"📊 Lägger till {len(add_paths)} rapport(er) för {args.company}...\n")

        # Ladda befintlig data från Supabase
        company = get_or_create_company(args.company)
        existing = load_all_periods(company["id"])
        print(f"📁 Befintliga perioder: {len(existing)}")

        # Extrahera nya PDFs
        on_progress, state, stop_timer = create_progress_tracker(add_paths)

        # Beräkna base_folder för filflyttning (baserat på första filen)
        first_path = Path(add_paths[0])
        if first_path.parent.name == "skall_extractas":
            base_folder = str(first_path.parent.parent.parent)
        else:
            base_folder = str(first_path.parent.parent)

        if args.model == "mistral":
            new_results, failed = asyncio.run(
                extract_all_pdfs_mistral(
                    add_paths,
                    args.company,
                    on_progress,
                    use_cache=not args.no_cache,
                    base_folder=base_folder,
                    quiet=True,
                )
            )
        else:
            new_results, failed = asyncio.run(
                extract_all_pdfs_multi_pass(
                    add_paths,
                    args.company,
                    on_progress,
                    use_cache=not args.no_cache,
                    base_folder=base_folder,
                    quiet=True,
                )
            )
        stop_timer()
        print()  # Ny rad efter progress

        if failed:
            print(f"\n⚠️  {len(failed)} fil(er) misslyckades:")
            for path, error in failed:
                print(f"   • {Path(path).name}: {error}")

        # Visa pipeline-detaljer
        if new_results:
            print_pipeline_details(new_results)

        # Kombinera och bygg Excel
        all_data = existing + new_results
        print(f"\n📈 Totalt {len(all_data)} perioder")

        if all_data:
            # Spara databok i bolagets ligger_i_databasen-mapp
            output_path = get_databook_path(args.output, args.company, base_folder)
            normalize_tokens = build_databook(all_data, str(output_path))
            print(f"✅ Databok uppdaterad: {output_path}")

            # Visa normaliseringskostnad
            if normalize_tokens:
                norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
                print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")
        else:
            print("❌ Ingen data att skriva")

        return

    # === SKAPA NY DATABOK ===
    if not args.pdf_dir:
        parser.print_help()
        sys.exit(1)

    if not args.company:
        print("❌ Ange bolag med --company")
        sys.exit(1)

    ok, message = check_database_setup()
    if not ok:
        print(message)
        sys.exit(1)

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.exists():
        print(f"❌ Mappen hittades inte: {args.pdf_dir}")
        sys.exit(1)

    # Hitta alla PDFs
    pdf_paths = sorted(pdf_dir.glob("*.pdf"))

    if not pdf_paths:
        print(f"❌ Inga PDF-filer hittades i {args.pdf_dir}")
        sys.exit(1)

    pdf_path_strs = [str(p) for p in pdf_paths]

    print(f"📄 Hittade {len(pdf_paths)} PDF-fil(er) i {args.pdf_dir}")
    print(f"🏢 Bolag: {args.company}")

    # Progress tracker
    on_progress, state, stop_timer = create_progress_tracker(pdf_path_strs)

    # Beräkna base_folder för filflyttning
    # Strukturen är: base_folder/company/skall_extractas/fil.pdf
    # eller: base_folder/company/fil.pdf
    if pdf_dir.name == "skall_extractas":
        base_folder = str(pdf_dir.parent.parent)
    else:
        base_folder = str(pdf_dir.parent)

    # Kör extraktion
    if args.model == "mistral":
        print("🔄 Mistral pipeline (OCR + Pixtral)")
        successful, failed = asyncio.run(
            extract_all_pdfs_mistral(
                pdf_path_strs,
                args.company,
                on_progress,
                use_cache=not args.no_cache,
                base_folder=base_folder,
                quiet=True,
            )
        )
    else:
        print("🔄 Claude pipeline (Haiku → Sonnet → Haiku)")
        successful, failed = asyncio.run(
            extract_all_pdfs_multi_pass(
                pdf_path_strs,
                args.company,
                on_progress,
                use_cache=not args.no_cache,
                base_folder=base_folder,
                quiet=True,
            )
        )
    stop_timer()
    print("\n")  # Ny rad efter progress bar

    # Sammanfattning
    print(f"\n{'═' * 50}")
    print(f"✅ Lyckades:  {len(successful)}")
    if state["cached"] > 0:
        print(f"💾 Cachade:   {state['cached']} (0 kr)")
    if failed:
        print(f"❌ Fel:       {len(failed)}")
        print("\nMisslyckade filer:")
        for path, error in failed:
            print(f"   • {Path(path).name}: {error}")

    # Visa extraktionssammanfattning per fil
    if successful:
        print(f"\n📊 Extraktionsresultat:")
        for result in successful:
            period = result.get("metadata", {}).get("period", "?")
            tables = result.get("tables", [])
            sections = result.get("sections", [])
            pipeline_info = result.get("_pipeline_info", {})
            validation = pipeline_info.get("validation", {}).get("tables", {})
            error_count = validation.get("error_count", 0)
            retry_stats = pipeline_info.get("retry_stats", {})

            status = "✓" if error_count == 0 else f"{error_count} fel"
            retry_info = f" (retry: {retry_stats.get('tables_retried', 0)} fixade)" if retry_stats.get("retry_count", 0) > 0 else ""
            print(f"   {period}: {len(tables)} tabeller, {len(sections)} sektioner - {status}{retry_info}")

    # Visa pipeline-detaljer
    if successful:
        print_pipeline_details(successful)

    # Bygg Excel
    if successful:
        # Spara databok i bolagets ligger_i_databasen-mapp
        output_path = get_databook_path(args.output, args.company, base_folder)
        normalize_tokens = build_databook(successful, str(output_path))
        print(f"\n📊 Databok skapad: {output_path}")
        print(f"   Innehåller {len(successful)} period(er)")

        # Visa normaliseringskostnad
        if normalize_tokens:
            norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
            print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")
    else:
        print("\n❌ Ingen data extraherades, ingen Excel skapad")
        sys.exit(1)


if __name__ == "__main__":
    main()
