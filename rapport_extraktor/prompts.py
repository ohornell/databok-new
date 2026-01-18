"""
Extraktions-prompt för finansiella rapporter.
Förenklad version - endast resultaträkning, balansräkning och kassaflöde.
"""

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
