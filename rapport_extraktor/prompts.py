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
    "valuta": "TSEK"
  },
  "resultatrakning": [
    {"rad": "Net sales", "varde": 12345},
    {"rad": "Cost of goods sold", "varde": -5000},
    {"rad": "Gross profit", "varde": 7345, "typ": "subtotal"},
    {"rad": "Operating result", "varde": -2000, "typ": "subtotal"},
    {"rad": "Result for the period", "varde": -3000, "typ": "total"}
  ],
  "balansrakning": [
    {"rad": "Intangible assets", "varde": 50000},
    {"rad": "Total non-current assets", "varde": 75000, "typ": "subtotal"},
    {"rad": "Total assets", "varde": 100000, "typ": "total"}
  ],
  "kassaflodesanalys": [
    {"rad": "Result before tax", "varde": -3000},
    {"rad": "Cash flow from operating activities", "varde": -2000, "typ": "subtotal"},
    {"rad": "Cash flow for the period", "varde": -5000, "typ": "total"}
  ]
}

VIKTIGA INSTRUKTIONER:
1. Behåll EXAKT ordning som i PDF:en - kopiera raderna i samma ordning som de visas
2. Använd EXAKTA radnamn från rapporten (kopiera text exakt som den står)
3. Numeriska värden ska vara tal (int eller float), inte text
4. Negativa tal anges med minustecken: -5000
5. Tomma celler = null
6. Markera subtotaler med "typ": "subtotal"
7. Markera totaler/summeringar med "typ": "total"
8. Om rapporten är på engelska, behåll engelska termer
9. Ta med ALLA rader från varje rapport - missa inget

AVGRÄNSNING - Resultaträkningen SLUTAR vid:
- "Periodens resultat" / "Nettoresultat" / "Result for the period" / "Net result"
- EXKLUDERA allt efter detta, t.ex:
  - "Hänförligt till moderbolagets aktieägare"
  - "Resultat per aktie"
  - "Antal aktier"
  - "Genomsnittligt antal aktier"
  - Alla rader med "hänförligt" eller "per aktie"

Returnera ENDAST JSON, ingen annan text före eller efter.
"""
