# Investment Bank Financial Databook Skill

Skapa professionella finansiella databöcker i Excel med Investment Bank-formatering (Goldman Sachs-stil).

## Användning

När användaren ber om en finansiell databok för ett bolag:

1. Hämta kvartalsrapporter från bolagets investor relations-sida
2. Extrahera koncernens finansiella data (Resultaträkning, Balansräkning, Kassaflödesanalys)
3. Skapa Excel-fil med tre flikar enligt formateringen nedan

## Formatering

### Färgpalett
```python
GS_NAVY = "1F3864"       # Mörkblå - headers
GS_LIGHT_BLUE = "D6DCE4" # Ljusblå - subtotaler
GS_LIGHT_GRAY = "F2F2F2" # Ljusgrå - totaler
GS_DARK_GRAY = "404040"  # Mörkgrå - text
GS_BLACK = "000000"      # Svart
```

### Färgkodning av data
```python
COLOR_HARDCODED = "0000FF"  # Blå - hårdkodade värden/inputs (inkl 0)
COLOR_FORMULA = "000000"    # Svart - formler
COLOR_LINK = "008000"       # Grön - cross-sheet länkar
```

### Typsnitt
- **Typsnitt:** Arial
- **Storlek:** 9pt för data, 11pt för titlar
- **Titel:** Bold, mörkblå (#1F3864)
- **Headers:** Bold, vit text på mörkblå bakgrund
- **Sektionsrubriker:** Bold, mörkblå text
- **Subtotaler:** Bold, mörkgrå text, ljusblå bakgrund
- **Totaler:** Bold, svart text, ljusgrå bakgrund
- **Källa:** 7pt, italic, grå (#808080)

### Nummerformat
```python
NUMBER_FORMAT = '#,##0_);(#,##0);"-"_)'  # Parenteser för negativa, "-" för noll
```

### Kantlinjer
- **Headers:** Medium linje under
- **Sektionsrubriker:** Tunn linje under (på alla kolumner)
- **Subtotaler:** Tunn linje över och under
- **Totaler:** Tunn linje över, dubbel linje under
- **Datarader:** Inga kantlinjer

### Layout per flik

#### Rad 1: Bolagsnamn
```
[BOLAGSNAMN] (publ)
```
- Font: Arial 11pt, bold, mörkblå
- Mergad över alla kolumner

#### Rad 2: Rapporttyp
```
Consolidated Income Statement / Consolidated Balance Sheet / Consolidated Statement of Cash Flows
```
- Font: Arial 10pt, mörkgrå

#### Rad 4: Header (mörkblå bakgrund)
```
| SEK '000 | Q1 [ÅR] | Q2 [ÅR] | Q3 [ÅR] | Q4 [ÅR] |
```
- Första kolumnen: Vänsterjusterad
- Övriga kolumner: Högerjusterade

#### Rad 5: Subheader
**Income Statement & Cash Flow:**
```
|          | Jan-Mar | Apr-Jun | Jul-Sep | Oct-Dec |
```

**Balance Sheet:**
```
|          | 31-Mar-[ÅR] | 30-Jun-[ÅR] | 30-Sep-[ÅR] | 31-Dec-[ÅR] |
```
- Textformat (@) för att undvika datumtolkning
- Högerjusterade

#### Rad 7+: Data
- Sektionsrubriker: Vänsterjusterade, bold, mörkblå
- Datarader: Indenterade (indent=2), blå siffror för inputs
- Subtotaler: Formler i svart, ljusblå bakgrund
- Totaler: Formler i svart, ljusgrå bakgrund, dubbel underlinje

### Övriga inställningar
- **Stödlinjer:** Dolda (`showGridLines = False`)
- **Frysta rubriker:** `freeze_panes = 'A6'`
- **Kolumnbredder:** A=32-36, B-D=14

## Excel-struktur

### Flik 1: Income Statement
```
Revenue
  Net sales
  Capitalized development costs
  Other operating income
  Total revenue (formel)

Operating expenses
  Cost of goods sold
  Other external costs
  Personnel costs
  Depreciation & amortization
  Other operating expenses
  Total operating expenses (formel)

Operating income (EBIT) (formel, total)

Financial items
  Interest income
  Interest expense
  Net financial items (formel)

Income before tax (EBT) (formel)
Income tax
Net income (formel, total)

Source: [Källa]
```

### Flik 2: Balance Sheet
```
ASSETS

Non-current assets
  Goodwill
  Capitalized development
  Patents
  Total intangible assets (formel)
  Machinery & equipment
  Fixtures & fittings
  Total tangible assets (formel)
  Deferred tax assets
  Total non-current assets (formel)

Current assets
  Inventory
  Trade receivables
  Other receivables
  Prepaid expenses
  Cash and cash equivalents
  Total current assets (formel)

TOTAL ASSETS (formel, total)

EQUITY AND LIABILITIES

Shareholders' equity
  Share capital
  Other contributed capital
  Retained earnings
  Total shareholders' equity (formel)

Current liabilities
  Trade payables
  Other liabilities
  Accrued expenses
  Total current liabilities (formel)

TOTAL EQUITY AND LIABILITIES (formel, total)

Source: [Källa]
```

### Flik 3: Cash Flow
```
Cash flows from operating activities
  Income before tax (länk till Income Statement, grön)
  Adjustments for non-cash items
  Cash flow before working capital changes (formel)
  Change in inventory
  Change in receivables
  Change in payables
  Net cash from operating activities (formel)

Cash flows from investing activities
  Investment in intangible assets
  Investment in tangible assets
  Change in financial assets
  Net cash from investing activities (formel)

Cash flows from financing activities
  Proceeds from share issue
  Share issue costs
  Net cash from financing activities (formel)

Net change in cash (formel, total)
Cash at beginning of period
FX effect on cash
Cash at end of period (formel, total)

Source: [Källa]
```

## Python-kod

Se [create_databook.py](create_databook.py) för komplett implementation.

### Nyckelkomponenter

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# Använd formler istället för hårdkodade summor
"=SUM(B8:B10)"  # Summerar celler
"='Income Statement'!B28"  # Cross-sheet länk

# Färgkodning baserat på datatyp
is_formula = isinstance(value, str) and value.startswith("=")
is_link = is_formula and ("!" in value)
is_hardcoded = isinstance(value, (int, float)) and col > 1
```

## Kvalitetskontroll

Efter skapande, verifiera:
1. Alla formler beräknas korrekt
2. Balansräkningen balanserar (Tillgångar = EK + Skulder)
3. Kassaflödet stämmer (Ingående + Periodens = Utgående)
4. Cross-sheet länkar fungerar
5. Färgkodning är konsekvent
