#!/usr/bin/env python3
"""
Automatisk namngivning av PDF-rapporter (gratis, utan AI).

Extraherar bolagsnamn, period och språk från PDF:ens första sida med pypdf.

Namnformat: {bolag}-{år}-q{kvartal}-{språk}.pdf
Exempel: vitrolife-2024-q3-sv.pdf, abg-sundal-collier-2025-q3-en.pdf

Användning:
    python rename_pdf.py rapport.pdf              # Enskild fil
    python rename_pdf.py rapport.pdf --dry-run    # Förhandsvisa
    python rename_pdf.py mapp/ --batch            # Alla PDFs i mapp
    python rename_pdf.py mapp/ --watch            # Övervaka mapp kontinuerligt
"""

import argparse
import re
import sys
import time
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    print("Installera pypdf: pip install pypdf")
    sys.exit(1)

WATCHDOG_AVAILABLE = False
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    pass


# Språkspecifika termer för identifiering
SWEDISH_INDICATORS = [
    'delårsrapport', 'kvartalsrapport', 'bokslutskommuniké', 'årsredovisning',
    'nettoomsättning', 'rörelseresultat', 'rörelsemarginal', 'koncernen',
    'januari', 'februari', 'mars', 'april', 'maj', 'juni',
    'juli', 'augusti', 'september', 'oktober', 'november', 'december',
    'kvartal', 'halvår', 'helår', 'msek', 'tsek', 'mkr', 'tkr',
    'aktieägare', 'styrelsen', 'verkställande direktör',
]

NORWEGIAN_INDICATORS = [
    'kvartalsrapport', 'delårsrapport', 'årsrapport', 'årsregnskap',
    'driftsinntekter', 'driftsresultat', 'driftsmargin', 'konsernet',
    'januar', 'februar', 'mars', 'april', 'mai', 'juni',
    'juli', 'august', 'september', 'oktober', 'november', 'desember',
    'kvartal', 'halvår', 'helår',
    # OBS: mnok/nok räknas INTE - det är valuta, inte språk!
    'aksjonærer', 'styret', 'administrerende direktør',
    'egenkapital', 'gjeld', 'eiendeler',
]

ENGLISH_INDICATORS = [
    'interim report', 'quarterly report', 'annual report', 'year-end report',
    'net sales', 'operating profit', 'operating margin', 'the group',
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
    'quarter', 'half-year', 'full-year', 'meur', 'musd', 'mgbp',
    'shareholders', 'board of directors', 'chief executive officer',
    'equity', 'liabilities', 'assets', 'revenue', 'earnings',
]

# Ord som INTE är bolagsnamn (filtrera bort)
EXCLUDED_WORDS = {
    # Svenska
    'januari', 'februari', 'mars', 'april', 'maj', 'juni',
    'juli', 'augusti', 'september', 'oktober', 'november', 'december',
    'kvartal', 'quarter', 'rapport', 'report', 'delårsrapport', 'kvartalsrapport',
    'bokslutskommuniké', 'årsredovisning', 'halvårsrapport',
    'koncernen', 'koncernens', 'moderbolaget', 'styrelsen',
    # Norska
    'januar', 'februar', 'mai', 'august', 'desember',
    'konsernet', 'konsernets', 'morselskapet', 'styret',
    'årsrapport', 'årsregnskap',
    # Engelska
    'january', 'february', 'march', 'may', 'june', 'july', 'august',
    'september', 'october', 'november', 'december',
    'interim', 'quarterly', 'annual', 'financial', 'consolidated',
    'group', 'company', 'corporation', 'limited', 'holdings',
    'the', 'for', 'and', 'year', 'half',
}


def extract_text_from_first_pages(pdf_path: str, num_pages: int = 2) -> str:
    """Extrahera text från PDF:ens första sidor."""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for i in range(min(num_pages, len(reader.pages))):
            page_text = reader.pages[i].extract_text() or ""
            text_parts.append(page_text)
        return "\n".join(text_parts)
    except Exception as e:
        print(f"[!] Kunde inte läsa PDF: {e}")
    return ""


def detect_language(text: str) -> str:
    """Detektera språk baserat på nyckelord. Returnerar 'sv', 'no', eller 'en'."""
    text_lower = text.lower()

    # Räkna träffar för varje språk
    sv_count = sum(1 for word in SWEDISH_INDICATORS if word in text_lower)
    no_count = sum(1 for word in NORWEGIAN_INDICATORS if word in text_lower)
    en_count = sum(1 for word in ENGLISH_INDICATORS if word in text_lower)

    # Engelska-specifika ord som är starka indikatorer
    english_strong = ['interim report', 'quarterly report', 'annual report',
                      'revenue', 'earnings', 'shareholders', 'board of directors',
                      'net sales', 'operating profit', 'the group', 'diluted eps']
    en_strong = sum(1 for word in english_strong if word in text_lower)

    # Om starka engelska indikatorer finns, prioritera engelska
    if en_strong >= 2:
        return 'en'

    # Om engelska har fler träffar än skandinaviska
    if en_count > max(sv_count, no_count):
        return 'en'

    # Norska och svenska är lika - kolla specifika skillnader
    if no_count > 0 or sv_count > 0:
        # Norska-specifika ord som inte finns på svenska (exkl. valuta)
        norwegian_unique = ['aksjonærer', 'styret', 'eiendeler', 'gjeld',
                           'konsernet', 'driftsinntekter', 'januar', 'februar', 'mai',
                           'august', 'desember', 'administrerende']
        swedish_unique = ['aktieägare', 'styrelsen', 'tillgångar', 'skulder', 'msek', 'tsek',
                         'koncernen', 'nettoomsättning', 'januari', 'februari', 'maj',
                         'augusti', 'december', 'verkställande']

        no_unique = sum(1 for word in norwegian_unique if word in text_lower)
        sv_unique = sum(1 for word in swedish_unique if word in text_lower)

        if no_unique > sv_unique:
            return 'no'
        elif sv_unique > no_unique:
            return 'sv'
        # Om lika, kolla generella räknare
        if no_count > sv_count:
            return 'no'
        elif sv_count >= no_count and sv_count > 0:
            return 'sv'

    # Default till svenska om inget tydligt
    return 'sv'


def find_company_name(text: str, filename: str) -> str | None:
    """Hitta bolagsnamn dynamiskt från text eller filnamn."""
    # Strategi 1: Hitta från filnamnet (ofta mest pålitligt)
    # Mönster: "BOLAG-2024-Q3" eller "Bolag_Q3_2024" etc.
    filename_patterns = [
        r'^([A-Za-zÅÄÖåäöØøÆæ][A-Za-zÅÄÖåäöØøÆæ0-9\s\-]+?)[-_\s]*\d{4}[-_\s]*[qQ]\d',
        r'^([A-Za-zÅÄÖåäöØøÆæ][A-Za-zÅÄÖåäöØøÆæ0-9\s\-]+?)[-_\s]*[qQ]\d[-_\s]*\d{4}',
        r'^([A-Za-zÅÄÖåäöØøÆæ][A-Za-zÅÄÖåäöØøÆæ\-\s]+?)[-_\s]+(?:interim|report|rapport)',
    ]

    for pattern in filename_patterns:
        match = re.match(pattern, filename, re.IGNORECASE)
        if match:
            name = match.group(1).strip(' -_')
            if len(name) >= 2 and name.lower() not in EXCLUDED_WORDS:
                return clean_company_name(name)

    # Strategi 2: Hitta från dokumenttext
    text_patterns = [
        # "Delårsrapport Bolagsnamn" eller "Interim Report Company Name"
        r'(?:delårsrapport|kvartalsrapport|interim report|quarterly report|årsrapport|annual report)\s+(?:för\s+)?([A-ZÅÄÖØÆ][A-Za-zÅÄÖåäöØøÆæ\s\-]+?)(?:\s+AB|\s+ASA|\s+Group|\s+AS|\s*[,\.]|\s+\d|\s+Q\d|\s+januari|\s+februar|\s+january|\n)',
        # Första raden med stort bolagsnamn
        r'^([A-ZÅÄÖØÆ][A-ZÅÄÖØÆ\s\-]{2,30}?)(?:\s+AB|\s+ASA|\s+AS|\s+Group)?\s*$',
        # "Bolagsnamn AB" eller "Company ASA" på egen rad
        r'\n([A-ZÅÄÖØÆ][A-Za-zÅÄÖåäöØøÆæ\s\-]+?)\s+(?:AB|ASA|AS|Group|Inc|Ltd|Holding)\s*[\n,\.]',
    ]

    for pattern in text_patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Ta bort trailing "AB", "ASA" etc om det kom med
            name = re.sub(r'\s+(AB|ASA|AS|Group|Inc|Ltd|Holding)\s*$', '', name, flags=re.IGNORECASE)
            if len(name) >= 2 and name.lower() not in EXCLUDED_WORDS:
                return clean_company_name(name)

    return None


def clean_company_name(name: str) -> str:
    """Rensa och normalisera bolagsnamn."""
    # Ta bort suffix
    name = re.sub(r'\s+(AB|ASA|AS|Group|Inc|Ltd|Holding|Corporation|Corp)\s*$', '', name, flags=re.IGNORECASE)
    # Ta bort extra whitespace
    name = ' '.join(name.split())
    # Kapitalisera korrekt
    name = name.strip(' -_')
    return name


def find_period(text: str, filename: str) -> tuple[int, int] | None:
    """Hitta kvartal och år i text eller filnamn."""
    combined = f"{filename} {text}"

    # Mönster för "Q1 2024", "Q1-2024", "Q3 / 2024", "q1 24", etc.
    q_patterns = [
        r'[qQ](\d)\s*[-_/]?\s*(\d{4})',  # Q1 2024, Q1-2024, Q3 / 2024
        r'[qQ](\d)\s*[-_/]?\s*(\d{2})(?!\d)',  # Q1 24, Q1-24
        r'(\d{4})\s*[qQ](\d)',  # 2024 Q1
    ]

    for pattern in q_patterns:
        match = re.search(pattern, combined)
        if match:
            groups = match.groups()
            if len(groups[0]) == 4:  # År först (2024 Q1)
                year = int(groups[0])
                quarter = int(groups[1])
            else:
                quarter = int(groups[0])
                year_str = groups[1]
                year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)

            if 1 <= quarter <= 4 and 2000 <= year <= 2100:
                return quarter, year

    # Mönster för "januari-mars 2024" etc.
    month_quarters = {
        ('januari', 'mars'): 1, ('jan', 'mar'): 1,
        ('january', 'march'): 1,
        ('april', 'juni'): 2, ('apr', 'jun'): 2,
        ('april', 'june'): 2,
        ('juli', 'september'): 3, ('jul', 'sep'): 3,
        ('july', 'september'): 3,
        ('oktober', 'december'): 4, ('okt', 'dec'): 4,
        ('october', 'december'): 4, ('oct', 'dec'): 4,
    }

    for (start, end), quarter in month_quarters.items():
        pattern = rf'{start}[^\d]*{end}[^\d]*(\d{{4}})'
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            if 2000 <= year <= 2100:
                return quarter, year

    return None


def generate_filename(company: str, quarter: int, year: int, language: str = 'sv') -> str:
    """Generera standardiserat filnamn med språksuffix.

    Format: {bolag}-{år}-q{kvartal}-{språk}.pdf
    Exempel: vitrolife-2024-q3-sv.pdf, abg-sundal-collier-2025-q3-en.pdf
    """
    company_slug = company.lower()
    company_slug = re.sub(r'[^\w\s-]', '', company_slug)
    company_slug = re.sub(r'[\s_]+', '-', company_slug)
    company_slug = company_slug.strip('-')
    return f"{company_slug}-{year}-q{quarter}-{language}.pdf"


def analyze_pdf(pdf_path: str) -> dict | None:
    """Analysera PDF och extrahera namninfo inklusive språk."""
    path = Path(pdf_path)

    if not path.exists():
        print(f"[!] Fil hittades inte: {pdf_path}")
        return None

    text = extract_text_from_first_pages(pdf_path)
    filename = path.stem

    company = find_company_name(text, filename)
    period = find_period(text, filename)
    language = detect_language(text)

    if not company:
        print(f"[!] Kunde inte hitta bolagsnamn")
        return None

    if not period:
        print(f"[!] Kunde inte hitta period (kvartal/år)")
        return None

    quarter, year = period

    return {
        "company": company,
        "quarter": quarter,
        "year": year,
        "language": language,
    }


def rename_pdf(pdf_path: str, dry_run: bool = False) -> tuple[bool, str]:
    """Analysera och döp om en PDF."""
    path = Path(pdf_path)

    print(f"\n[~] Analyserar: {path.name}")

    info = analyze_pdf(pdf_path)
    if not info:
        return False, "Kunde inte analysera PDF"

    company = info["company"]
    quarter = info["quarter"]
    year = info["year"]
    language = info["language"]

    new_name = generate_filename(company, quarter, year, language)
    new_path = path.parent / new_name

    language_names = {'sv': 'Svenska', 'no': 'Norska', 'en': 'Engelska'}
    print(f"    Bolag:  {company}")
    print(f"    Period: Q{quarter} {year}")
    print(f"    Språk:  {language_names.get(language, language)}")
    print(f"    Nytt namn: {new_name}")

    if new_path.exists() and new_path != path:
        return False, f"Fil finns redan: {new_name}"

    if path.name == new_name:
        return True, "Redan korrekt namn"

    if dry_run:
        print(f"    [DRY-RUN] Skulle döpa om till: {new_name}")
        return True, f"Dry-run: {path.name} -> {new_name}"

    path.rename(new_path)
    print(f"    [OK] Omdöpt!")

    return True, f"{path.name} -> {new_name}"


def batch_rename(folder: str, dry_run: bool = False):
    """Döp om alla PDF-filer i en mapp."""
    folder_path = Path(folder)

    if not folder_path.exists():
        print(f"[!] Mappen hittades inte: {folder}")
        return

    pdf_files = sorted(folder_path.glob("*.pdf"))

    if not pdf_files:
        print(f"[!] Inga PDF-filer i: {folder}")
        return

    print(f"\n{'=' * 50}")
    print(f"BATCH RENAME - {len(pdf_files)} filer")
    print(f"{'=' * 50}")

    if dry_run:
        print("[DRY-RUN MODE]")

    success_count = 0
    skip_count = 0
    fail_count = 0

    for pdf_path in pdf_files:
        # Hoppa över filer som redan har rätt format (med språksuffix)
        if re.match(r'^[\w-]+-\d{4}-q\d-(sv|no|en)\.pdf$', pdf_path.name):
            print(f"\n[SKIP] {pdf_path.name} (redan korrekt format)")
            skip_count += 1
            continue

        success, message = rename_pdf(str(pdf_path), dry_run)

        if success:
            success_count += 1
        else:
            fail_count += 1
            print(f"    [!] {message}")

    print(f"\n{'=' * 50}")
    print(f"Klart: {success_count} omdöpta, {skip_count} hoppade över, {fail_count} misslyckade")


class PdfRenameHandler(FileSystemEventHandler):
    """Watchdog handler som döper om nya PDF-filer."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.processed_files: set[str] = set()

    def on_created(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)

        # Endast PDF-filer
        if path.suffix.lower() != '.pdf':
            return

        # Hoppa över redan korrekt namngivna filer (med språksuffix)
        if re.match(r'^[\w-]+-\d{4}-q\d-(sv|no|en)\.pdf$', path.name):
            return

        # Vänta lite så filen hinner skrivas klart
        time.sleep(1)

        # Undvik att processa samma fil flera gånger
        if str(path) in self.processed_files:
            return
        self.processed_files.add(str(path))

        # Döp om filen
        rename_pdf(str(path), self.dry_run)


def watch_folder(folder: str, dry_run: bool = False):
    """Övervaka en mapp och döp om nya PDF-filer automatiskt."""
    if not WATCHDOG_AVAILABLE:
        print("[!] watchdog behövs för --watch")
        print("    Installera: pip install watchdog")
        sys.exit(1)

    folder_path = Path(folder)
    if not folder_path.exists():
        print(f"[!] Mappen hittades inte: {folder}")
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"WATCHDOG - Övervakar: {folder_path}")
    print(f"{'=' * 50}")
    if dry_run:
        print("[DRY-RUN MODE]")
    print("Tryck Ctrl+C för att avsluta\n")

    # Kör batch först för befintliga filer
    batch_rename(folder, dry_run)

    # Starta övervakning
    event_handler = PdfRenameHandler(dry_run)
    observer = Observer()
    observer.schedule(event_handler, folder, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Avslutar...")
        observer.stop()
    observer.join()


def main():
    parser = argparse.ArgumentParser(
        description="Automatisk namngivning av PDF-rapporter (gratis)"
    )

    parser.add_argument("path", help="PDF-fil eller mapp")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Visa vad som skulle göras")
    parser.add_argument("--batch", "-b", action="store_true",
                        help="Behandla som mapp")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="Övervaka mapp och döp om nya filer automatiskt")

    args = parser.parse_args()
    path = Path(args.path)

    if args.watch:
        watch_folder(args.path, args.dry_run)
    elif args.batch or path.is_dir():
        batch_rename(args.path, args.dry_run)
    else:
        rename_pdf(args.path, args.dry_run)


if __name__ == "__main__":
    main()
