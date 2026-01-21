#!/usr/bin/env python3
"""
Automatisk namngivning av PDF-rapporter (gratis, utan AI).

Extraherar bolagsnamn, period och språk från PDF:ens första sida med pypdf.
Döper automatiskt om mappar till samma namn som bolaget i PDF:en.

Namnformat: {bolag}-{år}-q{kvartal}-{språk}.pdf
Exempel: vitrolife-2024-q3-sv.pdf, abg-sundal-collier-2025-q3-en.pdf

Användning:
    python rename_pdf.py rapport.pdf              # Enskild fil
    python rename_pdf.py rapport.pdf --dry-run    # Förhandsvisa
    python rename_pdf.py mapp/ --batch            # Alla PDFs i mapp
    python rename_pdf.py mapp/ --watch            # Övervaka mapp kontinuerligt
    python rename_pdf.py --auto                   # Övervaka alla undermappar automatiskt
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


def company_to_slug(company: str) -> str:
    """Konvertera bolagsnamn till slug-format."""
    company_slug = company.lower()
    company_slug = re.sub(r'[^\w\s-]', '', company_slug)
    company_slug = re.sub(r'[\s_]+', '-', company_slug)
    company_slug = company_slug.strip('-')
    return company_slug


def generate_filename(company: str, quarter: int, year: int, language: str = 'sv') -> str:
    """Generera standardiserat filnamn med språksuffix.

    Format: {bolag}-{år}-q{kvartal}-{språk}.pdf
    Exempel: vitrolife-2024-q3-sv.pdf, abg-sundal-collier-2025-q3-en.pdf
    """
    company_slug = company_to_slug(company)
    return f"{company_slug}-{year}-q{quarter}-{language}.pdf"


def rename_folder_to_company(folder_path: Path, company: str, dry_run: bool = False) -> bool:
    """Döp om en mapp till bolagsnamnet (slug-format).

    Returnerar True om mappen döptes om, False annars.
    """
    company_slug = company_to_slug(company)
    current_name = folder_path.name

    # Hoppa över om mappen redan har rätt namn
    if current_name == company_slug:
        return False

    # Hoppa över speciella mappar
    if current_name in ('ligger_i_databasen', 'skall_extractas'):
        return False

    new_path = folder_path.parent / company_slug

    # Kontrollera om målmappen redan finns
    if new_path.exists():
        print(f"    [!] Kan inte döpa om mapp: {company_slug} finns redan")
        return False

    if dry_run:
        print(f"    [DRY-RUN] Mapp: {current_name} -> {company_slug}")
        return True

    folder_path.rename(new_path)
    print(f"    [OK] Mapp omdöpt: {current_name} -> {company_slug}")
    return True


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


def rename_pdf(pdf_path: str, dry_run: bool = False, rename_parent_folder: bool = False) -> tuple[bool, str, str | None]:
    """Analysera och döp om en PDF.

    Returnerar (success, message, company_name).
    """
    path = Path(pdf_path)

    print(f"\n[~] Analyserar: {path.name}")

    info = analyze_pdf(pdf_path)
    if not info:
        return False, "Kunde inte analysera PDF", None

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
        return False, f"Fil finns redan: {new_name}", company

    if path.name == new_name:
        return True, "Redan korrekt namn", company

    if dry_run:
        print(f"    [DRY-RUN] Skulle döpa om till: {new_name}")
        return True, f"Dry-run: {path.name} -> {new_name}", company

    path.rename(new_path)
    print(f"    [OK] Omdöpt!")

    return True, f"{path.name} -> {new_name}", company


def batch_rename(folder: str, dry_run: bool = False, rename_folder: bool = False) -> str | None:
    """Döp om alla PDF-filer i en mapp.

    Returnerar bolagsnamn om det hittades, annars None.
    """
    folder_path = Path(folder)

    if not folder_path.exists():
        print(f"[!] Mappen hittades inte: {folder}")
        return None

    pdf_files = sorted(folder_path.glob("*.pdf"))

    if not pdf_files:
        print(f"[!] Inga PDF-filer i: {folder}")
        return None

    print(f"\n{'=' * 50}")
    print(f"BATCH RENAME - {len(pdf_files)} filer i {folder_path.name}")
    print(f"{'=' * 50}")

    if dry_run:
        print("[DRY-RUN MODE]")

    success_count = 0
    skip_count = 0
    fail_count = 0
    detected_company = None

    for pdf_path in pdf_files:
        # Hoppa över filer som redan har rätt format (med språksuffix)
        if re.match(r'^[\w-]+-\d{4}-q\d-(sv|no|en)\.pdf$', pdf_path.name):
            print(f"\n[SKIP] {pdf_path.name} (redan korrekt format)")
            skip_count += 1
            # Extrahera bolagsnamn från redan namngivna filer
            if detected_company is None:
                info = analyze_pdf(str(pdf_path))
                if info:
                    detected_company = info["company"]
            continue

        success, message, company = rename_pdf(str(pdf_path), dry_run)

        if company and detected_company is None:
            detected_company = company

        if success:
            success_count += 1
        else:
            fail_count += 1
            print(f"    [!] {message}")

    print(f"\n{'=' * 50}")
    print(f"Klart: {success_count} omdöpta, {skip_count} hoppade över, {fail_count} misslyckade")

    return detected_company


class PdfRenameHandler(FileSystemEventHandler):
    """Watchdog handler som döper om nya PDF-filer och mappar."""

    def __init__(self, dry_run: bool = False, auto_rename_folders: bool = False):
        self.dry_run = dry_run
        self.auto_rename_folders = auto_rename_folders
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
        success, message, company = rename_pdf(str(path), self.dry_run)

        # Döp om föräldramappen om aktiverat och vi hittade ett bolagsnamn
        if self.auto_rename_folders and company:
            # Hitta "bolagsmappen" (två nivåer upp från PDF:en om den ligger i skall_extractas)
            parent = path.parent
            if parent.name in ('ligger_i_databasen', 'skall_extractas'):
                company_folder = parent.parent
                rename_folder_to_company(company_folder, company, self.dry_run)


def watch_folders(folders: list, dry_run: bool = False, auto_rename_folders: bool = False):
    """Övervaka en eller flera mappar och döp om nya PDF-filer automatiskt."""
    if not WATCHDOG_AVAILABLE:
        print("[!] watchdog behövs för --watch")
        print("    Installera: pip install watchdog")
        sys.exit(1)

    # Validera alla mappar
    folder_paths = []
    for folder in folders:
        folder_path = Path(folder)
        if not folder_path.exists():
            print(f"[!] Mappen hittades inte: {folder}")
            sys.exit(1)
        folder_paths.append(folder_path)

    print(f"\n{'=' * 50}")
    print(f"WATCHDOG - Övervakar {len(folders)} mappar:")
    for fp in folder_paths:
        print(f"  • {fp}")
    print(f"{'=' * 50}")
    if dry_run:
        print("[DRY-RUN MODE]")
    print("Tryck Ctrl+C för att avsluta\n")

    # Kör batch först för befintliga filer i varje mapp
    for folder in folders:
        batch_rename(folder, dry_run)

    # Starta övervakning för alla mappar
    observer = Observer()
    for folder in folders:
        event_handler = PdfRenameHandler(dry_run, auto_rename_folders)
        observer.schedule(event_handler, folder, recursive=False)

    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Avslutar...")
        observer.stop()
    observer.join()


def auto_watch(base_folder: str, dry_run: bool = False):
    """Automatiskt övervaka alla undermappar i en basmapp.

    Hittar alla bolagsmappar och övervakar deras 'skall_extractas'-mappar.
    Döper även om bolagsmapparna baserat på bolagsnamn från PDF:erna.
    """
    if not WATCHDOG_AVAILABLE:
        print("[!] watchdog behövs för --auto")
        print("    Installera: pip install watchdog")
        sys.exit(1)

    base_path = Path(base_folder)
    if not base_path.exists():
        print(f"[!] Basmappen hittades inte: {base_folder}")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"AUTO MODE - Övervakar alla bolagsmappar i: {base_path}")
    print(f"{'=' * 60}")
    if dry_run:
        print("[DRY-RUN MODE]")

    # Hitta alla undermappar (bolagsmappar)
    company_folders = [f for f in base_path.iterdir() if f.is_dir() and not f.name.startswith('.')]

    if not company_folders:
        print("[!] Inga undermappar hittades")
        sys.exit(1)

    # Samla alla mappar att övervaka
    watch_paths = []
    folders_to_rename = {}  # {folder_path: company_name}

    for company_folder in sorted(company_folders):
        print(f"\n[*] Bolagsmapp: {company_folder.name}")

        # Hitta alla undermappar med PDF:er
        subfolders = [
            company_folder / 'skall_extractas',
            company_folder / 'ligger_i_databasen',
            company_folder,  # Direkta PDF:er i bolagsmappen
        ]

        detected_company = None

        for subfolder in subfolders:
            if subfolder.exists() and subfolder.is_dir():
                pdf_files = list(subfolder.glob("*.pdf"))
                if pdf_files:
                    print(f"    [{subfolder.name}] {len(pdf_files)} PDF-filer")
                    watch_paths.append(subfolder)

                    # Kör batch rename och samla bolagsnamn
                    company = batch_rename(str(subfolder), dry_run)
                    if company and detected_company is None:
                        detected_company = company

        # Döp om bolagsmappen om vi hittade ett bolagsnamn
        if detected_company:
            company_slug = company_to_slug(detected_company)
            if company_folder.name != company_slug:
                folders_to_rename[company_folder] = detected_company

    # Döp om bolagsmappar
    if folders_to_rename:
        print(f"\n{'=' * 60}")
        print("OMDÖPNING AV BOLAGSMAPPAR")
        print(f"{'=' * 60}")
        for folder, company in folders_to_rename.items():
            rename_folder_to_company(folder, company, dry_run)

        # Uppdatera watch_paths efter omdöpningar
        if not dry_run:
            new_watch_paths = []
            for wp in watch_paths:
                # Kolla om föräldramappen döptes om
                parent = wp.parent
                if parent in folders_to_rename:
                    new_parent = parent.parent / company_to_slug(folders_to_rename[parent])
                    new_watch_paths.append(new_parent / wp.name)
                else:
                    new_watch_paths.append(wp)
            watch_paths = [p for p in new_watch_paths if p.exists()]

    # Starta övervakning
    if not watch_paths:
        print("\n[!] Inga mappar att övervaka")
        return

    print(f"\n{'=' * 60}")
    print(f"STARTAR ÖVERVAKNING - {len(watch_paths)} mappar:")
    for wp in watch_paths:
        print(f"  • {wp}")
    print(f"{'=' * 60}")
    print("Tryck Ctrl+C för att avsluta\n")

    observer = Observer()
    for folder in watch_paths:
        event_handler = PdfRenameHandler(dry_run, auto_rename_folders=True)
        observer.schedule(event_handler, str(folder), recursive=False)

    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Avslutar...")
        observer.stop()
    observer.join()


def watch_folder(folder: str, dry_run: bool = False):
    """Övervaka en mapp och döp om nya PDF-filer automatiskt."""
    watch_folders([folder], dry_run)


def main():
    parser = argparse.ArgumentParser(
        description="Automatisk namngivning av PDF-rapporter (gratis)"
    )

    parser.add_argument("path", nargs="*", help="En eller flera PDF-filer/mappar")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Visa vad som skulle göras")
    parser.add_argument("--batch", "-b", action="store_true",
                        help="Behandla som mapp(ar)")
    parser.add_argument("--watch", "-w", action="store_true",
                        help="Övervaka mapp(ar) och döp om nya filer automatiskt")
    parser.add_argument("--auto", "-a", action="store_true",
                        help="Automatiskt övervaka alla undermappar och döp om mappar till bolagsnamn")

    args = parser.parse_args()
    paths = args.path

    if args.auto:
        # Auto-läge: övervaka alla undermappar i basmappen
        base_folder = paths[0] if paths else "."
        auto_watch(base_folder, args.dry_run)
    elif args.watch:
        # Watch-läge kan hantera flera mappar
        watch_folders(paths, args.dry_run)
    elif not paths:
        parser.print_help()
    elif args.batch or len(paths) > 1 or Path(paths[0]).is_dir():
        # Batch-läge för mappar
        for path in paths:
            batch_rename(path, args.dry_run)
    else:
        # Enkelfil-läge
        rename_pdf(paths[0], args.dry_run)


if __name__ == "__main__":
    main()
