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
        {"label": "Nettoomsättning", "values": [842, 841, 3609], "order": 1},
        {"label": "Kostnad för sålda varor", "values": [-359, -361, -1470], "order": 2},
        {"label": "Bruttoresultat", "values": [483, 481, 2139], "order": 3, "type": "subtotal"},
        {"label": "Rörelseresultat", "values": [151, 167, 783], "order": 4, "type": "subtotal"}
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
INSTRUKTIONER FÖR TABELLER - KRITISKT
==============================================================================

KOLUMNRUBRIKER:
- Identifiera ALLA kolumnrubriker EXAKT som de står i PDF:en
- Om hierarkiska kolumner (år → kvartal), kombinera: "Q1 2025", "Q2 2025"
- Använd ALDRIG null eller tom sträng som kolumnrubrik
- Varje kolumn MÅSTE ha ett beskrivande namn

RADORDNING - KRITISKT:
- Varje rad MÅSTE ha "order": 1, 2, 3... baserat på position i PDF:en
- Detta säkerställer att summor kommer EFTER sina komponenter
- Behåll EXAKT ordning som i originaltabellen

VÄRDEN - KRITISKT:
- Numeriska värden som tal (int/float), inte text
- Tomma celler = null (INTE "n.a." eller tom sträng)
- Antal values MÅSTE matcha antal columns EXAKT
- Extrahera värdet som står i PDF:en - beräkna ALDRIG själv

RADTYPER:
- "type": "header" = Rubrikrad utan värden (t.ex. "TILLGÅNGAR", "Anläggningstillgångar")
- "type": "subtotal" = Delsumma (t.ex. "Bruttoresultat", "Summa omsättningstillgångar")
- "type": "total" = Slutsumma (t.ex. "Summa tillgångar", "Periodens resultat")
- Utelämna type för vanliga datarader

SEPARATA TABELLER:
- Koncernens resultaträkning = EN tabell
- Moderbolagets resultaträkning = SEPARAT tabell (med eget title)
- Blanda ALDRIG koncern och moderbolag i samma tabell

VIKTIGT - INKLUDERA ALLT:
- Inkludera ALLTID "Hänförligt till"-sektioner
- Inkludera resultat per aktie, antal aktier
- Missa INGA rader - extrahera tabellen EXAKT som den visas

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


# =============================================================================
# MULTI-PASS PIPELINE PROMPTS
# =============================================================================

# Pass 1: Strukturidentifiering (Haiku)
PASS_1_STRUCTURE_PROMPT = """Du är en expert på dokumentanalys.

Analysera kvartalsrapporten och identifiera ALLA element.

RETURNERA ENDAST JSON:

{
  "metadata": {
    "bolag": "Bolagsnamn",
    "period": "Q4 2024",
    "valuta": "MSEK",
    "sprak": "sv",
    "number_format": "swedish",
    "antal_sidor": 20
  },
  "structure_map": {
    "tables": [...],
    "sections": [...],
    "charts": [...]
  }
}

==============================================================================
SPRÅK OCH NUMMERFORMAT
==============================================================================

SVENSKT: sprak: "sv", number_format: "swedish"
- Termer: Nettoomsättning, Rörelseresultat
- Tal: 1 234,56

ENGELSKT: sprak: "en", number_format: "english"
- Termer: Net sales, Operating profit
- Tal: 1,234.56

==============================================================================
TABELLER - MISSA INGA!
==============================================================================

GÅ IGENOM VARJE SIDA och hitta:

□ Finansiella rapporter (resultat, balans, kassaflöde, EK-förändring)
□ Kvartalssammanställningar (Q1-Q4, flera år) - ofta SIST i dokumentet!
□ Segment/region/produkt-data
□ Tillväxt-tabeller (%, förändringar)
□ Nyckeltal
□ Noter

TABELLTYPER:
income_statement, balance_sheet, cash_flow, equity_changes,
parent_income_statement, parent_balance_sheet,
quarterly, kpi, segment, growth, note, other

TABELL-FORMAT:
{
  "id": "table_1",
  "title": "Koncernens resultaträkning",
  "type": "income_statement",
  "entity": "group",
  "page": 13,
  "row_count_estimate": 20,
  "column_headers": ["", "Okt-dec 2024", "Okt-dec 2023", "Jan-dec 2024"],
  "has_hierarchical_headers": true
}

ENTITY: "group" (koncern) eller "parent" (moderbolag)

KOLUMNRUBRIKER - om hierarkiska (två nivåer):
Kombinera: "Oktober-december" + "2024" → "Okt-dec 2024"

IGNORERA "Not"/"Note"-kolumner i column_headers!

==============================================================================
SEKTIONER OCH GRAFER
==============================================================================

SEKTIONSTYPER: narrative, summary, notes, outlook, other
GRAFTYPER: bar, line, pie, area, other

SEKTION-FORMAT:
{
  "id": "section_1",
  "title": "VD-kommentar",
  "type": "narrative",
  "page": 5,
  "estimated_word_count": 500
}

GRAF-FORMAT:
{
  "id": "chart_1",
  "title": "Nettoomsättning per kvartal",
  "chart_type": "bar",
  "page": 7
}

==============================================================================
CHECKLISTA
==============================================================================

□ Alla tabeller på ALLA sidor?
□ Koncern och moderbolag separerade?
□ Kvartalssammanställningar (sista sidorna)?
□ Noter identifierade?
"""


# Pass 2: Tabellextraktion (Sonnet)
PASS_2_TABLES_PROMPT = """Du är en expert på finansiell dataextraktion.

DOKUMENTINFO:
- Språk: {language}
- Nummerformat: {number_format}

STRUKTURKARTA:
{structure_map_json}

EXTRAHERA DESSA ELEMENT: {element_ids}

RETURNERA JSON:

{{
  "tables": [
    {{
      "id": "table_1",
      "title": "Koncernens resultaträkning",
      "type": "income_statement",
      "entity": "group",
      "page": 13,
      "currency": "MSEK",
      "columns": ["", "Okt-dec 2024", "Okt-dec 2023", "Jan-dec 2024", "Jan-dec 2023"],
      "rows": [
        {{"label": "Nettoomsättning", "label_en": "Net sales", "values": [null, 959, 904, 3609, 3512], "order": 1}},
        {{"label": "Bruttoresultat", "label_en": "Gross profit", "values": [null, 586, 514, 2139, 1977], "order": 3, "type": "subtotal"}}
      ],
      "footnotes": []
    }}
  ],
  "charts": [
    {{
      "id": "chart_1",
      "title": "Nettoomsättning",
      "chart_type": "bar",
      "page": 5,
      "estimated": true,
      "data_points": [{{"label": "Q1 2024", "value": 841}}]
    }}
  ]
}}

==============================================================================
NUMERISK PARSING - KRITISKT!
==============================================================================

ALLA VÄRDEN SKA VARA JSON-TAL (punkt som decimal).

SVENSKT FORMAT (number_format: "swedish"):
| PDF visar | JSON output |
|-----------|-------------|
| 35,1 | 35.1 |
| 1 225 | 1225 |
| 1 225,50 | 1225.5 |
| -373 | -373 |
| 373- | -373 |
| (373) | -373 |

ENGELSKT FORMAT (number_format: "english"):
| PDF visar | JSON output |
|-----------|-------------|
| 35.1 | 35.1 |
| 1,225 | 1225 |
| 1,225.50 | 1225.5 |
| -373 | -373 |
| (373) | -373 |

REGEL FÖR KOMMA I ENGELSKT FORMAT:
- "1,225" (exakt 3 siffror efter komma) = tusentalsavgränsare → 1225
- "1,22" (1-2 siffror efter komma) = decimal → 1.22

TOMMA VÄRDEN → null:
- "–" (em-dash), "—" (lång em-dash), "-" (ensamt bindestreck)
- "" (tom cell)
- "n/a", "N/A", "n.a.", "n.m."
- "ej tillämplig"

PROCENTTECKEN - ta bort:
- "61,1%" → 61.1
- "61.1%" → 61.1

==============================================================================
KOLUMNER
==============================================================================

Första kolumnen är ALLTID radnamn (tom rubrik "").
Antal values MÅSTE matcha antal columns EXAKT.

Om hierarkiska rubriker - kombinera:
                    Okt-dec     Jan-dec
                    2024  2023  2024  2023

Blir: ["", "Okt-dec 2024", "Okt-dec 2023", "Jan-dec 2024", "Jan-dec 2023"]

==============================================================================
RADER
==============================================================================

VARJE RAD:
{{
  "label": "Nettoomsättning",
  "label_en": "Net sales",
  "values": [null, 959, 904, 3609, 3512],
  "order": 1,
  "type": "data",
  "indent": 0
}}

FÄLT:
- label: Exakt text från PDF
- label_en: Engelsk översättning (om känd, annars utelämna)
- values: [null, ...värden...] - null först för label-kolumnen
- order: 1, 2, 3... baserat på position i PDF
- type: "data" | "header" | "subtotal" | "total" | "memo"
- indent: 0, 1, 2... för hierarkisk struktur (0 = default)

RADTYPER:
| Indikator | type |
|-----------|------|
| (vanlig rad) | "data" (eller utelämna) |
| Rubrik utan värden | "header" |
| Bruttoresultat, Rörelseresultat | "subtotal" |
| Summa, Totalt, Total | "total" |
| varav, därav, of which | "memo" |

VIKTIGT FÖR VALUES:
- Första värdet är ALLTID null (label-kolumnen)
- Om PDF visar ett tal → det talet i output (aldrig null)
- Extrahera exakt vad som står - beräkna ALDRIG själv

==============================================================================
FOTNOTER
==============================================================================

Om cell har fotnot (t.ex. "139*" eller "139¹"):
1. Extrahera värdet: 139
2. Samla fotnoter i tabellens footnotes-array:

"footnotes": [
  {{"marker": "*", "text": "Justerat för engångsposter"}}
]

==============================================================================
ENGELSKA TERMER (label_en)
==============================================================================

Lägg till label_en för vanliga termer:

| Svenska | label_en |
|---------|----------|
| Nettoomsättning | Net sales |
| Kostnad för sålda varor | Cost of goods sold |
| Bruttoresultat | Gross profit |
| Rörelseresultat | Operating profit |
| Finansnetto | Net financial items |
| Resultat före skatt | Profit before tax |
| Nettoresultat | Net profit |
| Summa tillgångar | Total assets |
| Summa eget kapital | Total equity |

Utelämna label_en om du är osäker.

==============================================================================
GRAFER
==============================================================================

{{
  "id": "chart_1",
  "title": "Nettoomsättning per kvartal",
  "chart_type": "bar",
  "page": 5,
  "estimated": true,
  "data_points": [
    {{"label": "Q1 2024", "value": 841}}
  ]
}}

- estimated: true = värden avlästa visuellt (default)
- estimated: false = exakta värden visas som etiketter

==============================================================================
CHECKLISTA
==============================================================================

☐ Är alla tal konverterade korrekt (komma → punkt)?
☐ Är negativa tal korrekt hanterade?
☐ Är tomma celler null (inte 0)?
☐ Matchar antal values antal columns för varje rad?
☐ Är första värdet i values alltid null?
☐ Har alla rader order-nummer?
"""


# Pass 3: Textextraktion (Haiku)
PASS_3_TEXT_PROMPT = """Du är en expert på textextraktion.

DOKUMENTSPRÅK: {language}

STRUKTURKARTA:
{structure_map_json}

EXTRAHERA DESSA SEKTIONER: {section_ids}

RETURNERA JSON:

{{
  "sections": [
    {{
      "id": "section_1",
      "title": "VD-kommentar",
      "type": "narrative",
      "page": 5,
      "content": "Full text här..."
    }}
  ],
  "quotes": [
    {{
      "text": "2024 var ett rekordår...",
      "source": "VD-kommentar",
      "page": 6
    }}
  ],
  "contacts": [
    {{
      "name": "Anna Andersson",
      "title": "CFO",
      "phone": "+46 70 123 45 67",
      "email": "ir@company.com"
    }}
  ],
  "calendar": [
    {{
      "event": "Årsredovisning 2024",
      "date": "2025-03-27"
    }},
    {{
      "event": "Delårsrapport Q1",
      "date": "2025-04-24"
    }}
  ],
  "footnotes": [
    {{
      "marker": "*",
      "text": "Jämförelsesiffror har justerats...",
      "page": 8
    }}
  ]
}}

==============================================================================
SEKTIONER
==============================================================================

- Behåll ALL text - förkorta INTE
- Behåll originalspråket
- Markera stycken med \\n\\n
- Behåll punktlistor (• eller -)
- Hoppa över tabeller (hanteras i Pass 2)

==============================================================================
CITAT (quotes)
==============================================================================

Identifiera framhävda citat:
- Större font
- Citattecken
- Textruta/highlight
- Pull quotes

Ofta VD-citat eller nyckelbudskap.

==============================================================================
KONTAKTER (contacts)
==============================================================================

Hitta kontaktinformation:
- IR-kontakt
- Presskontakt
- CFO för frågor
- Telefon, email

==============================================================================
KALENDER (calendar)
==============================================================================

Finansiell kalender:
- Kommande rapporter
- Årsstämma
- Utdelningsdatum

Formatera datum som: YYYY-MM-DD

==============================================================================
FOTNOTER (footnotes)
==============================================================================

Dokumentövergripande fotnoter som inte hör till specifik tabell:
- Redovisningsprinciper
- Justeringar av jämförelsetal
- Definitioner
"""
