"""
Extraktions-prompter för finansiella rapporter.
"""

# Förenklad prompt - endast tre finansiella rapporter (legacy)
EXTRACTION_PROMPT = """Du är en expert på finansiell dataextraktion.

Extrahera ENDAST följande tre rapporter från PDF:en:
1. Resultaträkning (Income Statement / Profit & Loss)
2. Balansräkning (Balance Sheet)
3. Kassaflödesanalys (Cash Flow Statement)

Returnera ENDAST giltig JSON (ingen annan text) med denna struktur:

{
  "metadata": {
    "bolag": "Bolagsnamn",
    "period": "Q1 2025",
    "valuta": "MSEK"
  },
  "resultatrakning": [
    {"rad": "Nettoomsättning", "varde": 12345},
    {"rad": "Kostnad för sålda varor", "varde": -5000},
    {"rad": "Bruttoresultat", "varde": 7345, "typ": "subtotal"},
    {"rad": "Rörelseresultat", "varde": -2000, "typ": "subtotal"},
    {"rad": "Periodens resultat", "varde": -3000, "typ": "total"}
  ],
  "balansrakning": [
    {"rad": "Goodwill", "varde": 50000},
    {"rad": "Summa anläggningstillgångar", "varde": 75000, "typ": "subtotal"},
    {"rad": "Summa tillgångar", "varde": 100000, "typ": "total"}
  ],
  "kassaflodesanalys": [
    {"rad": "Resultat efter finansiella poster", "varde": -3000},
    {"rad": "Kassaflöde från den löpande verksamheten", "varde": -2000, "typ": "subtotal"},
    {"rad": "Periodens kassaflöde", "varde": -5000, "typ": "total"}
  ]
}

VIKTIGA INSTRUKTIONER:
1. Behåll EXAKT ordning som i PDF:en - kopiera raderna i samma ordning som de visas
2. ÖVERSÄTT ALLTID alla termer till SVENSKA (även om rapporten är på engelska)
3. Numeriska värden ska vara tal (int eller float), inte text
4. Negativa tal anges med minustecken: -5000
5. Tomma celler = null
6. Markera subtotaler med "typ": "subtotal"
7. Markera totaler/summeringar med "typ": "total"
8. Ta med ALLA rader från varje rapport - missa inget

ÖVERSÄTTNINGSEXEMPEL (översätt liknande termer på samma sätt):
- Net sales / Revenue → Nettoomsättning
- Cost of sales → Kostnad för sålda varor
- Gross profit → Bruttoresultat
- Operating income → Rörelseresultat
- Income taxes / Tax → Skatt
- Net income → Periodens resultat
- Total assets → Summa tillgångar
- Cash flow from operating activities → Kassaflöde från den löpande verksamheten

AVGRÄNSNING - Resultaträkningen SLUTAR vid:
- "Periodens resultat" / "Nettoresultat" / "Result for the period" / "Net result"
- EXKLUDERA allt efter detta (resultat per aktie, antal aktier, etc.)

Returnera ENDAST JSON, ingen annan text före eller efter.
"""

NORMALIZE_PROMPT = """Du får en lista med radnamn från finansiella rapporter (resultaträkning, balansräkning, kassaflöde) från olika kvartal.
Några namn kan vara på engelska, andra på svenska. Samma koncept kan ha olika namn.

Din uppgift: Skapa en mappning som normaliserar alla namn till ett konsekvent svenskt standardnamn.

Input-lista:
{row_names}

Returnera ENDAST en JSON-mappning där:
- Key = originalnamnet (exakt som i listan)
- Value = standardiserat svenskt namn

Regler:
1. Namn som betyder samma sak ska mappas till SAMMA standardnamn
2. Använd svenska standardtermer
3. Behåll originalnamnet om det redan är korrekt standardiserat
4. Var konsekvent - "Net sales" och "Revenue" ska båda bli "Nettoomsättning"

Exempel på output:
{{
  "Net sales": "Nettoomsättning",
  "Revenue": "Nettoomsättning",
  "Nettoomsättning": "Nettoomsättning",
  "Cost of sales": "Kostnad för sålda varor",
  "Income taxes": "Skatt",
  "Inkomstskatt": "Skatt"
}}

Returnera ENDAST JSON, ingen annan text.
"""


# Full extraktion - all text, tabeller och grafer
FULL_EXTRACTION_PROMPT = """Du är en expert på dokumentanalys och dataextraktion från svenska kvartalsrapporter.

Analysera hela PDF:en och extrahera ALL information - text, tabeller OCH grafer/diagram.
Detta gäller alla bolag - extrahera allt du hittar.

Returnera ENDAST giltig JSON med denna struktur:

{
  "metadata": {
    "bolag": "Bolagsnamn från rapporten",
    "period": "Q1 2025",
    "valuta": "MSEK",
    "rapporttyp": "Kvartalsrapport",
    "sprak": "sv",
    "antal_sidor": 20
  },
  "sections": [
    {
      "title": "Sektionens rubrik",
      "page": 2,
      "type": "narrative",
      "content": "Full text från sektionen..."
    }
  ],
  "tables": [
    {
      "title": "Tabellens rubrik",
      "page": 8,
      "type": "income_statement",
      "columns": ["Q1 2025", "Q1 2024", "Helår 2024"],
      "rows": [
        {"label": "Radnamn", "values": [123, 456, 789]},
        {"label": "Summa", "values": [1000, 900, 3500], "type": "total"}
      ]
    }
  ],
  "charts": [
    {
      "title": "Nettoomsättning per kvartal",
      "page": 5,
      "chart_type": "bar",
      "x_axis": "Kvartal",
      "y_axis": "MSEK",
      "estimated": true,
      "data_points": [
        {"label": "Q1 2024", "value": 850},
        {"label": "Q2 2024", "value": 920}
      ]
    }
  ]
}

==============================================================================
STANDARDTABELLER ATT LETA EFTER (extrahera ALLA du hittar):
==============================================================================

1. Koncernens resultaträkning → type: "income_statement"
2. Koncernens balansräkning → type: "balance_sheet"
3. Koncernens kassaflödesanalys → type: "cash_flow"
4. Moderbolagets resultaträkning → type: "income_statement"
5. Moderbolagets balansräkning → type: "balance_sheet"
6. Nyckeltal / KPI-tabeller → type: "kpi"
7. Segmentdata (geografisk/produkt/region) → type: "segment"
8. ALLA ANDRA tabeller → type: "other"

VIKTIGT: Extrahera VARJE tabell i dokumentet. Missa ingen!
Använd "other" för tabeller som inte passar övriga kategorier.

==============================================================================
INSTRUKTIONER FÖR SEKTIONER:
==============================================================================

- Extrahera alla textsektioner: VD-ord, marknadsöversikt, verksamhetsbeskrivning, etc.
- Inkludera sidnummer där sektionen börjar
- Behåll ALL text - förkorta inte
- BEHÅLL ORIGINALSPRÅKET - översätt inte
- Typer: narrative, summary, highlights, other

==============================================================================
INSTRUKTIONER FÖR TABELLER:
==============================================================================

KOLUMNRUBRIKER - KRITISKT:
- Om rubriken sträcker sig över flera underkolumner, KOMBINERA dem:
  Exempel: "April-juni" med "2025" och "2024" under →
           columns: ["April-juni 2025", "April-juni 2024"]
- Använd ALDRIG null eller tom sträng som kolumnrubrik
- Varje kolumn MÅSTE ha ett beskrivande namn

VÄRDEN:
- Numeriska värden som tal (int/float), inte text
- Tomma celler i DATA = null (men INTE i kolumnrubriker)
- Antal values MÅSTE matcha antal columns

RADTYPER:
- Markera subtotaler med "type": "subtotal"
- Markera totaler/summeringar med "type": "total"
- Markera rubriker utan värden med "type": "header"

VIKTIGT - INKLUDERA ALLA RADER:
- Inkludera ALLTID "Hänförligt till"-sektioner efter resultat/nettoresultat
  Exempel: "Hänförligt till moderbolagets ägare", "Hänförligt till innehav utan bestämmande inflytande"
- Inkludera ALL information som finns i tabellen, även fotnot-rader
- Missa INGA rader - extrahera tabellen exakt som den visas

BEHÅLL ORIGINALSPRÅKET på alla radnamn och rubriker.

==============================================================================
INSTRUKTIONER FÖR GRAFER/DIAGRAM:
==============================================================================

- Extrahera ALLA grafer och diagram (stapeldiagram, linjediagram, cirkeldiagram, etc.)
- chart_type: bar, line, pie, area, other
- Inkludera axeletiketter (x_axis, y_axis) om synliga

**VIKTIGT - estimated-fältet:**
- `estimated: false` = Exakta värden visas i grafen (datapunktsetiketter, värden bredvid staplar)
- `estimated: true` = Värden uppskattade visuellt från grafen (avläst från axlar)

Om du är osäker, sätt estimated: true.

==============================================================================
ORDNING OCH FORMAT:
==============================================================================

- Behåll ordningen som i PDF:en
- Tabellrader i samma ordning som originalet
- sprak i metadata: "sv" för svenska, "en" för engelska, "mixed" för blandat

Returnera ENDAST JSON, ingen annan text före eller efter.
"""


# Full extraktion UTAN grafer - sparar tid och tokens
FULL_EXTRACTION_PROMPT_NO_CHARTS = """Du är en expert på dokumentanalys och dataextraktion från svenska kvartalsrapporter.

Analysera hela PDF:en och extrahera ALL information - text och tabeller (INGA grafer/diagram).
Detta gäller alla bolag - extrahera allt du hittar.

Returnera ENDAST giltig JSON med denna struktur:

{
  "metadata": {
    "bolag": "Bolagsnamn från rapporten",
    "period": "Q1 2025",
    "valuta": "MSEK",
    "rapporttyp": "Kvartalsrapport",
    "sprak": "sv",
    "antal_sidor": 20
  },
  "sections": [
    {
      "title": "Sektionens rubrik",
      "page": 2,
      "type": "narrative",
      "content": "Full text från sektionen..."
    }
  ],
  "tables": [
    {
      "title": "Tabellens rubrik",
      "page": 8,
      "type": "income_statement",
      "columns": ["Q1 2025", "Q1 2024", "Helår 2024"],
      "rows": [
        {"label": "Radnamn", "values": [123, 456, 789]},
        {"label": "Summa", "values": [1000, 900, 3500], "type": "total"}
      ]
    }
  ]
}

==============================================================================
STANDARDTABELLER ATT LETA EFTER (extrahera ALLA du hittar):
==============================================================================

1. Koncernens resultaträkning → type: "income_statement"
2. Koncernens balansräkning → type: "balance_sheet"
3. Koncernens kassaflödesanalys → type: "cash_flow"
4. Moderbolagets resultaträkning → type: "income_statement"
5. Moderbolagets balansräkning → type: "balance_sheet"
6. Nyckeltal / KPI-tabeller → type: "kpi"
7. Segmentdata (geografisk/produkt/region) → type: "segment"
8. ALLA ANDRA tabeller → type: "other"

VIKTIGT: Extrahera VARJE tabell i dokumentet. Missa ingen!
Använd "other" för tabeller som inte passar övriga kategorier.

==============================================================================
INSTRUKTIONER FÖR SEKTIONER:
==============================================================================

- Extrahera alla textsektioner: VD-ord, marknadsöversikt, verksamhetsbeskrivning, etc.
- Inkludera sidnummer där sektionen börjar
- Behåll ALL text - förkorta inte
- BEHÅLL ORIGINALSPRÅKET - översätt inte
- Typer: narrative, summary, highlights, other

==============================================================================
INSTRUKTIONER FÖR TABELLER:
==============================================================================

KOLUMNRUBRIKER - KRITISKT:
- Om rubriken sträcker sig över flera underkolumner, KOMBINERA dem:
  Exempel: "April-juni" med "2025" och "2024" under →
           columns: ["April-juni 2025", "April-juni 2024"]
- Använd ALDRIG null eller tom sträng som kolumnrubrik
- Varje kolumn MÅSTE ha ett beskrivande namn

VÄRDEN:
- Numeriska värden som tal (int/float), inte text
- Tomma celler i DATA = null (men INTE i kolumnrubriker)
- Antal values MÅSTE matcha antal columns

RADTYPER:
- Markera subtotaler med "type": "subtotal"
- Markera totaler/summeringar med "type": "total"
- Markera rubriker utan värden med "type": "header"

VIKTIGT - INKLUDERA ALLA RADER:
- Inkludera ALLTID "Hänförligt till"-sektioner efter resultat/nettoresultat
  Exempel: "Hänförligt till moderbolagets ägare", "Hänförligt till innehav utan bestämmande inflytande"
- Inkludera ALL information som finns i tabellen, även fotnot-rader
- Missa INGA rader - extrahera tabellen exakt som den visas

BEHÅLL ORIGINALSPRÅKET på alla radnamn och rubriker.

==============================================================================
ORDNING OCH FORMAT:
==============================================================================

- Behåll ordningen som i PDF:en
- Tabellrader i samma ordning som originalet
- sprak i metadata: "sv" för svenska, "en" för engelska, "mixed" för blandat

Returnera ENDAST JSON, ingen annan text före eller efter.
"""
