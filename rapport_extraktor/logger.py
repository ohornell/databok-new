"""
Centraliserad loggkonfiguration för Rapport Extraktor.

Tillhandahåller strukturerad loggning med:
- Konsol-output (DEBUG-nivå, färgkodad)
- Fil-output (DEBUG-nivå) -> extraction_run_{timestamp}.log
- Stöd för svenska meddelanden

Användning:
    from logger import setup_logger, get_logger

    # I början av extraktion
    setup_logger("Vitrolife", "/path/to/alla_rapporter")

    # I moduler
    logger = get_logger(__name__)
    logger.info("[OCR] Sida 1/15 klar")
    logger.debug("[API] Request skickad")
    logger.warning("[VARNING] Tabell saknar data")
    logger.error("[FEL] Kunde inte extrahera tabell")
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Global logger instance
_logger: Optional[logging.Logger] = None
_log_file_path: Optional[Path] = None


class ColoredFormatter(logging.Formatter):
    """Formatter med ANSI-färger för konsol-output."""

    # ANSI escape codes
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Grön
        'WARNING': '\033[33m',   # Gul
        'ERROR': '\033[31m',     # Röd
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m',      # Reset
    }

    def format(self, record: logging.LogRecord) -> str:
        # Lägg till färg baserat på nivå
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']

        # Formatera meddelandet
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        level = record.levelname
        module = record.name.split('.')[-1][:20]  # Begränsa modulnamn till 20 tecken

        # Färgkoda nivå
        colored_level = f"{color}{level:<8}{reset}"

        return f"{timestamp} | {colored_level} | {module:<20} | {record.getMessage()}"


class PlainFormatter(logging.Formatter):
    """Formatter utan färger för fil-output."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        level = record.levelname
        module = record.name.split('.')[-1][:20]

        return f"{timestamp} | {level:<8} | {module:<20} | {record.getMessage()}"


class SupabaseHandler(logging.Handler):
    """Logging handler som skriver till Supabase."""

    def __init__(self, company_id: str | None = None):
        super().__init__()
        self.company_id = company_id
        self.period_id: str | None = None

    def set_period_id(self, period_id: str):
        """Sätt period_id för att koppla loggar till en specifik extraktion."""
        self.period_id = period_id

    def emit(self, record: logging.LogRecord):
        try:
            from supabase_client import log_to_supabase
            log_to_supabase(
                log_level=record.levelname,
                module=record.name.split('.')[-1],
                message=record.getMessage(),
                period_id=self.period_id,
                company_id=self.company_id,
            )
        except Exception:
            pass  # Ignorera för att undvika rekursiv loggning


# Global Supabase handler (för att kunna sätta period_id senare)
_supabase_handler: Optional[SupabaseHandler] = None


def set_period_id(period_id: str):
    """Sätt period_id på Supabase-handler för att koppla loggar till extraktion."""
    global _supabase_handler
    if _supabase_handler:
        _supabase_handler.set_period_id(period_id)


def setup_logger(
    company_name: str,
    base_folder: str | Path | None = None,
    console_level: int = logging.DEBUG,
    file_level: int = logging.DEBUG,
    company_id: str | None = None,
) -> logging.Logger:
    """
    Konfigurera och returnera huvudlogger.

    Args:
        company_name: Bolagsnamn (används för att hitta loggmapp)
        base_folder: Basmapp där bolagsmappar finns (default: alla_rapporter/)
        console_level: Loggnivå för konsol (default: DEBUG)
        file_level: Loggnivå för fil (default: DEBUG)
        company_id: Company UUID för Supabase-loggning (valfritt)

    Returns:
        Konfigurerad logger-instans
    """
    global _logger, _log_file_path, _supabase_handler

    # Skapa eller återanvänd logger
    logger = logging.getLogger('rapport_extraktor')
    logger.setLevel(logging.DEBUG)  # Sätt lägsta nivå

    # Ta bort befintliga handlers för att undvika dubbletter
    logger.handlers.clear()

    # === KONSOL HANDLER ===
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)

    # Använd färger om terminal stödjer det
    if sys.stdout.isatty():
        console_handler.setFormatter(ColoredFormatter())
    else:
        console_handler.setFormatter(PlainFormatter())

    logger.addHandler(console_handler)

    # === FIL HANDLER ===
    if base_folder is not None:
        base_folder = Path(base_folder)

        # Skapa slug från bolagsnamn
        company_slug = slugify(company_name)

        # Loggmapp: alla_rapporter/{company}/ligger_i_databasen/
        log_folder = base_folder / company_slug / "ligger_i_databasen"
        log_folder.mkdir(parents=True, exist_ok=True)

        # Filnamn med timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f"extraction_run_{timestamp}.log"
        _log_file_path = log_folder / log_filename

        # Skapa file handler
        file_handler = logging.FileHandler(_log_file_path, encoding='utf-8')
        file_handler.setLevel(file_level)
        file_handler.setFormatter(PlainFormatter())
        logger.addHandler(file_handler)

        logger.info(f"[LOGG] Loggfil skapad: {_log_file_path}")

    # === SUPABASE HANDLER (om cloud-läge och company_id finns) ===
    if os.getenv("STORAGE_MODE") == "cloud" and company_id:
        _supabase_handler = SupabaseHandler(company_id=company_id)
        _supabase_handler.setLevel(logging.INFO)  # Endast INFO+ till Supabase
        logger.addHandler(_supabase_handler)
        logger.info("[LOGG] Supabase-loggning aktiverad")

    _logger = logger
    return logger


def get_logger(name: str = 'rapport_extraktor') -> logging.Logger:
    """
    Hämta logger-instans för en modul.

    Args:
        name: Modulnamn (vanligtvis __name__)

    Returns:
        Logger-instans (child av huvudlogger)
    """
    global _logger

    if _logger is None:
        # Skapa enkel logger om setup_logger inte anropats
        _logger = logging.getLogger('rapport_extraktor')
        _logger.setLevel(logging.DEBUG)

        if not _logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(logging.DEBUG)
            if sys.stdout.isatty():
                handler.setFormatter(ColoredFormatter())
            else:
                handler.setFormatter(PlainFormatter())
            _logger.addHandler(handler)

    # Returnera child logger för modulen
    return logging.getLogger(f'rapport_extraktor.{name}')


def get_log_file_path() -> Optional[Path]:
    """Returnera sökväg till aktuell loggfil."""
    return _log_file_path


def slugify(text: str) -> str:
    """
    Konvertera text till slug (lowercase, bindestreck istället för mellanslag).

    Args:
        text: Text att konvertera

    Returns:
        Slug-version av texten
    """
    import re
    import unicodedata

    # Normalisera unicode (t.ex. å -> a)
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')

    # Lowercase och ersätt mellanslag/specialtecken med bindestreck
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text).strip('-')

    return text


# ============================================
# BEKVÄMLIGHETS-FUNKTIONER FÖR LOGGNING
# ============================================

def log_extraction_start(pdf_path: str, company_name: str, pipeline: str) -> None:
    """Logga start av extraktion."""
    logger = get_logger('extraction')
    filename = Path(pdf_path).name
    logger.info(f"{'=' * 60}")
    logger.info(f"[START] Extraherar: {filename}")
    logger.info(f"[START] Bolag: {company_name} | Pipeline: {pipeline}")
    logger.info(f"{'=' * 60}")


def log_extraction_complete(
    pdf_path: str,
    tables: int,
    sections: int,
    charts: int,
    cost_sek: float,
    elapsed_seconds: float,
) -> None:
    """Logga slutförd extraktion."""
    logger = get_logger('extraction')
    filename = Path(pdf_path).name
    logger.info(f"[RESULTAT] {filename}")
    logger.info(f"   Tabeller: {tables} | Sektioner: {sections} | Grafer: {charts}")
    logger.info(f"   Kostnad: {cost_sek:.4f} SEK | Tid: {elapsed_seconds:.1f}s")


def log_ocr_progress(page_num: int, total_pages: int, elapsed: float = 0) -> None:
    """Logga OCR-progress per sida."""
    logger = get_logger('ocr')
    if elapsed > 0:
        logger.info(f"[OCR] Sida {page_num}/{total_pages} klar ({elapsed:.1f}s)")
    else:
        logger.info(f"[OCR] Sida {page_num}/{total_pages} klar")


def log_embedding_progress(
    processed: int,
    total: int,
    batch_num: int,
    success: bool = True,
) -> None:
    """Logga embedding-generering progress."""
    logger = get_logger('embeddings')
    if success:
        logger.info(f"[EMBEDDING] Batch {batch_num}: {processed}/{total} sektioner")
    else:
        logger.warning(f"[EMBEDDING] Batch {batch_num}: FEL vid generering")


def log_validation_result(
    is_valid: bool,
    tables_extracted: int,
    tables_expected: int,
    warnings: list[str],
    errors: list[str],
) -> None:
    """Logga valideringsresultat."""
    logger = get_logger('validation')

    if is_valid:
        logger.info(f"[VALIDERING] OK - {tables_extracted}/{tables_expected} tabeller")
    else:
        logger.warning(f"[VALIDERING] PROBLEM - {tables_extracted}/{tables_expected} tabeller")

    for warning in warnings:
        logger.warning(f"   VARNING: {warning}")

    for error in errors:
        logger.error(f"   FEL: {error}")


def log_api_request(model: str, operation: str, tokens_in: int = 0, tokens_out: int = 0) -> None:
    """Logga API-request (DEBUG-nivå)."""
    logger = get_logger('api')
    if tokens_in > 0 or tokens_out > 0:
        logger.debug(f"[API] {model} - {operation}: {tokens_in} in / {tokens_out} ut tokens")
    else:
        logger.debug(f"[API] {model} - {operation}")


def log_file_operation(operation: str, source: str, destination: str = "") -> None:
    """Logga filoperationer."""
    logger = get_logger('files')
    if destination:
        logger.info(f"[FIL] {operation}: {Path(source).name} -> {destination}")
    else:
        logger.info(f"[FIL] {operation}: {Path(source).name}")
