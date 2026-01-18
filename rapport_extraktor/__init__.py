"""
Finansiell Rapportextraktor

Extraherar finansiell data från PDF-kvartalsrapporter
och skapar professionella Excel-databöcker.
"""

from .extractor import extract_all_pdfs, extract_pdf, load_cached_extractions
from .excel_builder import build_databook
from .prompts import EXTRACTION_PROMPT

__all__ = [
    "extract_all_pdfs",
    "extract_pdf",
    "load_cached_extractions",
    "build_databook",
    "EXTRACTION_PROMPT",
]
