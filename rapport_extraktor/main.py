#!/usr/bin/env python3
"""
Finansiell Rapportextraktor - CLI

Extraherar finansiell data från PDF-kvartalsrapporter och
skapar professionella Excel-databöcker.

Användning:
    # Skapa ny databok från alla PDFs i en mapp
    python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx

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


def main():
    parser = argparse.ArgumentParser(
        description="Extrahera finansiell data från PDF-rapporter till Excel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exempel:
  python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx
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
                use_cache=not args.no_cache
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
    successful, failed = asyncio.run(
        extract_all_pdfs(
            pdf_path_strs,
            args.company,
            on_progress,
            use_cache=not args.no_cache
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
