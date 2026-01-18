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
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from extractor import extract_all_pdfs, load_cached_extractions
from excel_builder import build_databook
from supabase_client import list_companies, get_or_create_company, slugify, check_database_setup

# Ladda miljövariabler
load_dotenv()


# Claude Sonnet 4 priser (USD per 1M tokens)
PRICE_INPUT = 3.00   # $3 per 1M input tokens
PRICE_OUTPUT = 15.00  # $15 per 1M output tokens
USD_TO_SEK = 10.50   # Ungefärlig växelkurs


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Beräkna kostnad i SEK."""
    usd = (input_tokens * PRICE_INPUT + output_tokens * PRICE_OUTPUT) / 1_000_000
    return usd * USD_TO_SEK


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
    """
    # Behåll ordning med lista av sökvägar
    path_order = [str(p) for p in pdf_paths]
    files = {str(p): {
        "name": Path(p).name,
        "status": "pending",
        "input": 0,
        "output": 0,
        "start_time": None,
        "elapsed": 0,
    } for p in pdf_paths}

    state = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cached": 0,
        "failed": 0,
        "start_time": time.time(),
    }

    def render():
        # Rensa och flytta cursor - använd fler rader för säkerhet
        num_lines = len(files) + 2
        sys.stdout.write(f"\033[{num_lines}A")  # Flytta upp
        sys.stdout.write("\033[J")  # Rensa allt nedanför cursor

        for path in path_order:
            info = files[path]
            if info["status"] == "pending":
                icon = "[ ]"
                details = ""
            elif info["status"] == "extracting":
                icon = "[~]"
                elapsed = time.time() - info["start_time"] if info["start_time"] else 0
                details = f"{format_time(elapsed)}"
            elif info["status"] == "cached":
                icon = "[C]"
                details = "(cachad)"
            elif info["status"] == "done":
                icon = "[X]"
                tokens = info["input"] + info["output"]
                cost = calculate_cost(info["input"], info["output"])
                details = f"{tokens:,} tok | {cost:.2f} kr | {format_time(info['elapsed'])}"
            elif info["status"] == "failed":
                icon = "[!]"
                details = "fel"
            else:
                icon = "[?]"
                details = ""

            print(f"{icon} {info['name']:<35} {details}")

        # Totalt
        total_tokens = state["total_input_tokens"] + state["total_output_tokens"]
        total_cost = calculate_cost(state["total_input_tokens"], state["total_output_tokens"])
        elapsed = time.time() - state["start_time"]
        print(f"    Totalt: {total_tokens:,} tokens | {total_cost:.2f} kr | {format_time(elapsed)}")
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
                state["total_input_tokens"] += token_info["input_tokens"]
                state["total_output_tokens"] += token_info["output_tokens"]
        elif status.startswith("failed"):
            files[path_key]["status"] = "failed"
            state["failed"] += 1
        elif status == "extracting":
            files[path_key]["status"] = "extracting"
            files[path_key]["start_time"] = time.time()

        render()

    # Initial render - skapa plats för alla rader
    for _ in range(len(files) + 2):
        print()
    render()

    return on_progress, state


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


def run_interactive_mode(pdf_path: str | None = None):
    """
    Kör interaktivt läge med nytt flöde:
    1. START - Välj bolag från databasen
    2. Välj läge: Skapa databok (alla perioder) eller Extrahera kvartal
    3. Om kvartal - välj från lista eller extrahera ny PDF
    """
    import re
    from supabase_client import get_or_create_company, period_exists, get_pdf_hash, load_all_periods

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

    # === VÄLJ LÄGE ===
    print(f"\n{'═' * 50}")
    print(f"  Bolag: {company_name}")
    print(f"{'═' * 50}")

    if not all_periods:
        print("\n❌ Inga perioder finns för detta bolag.")
        print("   Vill du extrahera en ny rapport?")
        extract_new = input("   [Y/n] > ").strip().upper()
        if extract_new == "N":
            return
        # Gå till extraktion
        mode_choice = "2"
    else:
        print("\nVad vill du göra?")
        print("   1) Skapa fullständig databok (alla perioder)")
        print("   2) Skapa databok för ett specifikt kvartal")
        print("   3) Extrahera nytt kvartal från PDF")
        mode_choice = input("\n> ").strip()

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
        output_file = output_input if output_input else default_output

        # Bygg Excel
        print("\n📊 Skapar databok...")
        normalize_tokens = build_databook(data_to_export, output_file)

        print(f"\n✅ Databok skapad: {output_file}")
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
                output_file = output_input if output_input else default_output

                # Bygg Excel
                print("\n📊 Skapar databok...")
                normalize_tokens = build_databook(data_to_export, output_file)

                print(f"\n✅ Databok skapad: {output_file}")
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

        # Extraktionstyp
        print("\nExtraktionstyp:")
        print("   1) Standard (endast finansiella rapporter)")
        print("   2) Full (ALL text och alla tabeller)")
        extraction_choice = input("> ").strip()
        full_extraction = extraction_choice == "2"

        # Grafer (endast om full extraktion)
        skip_charts = False
        if full_extraction:
            charts_input = input("\nExtrahera grafer/diagram? [Y/n]: ").strip().upper()
            skip_charts = charts_input == "N"

        # Modell
        print("\nModell:")
        print("   1) Sonnet (rekommenderas)")
        print("   2) Haiku (billigare, mindre kapabel)")
        model_choice = input("> ").strip()
        model = "haiku" if model_choice == "2" else "sonnet"

        # === KONTROLLERA CACHE ===
        pdf_hash = get_pdf_hash(str(path))

        # Försök hitta period från filnamn
        period_match = re.search(r'[qQ](\d)[_-]?(\d{4})', path.stem)
        skip_extraction = False
        extracted_period = None

        if period_match:
            quarter = int(period_match.group(1))
            year = int(period_match.group(2))
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

            # Automatisk streaming för stora filer eller full extraktion
            pdf_size = path.stat().st_size
            use_streaming = pdf_size > 1_000_000 or full_extraction

            on_progress, state = create_progress_tracker([str(path)])

            successful, failed = asyncio.run(
                extract_all_pdfs(
                    [str(path)],
                    company_name,
                    on_progress,
                    use_cache=False,
                    full_extraction=full_extraction,
                    skip_charts=skip_charts,
                    use_streaming=use_streaming,
                    model=model
                )
            )
            print()

            if successful:
                extraction_cost = calculate_cost(state["total_input_tokens"], state["total_output_tokens"])
                extracted_period = successful[0].get("metadata", {}).get("period", "?")
                print(f"\n✅ Extraktion klar! Data sparad till databasen.")
                print(f"   Bolag:  {company_name}")
                print(f"   Period: {extracted_period}")
                print(f"   Kostnad: {extraction_cost:.2f} kr")
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
        output_file = output_input if output_input else default_output

        # Bygg Excel
        print("\n📊 Skapar databok...")
        normalize_tokens = build_databook(data_to_export, output_file)

        print(f"\n✅ Databok skapad: {output_file}")
        print(f"   Innehåller {len(data_to_export)} period(er)")

        total_cost = extraction_cost
        if normalize_tokens:
            norm_cost = calculate_cost(normalize_tokens["input_tokens"], normalize_tokens["output_tokens"])
            total_cost += norm_cost
            print(f"\n💰 Normaliseringskostnad: {norm_cost:.2f} kr")

        if total_cost > 0:
            print(f"💰 Total kostnad: {total_cost:.2f} kr")

    else:
        print("❌ Ogiltigt val.")


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
        "--full",
        action="store_true",
        help="Full extraktion - extrahera ALL text och alla tabeller (inte bara finansiella)"
    )
    parser.add_argument(
        "--period", "-p",
        nargs="+",
        metavar="PERIOD",
        help="Filtrera på specifika perioder (t.ex. 'Q1 2025' 'Q2 2025')"
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Använd streaming API (långsammare men visar progress)"
    )
    parser.add_argument(
        "--skip-charts",
        action="store_true",
        help="Hoppa över extraktion av grafer/diagram"
    )
    parser.add_argument(
        "--model",
        choices=["sonnet", "haiku"],
        default="sonnet",
        help="Vilken Claude-modell att använda (default: sonnet)"
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Interaktivt läge - guidat flöde för att skapa databöcker"
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

        run_interactive_mode(pdf_file)
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
        data = load_cached_extractions(args.company)

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

        normalize_tokens = build_databook(data, args.output)
        print(f"✅ Databok skapad: {args.output}")
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
        existing = load_cached_extractions(args.company)
        print(f"📁 Befintliga perioder: {len(existing)}")

        # Extrahera nya PDFs
        on_progress, state = create_progress_tracker(add_paths)
        new_results, failed = asyncio.run(
            extract_all_pdfs(
                add_paths,
                args.company,
                on_progress,
                use_cache=not args.no_cache,
                full_extraction=args.full,
                skip_charts=args.skip_charts,
                use_streaming=args.streaming,
                model=args.model
            )
        )
        print()  # Ny rad efter progress

        if failed:
            print(f"\n⚠️  {len(failed)} fil(er) misslyckades:")
            for path, error in failed:
                print(f"   • {Path(path).name}: {error}")

        # Kombinera och bygg Excel
        all_data = existing + new_results
        print(f"\n📈 Totalt {len(all_data)} perioder")

        if all_data:
            normalize_tokens = build_databook(all_data, args.output)
            print(f"✅ Databok uppdaterad: {args.output}")

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
    on_progress, state = create_progress_tracker(pdf_path_strs)

    # Kör extraktion
    if args.full:
        print("🔍 Full extraktion aktiverad - extraherar ALL text och alla tabeller")
    if args.skip_charts:
        print("📊 Hoppar över grafer/diagram")
    if args.model == "haiku":
        print("🤖 Använder Haiku-modellen (billigare men mindre kapabel)")
    successful, failed = asyncio.run(
        extract_all_pdfs(
            pdf_path_strs,
            args.company,
            on_progress,
            use_cache=not args.no_cache,
            full_extraction=args.full,
            skip_charts=args.skip_charts,
            use_streaming=args.streaming,
            model=args.model
        )
    )
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

    # Kostnadssammanfattning
    total_tokens = state["total_input_tokens"] + state["total_output_tokens"]
    if total_tokens > 0:
        total_cost = calculate_cost(state["total_input_tokens"], state["total_output_tokens"])
        print(f"\n💰 Kostnad:")
        print(f"   Input:  {state['total_input_tokens']:,} tokens")
        print(f"   Output: {state['total_output_tokens']:,} tokens")
        print(f"   Totalt: {total_cost:.2f} kr")

    # Bygg Excel
    if successful:
        normalize_tokens = build_databook(successful, args.output)
        print(f"\n📊 Databok skapad: {args.output}")
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
