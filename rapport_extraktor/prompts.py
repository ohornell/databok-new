"""
Extraktions-prompter för multi-pass pipeline.
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
SPRÅK OCH NUMMERFORMAT - KRITISKT!
==============================================================================

DETEKTERA SPRÅK BASERAT PÅ DOKUMENTETS TEXT, INTE VALUTA ELLER BOLAGSNAMN!

VIKTIGT: Bolag kan skriva rapporter på annat språk än sitt hemland.
- Svenskt bolag kan skriva på engelska
- Norskt bolag kan skriva på engelska
- SEK/MSEK/NOK/MNOK är VALUTA, inte språkindikator!
Kolla den faktiska texten i dokumentet!

ENGELSKA INDIKATORER (sprak: "en", number_format: "english"):
- "Interim Report", "Quarterly Report", "Annual Report"
- "Revenue", "Net sales", "Operating profit", "Gross profit"
- "Total assets", "Total equity", "Cash flow"
- "The Group", "Shareholders", "Board of Directors"
- Månader: January, February, March, April, May, June...

SVENSKA INDIKATORER (sprak: "sv", number_format: "swedish"):
- "Kvartalsrapport", "Delårsrapport", "Årsredovisning"
- "Nettoomsättning", "Rörelseresultat", "Bruttoresultat"
- "Koncernen", "Aktieägare", "Styrelsen"
- Månader: januari, februari, mars, april, maj, juni...

NORSKA INDIKATORER (sprak: "no", number_format: "swedish"):
- "Kvartalsrapport", "Delårsrapport", "Årsregnskap"
- "Driftsinntekter", "Driftsresultat", "Konsernet"
- "Aksjonærer", "Styret", "Eiendeler", "Gjeld"
- Månader: januar, februar, mars, april, mai, juni...

NUMMERFORMAT:
- swedish: 1 234,56 (mellanslag + komma) - används för sv och no
- english: 1,234.56 (komma + punkt) - används för en

==============================================================================
TABELLER - MISSA INGA!
==============================================================================

GÅ IGENOM VARJE SIDA och hitta:

□ Finansiella rapporter (resultat, balans, kassaflöde, EK-förändring)
□ Kvartalssammanställningar (Q1-Q4, flera år) - ofta SIST i dokumentet!
□ Segment/region/produkt-data (intäkter per division, geografi)
□ Tillväxt-tabeller (%, förändringar)
□ Nyckeltal (KPIs, ratios)
□ Noter
□ Aktiedata (aktieantal, ägarstruktur, utdelning)
□ Personaldata (headcount, anställda per division)
□ Kostnadsanalys (operating costs, compensation)
□ Kapitalstruktur (core capital, regulatory ratios)
□ Marknadsvolymer (ECM, DCM, M&A volumes)
□ Forward contracts / terminkontrakt

TABELLTYPER:
income_statement, balance_sheet, cash_flow, equity_changes,
parent_income_statement, parent_balance_sheet,
quarterly, kpi, segment, growth, note, shareholder, personnel, costs, capital, market_volumes, other

OBS: Det kan finnas andra tabelltyper som inte listas ovan.
Identifiera och inkludera ALLA tabeller oavsett typ - använd "other" för okända typer.

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

VIKTIGT OM SIDNUMMER (page):
- "page" ska vara PDF-sidnummer (1 = första sidan i PDF-filen)
- Räkna från 1, oavsett vad som står tryckt på sidan
- Om omslaget är PDF-sida 1, så är nästa sida PDF-sida 2
- Använd INTE det tryckta sidnumret (t.ex. "sida 5" på sidan)

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
  "page": 7,
  "has_data_labels": true,
  "data_point_count": 8
}

VIKTIGT FÖR GRAFER:
- has_data_labels: true om diagrammet har SYNLIGA VÄRDEN på/över staplarna
- data_point_count: uppskattning av antal datapunkter (antal staplar, punkter, etc.)

Grafer med has_data_labels=true innehåller ofta FLERÅRIG historisk data som
måste extraheras noggrant i Pass 2!

OBS: "page" för sektioner och grafer följer samma regel som tabeller -
använd PDF-sidnummer (1 = första sidan i filen), INTE tryckt sidnummer.

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

SÄRSKILD UPPMÄRKSAMHET: Grafer markerade med "has_data_labels": true
innehåller SYNLIGA DATAVÄRDEN som måste extraheras noggrant.
Läs varje etikett/siffra på diagrammet - missa INGA datapunkter!

==============================================================================
KRITISKT - EXTRAHERA ALLA TABELLER!
==============================================================================

Du MÅSTE extrahera VARJE tabell i listan ovan. Missa INGEN tabell!
- Varje tabell-ID i listan MÅSTE finnas i din output
- Om en tabell är svår att läsa - gör ditt bästa, men inkludera den ALLTID
- Hellre en tabell med några osäkra värden än en saknad tabell
- Kontrollera att antal tabeller i output matchar antal i input-listan

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
        {{"label": "Nettoomsättning", "label_en": "Net sales", "note_ref": "4", "values": [null, 959, 904, 3609, 3512], "order": 1}},
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

SVENSKT/NORSKT FORMAT (number_format: "swedish"):
Används för språk: sv, no
| PDF visar | JSON output |
|-----------|-------------|
| 35,1 | 35.1 |
| 1 225 | 1225 |
| 1 225,50 | 1225.5 |
| -373 | -373 |
| 373- | -373 |
| (373) | -373 |

ENGELSKT FORMAT (number_format: "english"):
Används för språk: en
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

REGEL FÖR SPRÅK → NUMMERFORMAT:
- sv (svenska) → swedish
- no (norska) → swedish (SAMMA som svenska!)
- en (engelska) → english

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
  "note_ref": "4",
  "values": [null, 959, 904, 3609, 3512],
  "order": 1,
  "type": "data",
  "indent": 0
}}

FÄLT:
- label: Exakt text från PDF
- label_en: Engelsk översättning (om känd, annars utelämna)
- note_ref: Notreferens om raden har en (t.ex. "4", "5") - UTELÄMNA om ingen not
- values: [null, ...värden...] - null först för label-kolumnen. INKLUDERA ALDRIG notreferenser här!
- order: 1, 2, 3... baserat på position i PDF
- type: "data" | "header" | "subtotal" | "total" | "memo"
- indent: 0, 1, 2... för hierarkisk struktur (0 = default)

NOTREFERENSER - KRITISKT:
- Om PDF har en "Not"-kolumn med siffror som "4", "5" → spara i note_ref, INTE i values
- values ska ENDAST innehålla numeriska finansiella data
- Exempel: Om PDF visar "Nettoomsättning | 4 | 835 | 867" där 4 är notreferens:
  → note_ref: "4", values: [null, 835, 867]

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
ENGELSKA TERMER (label_en) - KRITISKT FÖR CROSS-LANGUAGE JÄMFÖRELSE
==============================================================================

Lägg till label_en för ALLA vanliga finansiella termer.
Detta möjliggör jämförelse mellan bolag på olika språk.

SVENSKA → ENGELSKA:
| Svenska | label_en |
|---------|----------|
| Nettoomsättning | Net sales |
| Kostnad för sålda varor | Cost of goods sold |
| Bruttoresultat | Gross profit |
| Försäljningskostnader | Selling expenses |
| Administrationskostnader | Administrative expenses |
| Rörelseresultat | Operating profit |
| Finansiella intäkter | Financial income |
| Finansiella kostnader | Financial expenses |
| Finansnetto | Net financial items |
| Resultat före skatt | Profit before tax |
| Skatt | Tax |
| Nettoresultat | Net profit |
| Periodens resultat | Profit for the period |
| Summa tillgångar | Total assets |
| Summa eget kapital | Total equity |
| Summa skulder | Total liabilities |
| Kassaflöde från rörelsen | Cash flow from operations |

NORSKA → ENGELSKA:
| Norska | label_en |
|--------|----------|
| Driftsinntekter | Net sales |
| Salgsinntekter | Net sales |
| Varekostnad | Cost of goods sold |
| Bruttoresultat | Gross profit |
| Lønnskostnader | Personnel expenses |
| Andre driftskostnader | Other operating expenses |
| Driftsresultat | Operating profit |
| Finansinntekter | Financial income |
| Finanskostnader | Financial expenses |
| Resultat før skatt | Profit before tax |
| Skattekostnad | Tax |
| Årsresultat | Net profit |
| Periodens resultat | Profit for the period |
| Sum eiendeler | Total assets |
| Sum egenkapital | Total equity |
| Sum gjeld | Total liabilities |
| Kontantstrøm fra drift | Cash flow from operations |

ENGELSKA (behåll label_en = label):
Om dokumentet är på engelska, sätt label_en = samma som label.

Utelämna label_en ENDAST om du är osäker på korrekt översättning.

==============================================================================
GRAFER - KRITISKT FÖR VISUELLA DIAGRAM MED DATAVÄRDEN
==============================================================================

VIKTIGT: Många rapporter har KPI-sidor med stapel-/linjediagram där faktiska
värden visas DIREKT PÅ diagrammet (som etiketter på/över staplarna).

DESSA DIAGRAM INNEHÅLLER OFTA FLERÅRIG DATA - MISSA INTE TIDIGARE ÅR!

Typiskt exempel (KPI-sida med 3 diagram):
- "Operating revenues" - stapeldiagram med 7 kvartal/år
- "Operating margin" - stapeldiagram med procentvärden per period
- "Diluted EPS" - stapeldiagram med EPS per kvartal

FÖR VARJE DIAGRAM - EXTRAHERA ALLA SYNLIGA DATAPUNKTER:
1. Läs VARJE etikett på/över staplarna noggrant
2. Identifiera tidsperioden för varje stapel (Q1, Q2, Q3, Q4, helår, etc.)
3. Inkludera ALLA perioder - även tidigare år (2021, 2022, 2023, etc.)
4. Om det finns staplad data (t.ex. olika segment), extrahera varje segment

GRAFFORMAT:
{{
  "id": "chart_1",
  "title": "Operating revenues (NOKm)",
  "chart_type": "bar",
  "page": 3,
  "estimated": false,
  "data_points": [
    {{"label": "Q1 2023", "value": 2847, "segment": "Investment Banking"}},
    {{"label": "Q1 2023", "value": 823, "segment": "Equities"}},
    {{"label": "Q2 2023", "value": 2650, "segment": "Investment Banking"}},
    {{"label": "Q2 2023", "value": 790, "segment": "Equities"}},
    {{"label": "Q3 2024", "value": 3102, "segment": "Investment Banking"}},
    {{"label": "Q3 2024", "value": 956, "segment": "Equities"}}
  ]
}}

FÄLT:
- estimated: false = exakta värden visas som etiketter på diagrammet
- estimated: true = värden uppskattade visuellt (stapelhöjd avläst)
- segment: Om staplad data, ange segmentnamn (valfritt)

CHECKLISTA FÖR VARJE DIAGRAM:
☐ Har du läst ALLA etiketter på diagrammet?
☐ Har du inkluderat ALLA tidsperioder (även historiska)?
☐ Är procentvärden korrekt avlästa (61.1% → 61.1)?
☐ Finns det staplad data som behöver separata segment?

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
