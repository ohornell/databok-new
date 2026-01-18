"""
Excel-databok byggare med Investment Bank-formatering.
Skapar professionellt formaterade finansiella rapporter.

Förenklad version - endast resultaträkning, balansräkning och kassaflöde.
Inkluderar radnormalisering för att matcha liknande radnamn mellan kvartal.
"""

import re
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def normalize_row_name(name: str) -> str:
    """
    Normalisera radnamn för jämförelse mellan kvartal.
    Hanterar variationer som:
    - "Accounts receivable" vs "Accounts receivables"
    - "Stock options" vs "Employee stock options"
    """
    if not name:
        return ""
    n = name.lower().strip()
    # Ta bort trailing 's' för plural-variationer
    if n.endswith('receivables'):
        n = n[:-1]  # receivables -> receivable
    return n

# ============================================
# INVESTMENT BANK STYLE GUIDE
# ============================================

# Färgpalett (Goldman Sachs-inspirerad)
GS_NAVY = "1F3864"
GS_LIGHT_BLUE = "D6DCE4"
GS_LIGHT_GRAY = "F2F2F2"
GS_DARK_GRAY = "404040"
GS_BLACK = "000000"

# Färgkodning för data
COLOR_HARDCODED = "0000FF"  # Blå - hårdkodade värden

# Fonter
TITLE_FONT = Font(name='Arial', size=11, bold=True, color=GS_NAVY)
SUBTITLE_FONT = Font(name='Arial', size=10, color=GS_DARK_GRAY)
HEADER_FONT = Font(name='Arial', size=9, bold=True, color="FFFFFF")
SUBHEADER_FONT = Font(name='Arial', size=8, italic=True, color=GS_DARK_GRAY)
SECTION_FONT = Font(name='Arial', size=9, bold=True, color=GS_NAVY)
LABEL_FONT = Font(name='Arial', size=9, color=GS_DARK_GRAY)
DATA_FONT = Font(name='Arial', size=9, color=COLOR_HARDCODED)
TOTAL_FONT = Font(name='Arial', size=9, bold=True, color=GS_BLACK)
SUBTOTAL_FONT = Font(name='Arial', size=9, bold=True, color=GS_DARK_GRAY)
SOURCE_FONT = Font(name='Arial', size=7, italic=True, color="808080")

# Fyllningar
HEADER_FILL = PatternFill(start_color=GS_NAVY, end_color=GS_NAVY, fill_type="solid")
SUBTOTAL_FILL = PatternFill(start_color=GS_LIGHT_BLUE, end_color=GS_LIGHT_BLUE, fill_type="solid")
TOTAL_FILL = PatternFill(start_color=GS_LIGHT_GRAY, end_color=GS_LIGHT_GRAY, fill_type="solid")

# Ramar
thin_side = Side(style='thin', color=GS_DARK_GRAY)
medium_side = Side(style='medium', color=GS_DARK_GRAY)
double_side = Side(style='double', color=GS_BLACK)

HEADER_BORDER = Border(bottom=medium_side)
SECTION_BORDER = Border(bottom=thin_side)
SUBTOTAL_BORDER = Border(top=thin_side, bottom=thin_side)
TOTAL_BORDER = Border(top=thin_side, bottom=double_side)
NO_BORDER = Border()

# Alignment
RIGHT_ALIGN = Alignment(horizontal='right', vertical='center')
LEFT_ALIGN = Alignment(horizontal='left', vertical='center', indent=1)
INDENT_ALIGN = Alignment(horizontal='left', vertical='center', indent=2)

# Nummerformat
NUMBER_FORMAT = '#,##0_);(#,##0);"-"_)'
PERCENT_FORMAT = '0.0%_);(0.0%)'


def sort_by_period(data: list[dict]) -> list[dict]:
    """
    Sortera extraherad data kronologiskt efter period.
    Hanterar format som Q1 2025, Q2 2024, etc.
    """
    def period_key(item):
        period = item.get("metadata", {}).get("period", "")
        # Extrahera Q-nummer och år
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter = int(match.group(1))
            year = int(match.group(2))
            return (year, quarter)
        return (0, 0)

    return sorted(data, key=period_key)


def collect_all_rows(data_list: list[dict], data_key: str) -> list[str]:
    """
    Samla alla unika radnamn från alla perioder med smart ordning.

    Algoritm:
    1. Använd första kvartalets ordning som bas
    2. När nya rader dyker upp i senare kvartal, försök placera dem
       på rätt position baserat på omgivande rader
    3. Normalisera radnamn för jämförelse (t.ex. "receivables" -> "receivable")
    """
    if not data_list:
        return []

    # Samla alla rader med normaliserade namn för jämförelse
    # Key: normaliserat namn, Value: (originalnamn, första_index_per_period)
    seen_normalized = {}

    # Bygg ordnad lista baserad på alla perioders ordning
    ordered_rows = []

    for period_idx, item in enumerate(data_list):
        rows = item.get(data_key, [])
        prev_normalized = None

        for row_idx, row in enumerate(rows):
            row_name = row.get("rad") or row.get("namn") or row.get("region", "")
            if not row_name:
                continue

            norm = normalize_row_name(row_name)

            if norm not in seen_normalized:
                # Ny rad - behöver placeras
                seen_normalized[norm] = row_name

                if period_idx == 0:
                    # Första perioden - lägg till direkt
                    ordered_rows.append(row_name)
                else:
                    # Senare period - försök placera efter föregående rad
                    if prev_normalized and prev_normalized in seen_normalized:
                        # Hitta positionen för föregående rad
                        prev_orig = seen_normalized[prev_normalized]
                        try:
                            prev_pos = ordered_rows.index(prev_orig)
                            ordered_rows.insert(prev_pos + 1, row_name)
                        except ValueError:
                            # Föregående rad hittades inte, lägg till sist
                            ordered_rows.append(row_name)
                    else:
                        # Ingen föregående rad att referera till, lägg till sist
                        ordered_rows.append(row_name)

            prev_normalized = norm

    return ordered_rows


def detect_row_type(row_data: dict, row_name: str) -> str:
    """
    Detektera radtyp baserat på data och namn.
    """
    # Explicit typ från extraktionen
    if row_data.get("typ") == "total":
        return "total"
    if row_data.get("typ") == "subtotal":
        return "subtotal"

    # Detektera baserat på nyckelord
    name_lower = row_name.lower()

    total_keywords = ["summa", "total", "netto", "resultat efter"]
    if any(kw in name_lower for kw in total_keywords):
        if "summa" in name_lower or "total" in name_lower:
            return "total" if "tillgångar" in name_lower or "skulder" in name_lower else "subtotal"

    return "data"


def apply_row_style(ws, row_num: int, num_cols: int, row_type: str, row_name: str):
    """
    Applicera stil på en rad baserat på typ.
    """
    for col in range(1, num_cols + 1):
        cell = ws.cell(row=row_num, column=col)

        if row_type == "section":
            cell.font = SECTION_FONT
            cell.border = SECTION_BORDER
            cell.alignment = LEFT_ALIGN
        elif row_type == "subtotal":
            cell.fill = SUBTOTAL_FILL
            cell.border = SUBTOTAL_BORDER
            cell.font = SUBTOTAL_FONT if col == 1 else Font(name='Arial', size=9, bold=True, color=COLOR_HARDCODED)
            cell.alignment = LEFT_ALIGN if col == 1 else RIGHT_ALIGN
            if col > 1:
                cell.number_format = NUMBER_FORMAT
        elif row_type == "total":
            cell.fill = TOTAL_FILL
            cell.border = TOTAL_BORDER
            cell.font = TOTAL_FONT if col == 1 else Font(name='Arial', size=9, bold=True, color=COLOR_HARDCODED)
            cell.alignment = LEFT_ALIGN if col == 1 else RIGHT_ALIGN
            if col > 1:
                cell.number_format = NUMBER_FORMAT
        else:  # data
            cell.border = NO_BORDER
            if col == 1:
                cell.font = LABEL_FONT
                cell.alignment = INDENT_ALIGN
            else:
                cell.font = DATA_FONT
                cell.alignment = RIGHT_ALIGN
                cell.number_format = NUMBER_FORMAT


def populate_financial_sheet(
    ws,
    data_list: list[dict],
    data_key: str,
    periods: list[str],
    company_name: str
):
    """
    Fyll ett finansiellt blad med data från alla perioder.
    """
    num_periods = len(periods)

    # Titel
    ws.merge_cells(f'A1:{get_column_letter(num_periods + 1)}1')
    ws['A1'] = company_name.upper()
    ws['A1'].font = TITLE_FONT
    ws['A1'].alignment = LEFT_ALIGN

    # Undertitel baserad på data_key (förenklad version)
    titles = {
        "resultatrakning": "Resultaträkning",
        "balansrakning": "Balansräkning",
        "kassaflodesanalys": "Kassaflödesanalys",
    }
    ws.merge_cells(f'A2:{get_column_letter(num_periods + 1)}2')
    ws['A2'] = titles.get(data_key, data_key.replace("_", " ").title())
    ws['A2'].font = SUBTITLE_FONT

    # Header-rad
    headers = [""]
    for item in data_list:
        period = item.get("metadata", {}).get("period", "?")
        headers.append(period)

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = LEFT_ALIGN if col == 1 else RIGHT_ALIGN
        cell.border = HEADER_BORDER

    # Valuta i första cellen
    valuta = data_list[0].get("metadata", {}).get("valuta", "TSEK") if data_list else "TSEK"
    ws.cell(row=4, column=1, value=valuta)

    # Samla alla radnamn
    all_rows = collect_all_rows(data_list, data_key)

    # Skriv data
    current_row = 6
    row_name_normalized = normalize_row_name  # Referens för snabbare anrop

    for row_name in all_rows:
        # Hämta värden för varje period
        values = [row_name]
        row_data = {}
        target_norm = row_name_normalized(row_name)

        for item in data_list:
            rows = item.get(data_key, [])
            value = None
            for r in rows:
                r_name = r.get("rad") or r.get("namn") or r.get("region", "")
                # Använd normaliserad jämförelse för att matcha liknande radnamn
                if row_name_normalized(r_name) == target_norm:
                    value = r.get("varde")
                    row_data = r
                    break
            values.append(value)

        # Skriv rad
        for col, val in enumerate(values, 1):
            ws.cell(row=current_row, column=col, value=val)

        # Detektera och applicera stil
        row_type = detect_row_type(row_data, row_name)
        apply_row_style(ws, current_row, num_periods + 1, row_type, row_name)

        current_row += 1

    # Källa
    current_row += 2
    ws.cell(row=current_row, column=1, value=f"Källa: {company_name} kvartalsrapporter").font = SOURCE_FONT

    # Kolumnbredder
    ws.column_dimensions['A'].width = 36
    for col in range(2, num_periods + 2):
        ws.column_dimensions[get_column_letter(col)].width = 14

    # Frys rubriker
    ws.freeze_panes = 'A5'

    # Dölj gridlines
    ws.sheet_view.showGridLines = False


def populate_notes_sheet(ws, data_list: list[dict], company_name: str):
    """
    Speciell hantering för noter som har annan struktur.
    """
    ws.merge_cells('A1:D1')
    ws['A1'] = company_name.upper()
    ws['A1'].font = TITLE_FONT

    ws.merge_cells('A2:D2')
    ws['A2'] = "Noter"
    ws['A2'].font = SUBTITLE_FONT

    current_row = 4

    # Samla alla noter från alla perioder
    all_notes = {}
    for item in data_list:
        period = item.get("metadata", {}).get("period", "?")
        for note in item.get("noter", []):
            note_num = note.get("nummer", 0)
            if note_num not in all_notes:
                all_notes[note_num] = {
                    "titel": note.get("titel", ""),
                    "perioder": {}
                }
            all_notes[note_num]["perioder"][period] = note

    # Skriv noter
    for note_num in sorted(all_notes.keys()):
        note_info = all_notes[note_num]

        # Not-rubrik
        ws.cell(row=current_row, column=1, value=f"Not {note_num}: {note_info['titel']}")
        ws.cell(row=current_row, column=1).font = SECTION_FONT
        current_row += 1

        # Tabeller från noten (ta från senaste period)
        if note_info["perioder"]:
            latest_note = list(note_info["perioder"].values())[-1]
            for table in latest_note.get("tabeller", []):
                # Tabellrubrik
                ws.cell(row=current_row, column=1, value=table.get("rubrik", ""))
                ws.cell(row=current_row, column=1).font = SUBTOTAL_FONT
                current_row += 1

                # Tabellrader
                for rad in table.get("rader", []):
                    ws.cell(row=current_row, column=1, value=rad.get("rad", ""))
                    ws.cell(row=current_row, column=2, value=rad.get("varde"))
                    ws.cell(row=current_row, column=1).font = LABEL_FONT
                    ws.cell(row=current_row, column=2).font = DATA_FONT
                    ws.cell(row=current_row, column=2).number_format = NUMBER_FORMAT
                    current_row += 1

        current_row += 1

    # Kolumnbredder
    ws.column_dimensions['A'].width = 50
    ws.column_dimensions['B'].width = 14

    ws.sheet_view.showGridLines = False


def build_databook(extracted_data: list[dict], output_path: str):
    """
    Bygg komplett Excel-databok från extraherad data.

    Args:
        extracted_data: Lista med extraherad data från varje PDF
        output_path: Sökväg för output Excel-fil
    """
    if not extracted_data:
        raise ValueError("Ingen data att bygga databok från")

    wb = Workbook()
    wb.remove(wb.active)

    # Sortera data kronologiskt
    sorted_data = sort_by_period(extracted_data)
    periods = [d.get("metadata", {}).get("period", "?") for d in sorted_data]

    # Hämta bolagsnamn
    company_name = sorted_data[0].get("metadata", {}).get("bolag", "Okänt bolag")

    # Flikar att skapa (förenklad version - endast 3 rapporter)
    sheets = [
        ("Resultaträkning", "resultatrakning"),
        ("Balansräkning", "balansrakning"),
        ("Kassaflöde", "kassaflodesanalys"),
    ]

    for sheet_name, data_key in sheets:
        # Kontrollera om det finns data för denna flik
        has_data = any(d.get(data_key) for d in sorted_data)
        if has_data:
            ws = wb.create_sheet(sheet_name)
            populate_financial_sheet(ws, sorted_data, data_key, periods, company_name)

    # Noter hanteras inte längre i förenklad version

    # Spara
    wb.save(output_path)
