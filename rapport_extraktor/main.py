#!/usr/bin/env python3
"""
Finansiell Rapportextraktor - CLI

Extraherar finansiell data från PDF-kvartalsrapporter och
skapar professionella Excel-databöcker.

Användning:
    # Skapa ny databok från alla PDFs i en mapp
    python main.py ./rapporter/ -o databok.xlsx

    # Lägg till nya rapporter till befintlig databok
    python main.py --update databok.xlsx --add ny_rapport.pdf

    # Rensa cache och extrahera allt på nytt
    python main.py ./rapporter/ -o databok.xlsx --no-cache
"""

import argparse
import asyncio
import sys
from pathlib import Path

from extractor import extract_all_pdfs, load_cached_extractions, clear_cache
from excel_builder import build_databook


def create_progress_tracker(total: int):
    """
    Skapa progress-callback för terminal-output.
    """
    state = {"done": 0, "cached": 0, "failed": 0, "extracting": 0}

    def on_progress(pdf_path: str, status: str):
        filename = Path(pdf_path).name

        if status == "cached":
            state["cached"] += 1
            state["done"] += 1
        elif status == "done":
            state["done"] += 1
            state["extracting"] = max(0, state["extracting"] - 1)
        elif status.startswith("failed"):
            state["failed"] += 1
            state["done"] += 1
            state["extracting"] = max(0, state["extracting"] - 1)
        elif status == "extracting":
            state["extracting"] += 1

        # Progress bar
        pct = (state["done"] / total) * 100
        bar_width = 30
        filled = int(bar_width * state["done"] / total)
        bar = "█" * filled + "░" * (bar_width - filled)

        # Status line
        status_text = f"[{bar}] {pct:5.1f}%  {state['done']}/{total}"
        if state["extracting"] > 0:
            status_text += f"  ⏳ {state['extracting']} pågående"

        sys.stdout.write(f"\r{status_text:<70}")
        sys.stdout.flush()

    return on_progress, state


def main():
    parser = argparse.ArgumentParser(
        description="Extrahera finansiell data från PDF-rapporter till Excel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exempel:
  python main.py ./rapporter/ -o databok.xlsx
  python main.py --update databok.xlsx --add q4_rapport.pdf
  python main.py ./rapporter/ --no-cache -o ny_databok.xlsx
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

    # Uppdatera befintlig databok
    parser.add_argument(
        "--update",
        metavar="XLSX",
        help="Uppdatera befintlig databok"
    )
    parser.add_argument(
        "--add",
        nargs="+",
        metavar="PDF",
        help="PDF-filer att lägga till"
    )

    # Övriga flaggor
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignorera cache, extrahera allt på nytt"
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Rensa all cachad data"
    )

    args = parser.parse_args()

    # Rensa cache om begärt
    if args.clear_cache:
        clear_cache()
        print("Cache rensad.")
        if not args.pdf_dir and not args.update:
            return

    # === UPPDATERINGS-LÄGE ===
    if args.update:
        if not args.add:
            print("❌ Ange PDF-filer att lägga till med --add")
            sys.exit(1)

        # Verifiera att PDFs finns
        add_paths = []
        for pdf in args.add:
            path = Path(pdf)
            if not path.exists():
                print(f"❌ Fil hittades inte: {pdf}")
                sys.exit(1)
            add_paths.append(str(path))

        print(f"📊 Uppdaterar {args.update} med {len(add_paths)} nya PDF(s)...\n")

        # Ladda befintlig cachad data
        existing = load_cached_extractions()
        print(f"📁 Befintliga perioder i cache: {len(existing)}")

        # Extrahera nya PDFs
        on_progress, state = create_progress_tracker(len(add_paths))
        new_results, failed = asyncio.run(
            extract_all_pdfs(add_paths, on_progress, use_cache=not args.no_cache)
        )
        print()  # Ny rad efter progress

        if failed:
            print(f"\n⚠️  {len(failed)} fil(er) misslyckades:")
            for path, error in failed:
                print(f"   • {Path(path).name}: {error}")

        # Kombinera och bygg om
        all_data = existing + new_results
        print(f"\n📈 Totalt {len(all_data)} perioder")

        if all_data:
            build_databook(all_data, args.update)
            print(f"✅ Databok uppdaterad: {args.update}")
        else:
            print("❌ Ingen data att skriva")

        return

    # === SKAPA NY DATABOK ===
    if not args.pdf_dir:
        parser.print_help()
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

    print(f"📄 Hittade {len(pdf_paths)} PDF-fil(er) i {args.pdf_dir}\n")

    # Progress tracker
    on_progress, state = create_progress_tracker(len(pdf_paths))

    # Kör extraktion
    successful, failed = asyncio.run(
        extract_all_pdfs(
            [str(p) for p in pdf_paths],
            on_progress,
            use_cache=not args.no_cache
        )
    )
    print()  # Ny rad efter progress bar

    # Sammanfattning
    print(f"\n{'═' * 50}")
    print(f"✅ Lyckades:  {len(successful)}")
    if state["cached"] > 0:
        print(f"💾 Cachade:   {state['cached']}")
    if failed:
        print(f"❌ Fel:       {len(failed)}")
        print("\nMisslyckade filer:")
        for path, error in failed:
            print(f"   • {Path(path).name}: {error}")

    # Bygg Excel
    if successful:
        build_databook(successful, args.output)
        print(f"\n📊 Databok skapad: {args.output}")
        print(f"   Innehåller {len(successful)} period(er)")
    else:
        print("\n❌ Ingen data extraherades, ingen Excel skapad")
        sys.exit(1)


if __name__ == "__main__":
    main()
