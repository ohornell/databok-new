# Rapport Extraktor

Extraherar finansiell data från PDF-kvartalsrapporter och skapar professionella Excel-databöcker med Investment Bank-formatering. Data lagras i Supabase för snabb sökning och enkel koppling till frontend.

## Arkitektur

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Python CLI     │────▶│  Supabase    │◀────│  Next.js    │
│  (extraktion)   │     │  (PostgreSQL)│     │  (frontend) │
└─────────────────┘     └──────────────┘     └─────────────┘
```

## Funktioner

### Grundläggande
- Automatisk extraktion av finansiell data från PDF-rapporter via Claude API
- Stöd för resultaträkning, balansräkning och kassaflödesanalys
- Professionellt formaterad Excel-output (Goldman Sachs-inspirerad stil)
- Supabase-lagring för snabb sökning och frontend-koppling
- Parallell bearbetning av flera PDF-filer (upp till 5 samtidiga)
- Smart caching - redan extraherade rapporter hämtas från databasen
- Token-tracking i realtid med kostnadssammanfattning efter körning
- Smart AI-driven radnormalisering för att matcha liknande radnamn mellan kvartal

### Full extraktion (`--full`)
- Extraherar ALL text från rapporten (VD-ord, marknadsöversikt, verksamhetsbeskrivning etc.)
- Extraherar ALLA tabeller (koncern, moderbolag, nyckeltal, segment)
- Extraherar grafer/diagram med datapunkter (stapel, linje, cirkel, yta)
- Separata Excel-flikar för textsektioner och grafer

## Installation

```bash
cd rapport_extraktor
pip install -r requirements.txt
```

## Konfiguration

### Anthropic API

Exportera din API-nyckel:
```bash
export ANTHROPIC_API_KEY='din-nyckel'
```

### Supabase Setup

1. Skapa ett projekt på [supabase.com](https://supabase.com)
2. Kopiera `.env.example` till `.env`:
   ```bash
   cp .env.example .env
   ```
3. Fyll i credentials från **Project Settings > API**:
   - `SUPABASE_URL` - Project URL
   - `SUPABASE_KEY` - anon/public key

4. Verifiera setup (ger instruktioner om tabeller saknas):
   ```bash
   python main.py --check-db
   ```
   Om tabeller saknas visas en länk till SQL Editor där du klistrar in `schema.sql`

## Användning

### Extrahera rapporter för ett bolag

```bash
python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx
```

### Full extraktion (text, alla tabeller, grafer)

```bash
python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx --full
```

Output:
```
📄 Hittade 4 PDF-fil(er) i ./rapporter/
🏢 Bolag: Freemelt

[X] freemelt-q1-2025.pdf    31,200 tok | 1.25 kr | 12.3s
[X] freemelt-q2-2025.pdf    32,450 tok | 1.31 kr | 11.8s
[X] freemelt-q3-2025.pdf    30,890 tok | 1.22 kr | 13.1s
[~] freemelt-q4-2025.pdf    8.5s
    Totalt: 94,540 tokens | 3.78 kr | 45.7s

══════════════════════════════════════════════════
✅ Lyckades:  4

💰 Kostnad:
   Input:  122,150 tokens
   Output: 3,280 tokens
   Totalt: 5.04 kr

📊 Databok skapad: databok.xlsx
   Innehåller 4 period(er)

💰 Normaliseringskostnad: 0.15 kr
```

### Lägg till nya rapporter

Lägg in Q4-rapporten i samma mapp och kör igen. Cachade rapporter (Q1-Q3) hoppas över automatiskt:

```bash
python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx
```

Output:
```
[C] freemelt-q1-2025.pdf    (cachad)
[C] freemelt-q2-2025.pdf    (cachad)
[C] freemelt-q3-2025.pdf    (cachad)
[X] freemelt-q4-2025.pdf    32,020 tok | 1.26 kr | 11.5s
    Totalt: 32,020 tokens | 1.26 kr | 12.1s
```

Endast Q4 extraheras (kostar tokens), Q1-Q3 laddas från databasen (gratis).

### Generera Excel från databas (utan ny extraktion)

```bash
python main.py --company "Freemelt" --from-db -o databok.xlsx
```

### Filtrera på specifika perioder

```bash
python main.py --company "Freemelt" --from-db -o databok.xlsx --period "Q1 2025" "Q2 2025"
```

### Lista alla bolag i databasen

```bash
python main.py --list-companies
```

### Ignorera cache och extrahera allt på nytt

```bash
python main.py ./rapporter/ --company "Freemelt" -o databok.xlsx --no-cache
```

## Flöde

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EXTRAKTIONSFLÖDE                               │
└─────────────────────────────────────────────────────────────────────────────┘

  PDF-fil                    Claude API                      Supabase
  ───────                    ──────────                      ────────

  ┌─────────┐
  │ Q1.pdf  │──┐
  └─────────┘  │
  ┌─────────┐  │   ┌──────────────────────────┐
  │ Q2.pdf  │──┼──▶│  1. EXTRAKTION           │
  └─────────┘  │   │  (async, 5 parallella)   │
  ┌─────────┐  │   │                          │
  │ Q3.pdf  │──┘   │  • Skicka PDF som base64 │
  └─────────┘      │  • Claude läser & tolkar │
               │  • Returnerar JSON        │
               └────────────┬─────────────┘
                            │
                            ▼
               ┌──────────────────────────┐
               │  JSON per kvartal:       │
               │  {                       │
               │    metadata: {...},      │
               │    resultatrakning: [...],│
               │    balansrakning: [...], │
               │    kassaflodesanalys: [..]│
               │  }                       │
               └────────────┬─────────────┘
                            │
                            ▼
               ┌──────────────────────────┐     ┌──────────────────┐
               │  2. SPARA TILL SUPABASE  │────▶│  companies       │
               │                          │     │  ├─ id           │
               │  • get_or_create_company │     │  ├─ name         │
               │  • save_period           │     │  └─ slug         │
               │  • Hash PDF för cache    │     ├──────────────────┤
               │                          │     │  periods         │
               └────────────┬─────────────┘     │  ├─ company_id   │
                            │                   │  ├─ quarter/year │
                            │                   │  └─ pdf_hash     │
                            │                   ├──────────────────┤
                            │                   │  financial_data  │
                            ▼                   │  ├─ period_id    │
               ┌──────────────────────────┐     │  ├─ row_name     │
               │  3. EXCEL-GENERERING     │     │  └─ value        │
               │                          │     └──────────────────┘
               │  a) AI-normalisering:    │
               │     • Samla alla radnamn │
               │     • Claude mappar till │
               │       svenska termer     │
               │                          │
               │  b) Bygg Excel:          │
               │     • Sortera perioder   │
               │     • Skapa flikar       │
               │     • Applicera styling  │
               └────────────┬─────────────┘
                            │
                            ▼
               ┌──────────────────────────┐
               │  📊 databok.xlsx         │
               │                          │
               │  Flikar:                 │
               │  • Resultaträkning       │
               │  • Balansräkning         │
               │  • Kassaflöde            │
               │  (med --full:)           │
               │  • Grafer                │
               │  • VD-ord                │
               │  • Marknadsöversikt      │
               │  • ...fler textsektioner │
               └──────────────────────────┘
```

### Cache-logik

Vid upprepade körningar kontrolleras om PDF:en redan är extraherad:

```
┌─────────┐    PDF hash    ┌──────────────┐
│  PDF    │───────────────▶│  Supabase    │
└─────────┘                │  periods     │
                           │  (pdf_hash)  │
                           └──────┬───────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
               Hash matchar?               Hash matchar ej
                    │                           │
                    ▼                           ▼
           ┌───────────────┐           ┌───────────────┐
           │ Ladda från DB │           │ Ny extraktion │
           │ (0 kr)        │           │ (~1-2 kr)     │
           └───────────────┘           └───────────────┘
```

### Databasschema

```sql
companies (1) ─────< periods (N) ─────< financial_data (N)
    │                   │                    │
    ├─ id (UUID)        ├─ id (UUID)         ├─ id (UUID)
    ├─ name             ├─ company_id (FK)   ├─ period_id (FK)
    └─ slug             ├─ quarter           ├─ statement_type
                        ├─ year              ├─ row_name
                        ├─ pdf_hash          ├─ value
                        └─ valuta            └─ row_type

                    periods (1) ─────< sections (N)        -- Textsektioner
                                       ├─ title
                                       ├─ page_number
                                       ├─ section_type
                                       └─ content

                    periods (1) ─────< report_tables (N)   -- Alla tabeller (JSONB)
                                       ├─ title
                                       ├─ table_type
                                       ├─ columns (JSONB)
                                       └─ rows (JSONB)

                    periods (1) ─────< charts (N)          -- Grafer/diagram
                                       ├─ title
                                       ├─ chart_type
                                       ├─ estimated
                                       └─ data_points (JSONB)
```

## Kostnader

Verktyget använder Claude Sonnet 4 för:
1. **PDF-extraktion** - extraherar finansiell data från varje PDF (~1-2 kr/rapport, ~4-5 kr med `--full`)
2. **Radnormalisering** - matchar radnamn mellan kvartal för konsekvent Excel (~0.10-0.20 kr/körning)

Kostnaden visas i realtid under körning och summeras efteråt.

## Projektstruktur

```
rapport_extraktor/
├── main.py              # CLI-verktyg
├── extractor.py         # Async PDF-extraktion via Claude API
├── excel_builder.py     # Excel-generering med formatering + AI-normalisering
├── supabase_client.py   # Supabase databashantering
├── prompts.py           # Extraktions-prompter för Claude
├── schema.sql           # Databasschema för Supabase
├── requirements.txt     # Python-beroenden
└── .env.example         # Mall för miljövariabler
```

## Next.js Integration

```typescript
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)

// Hämta alla bolag
const { data: companies } = await supabase
  .from('companies')
  .select('*')

// Hämta finansdata för ett bolag
const { data } = await supabase
  .from('financial_data')
  .select('*, periods!inner(quarter, year, companies!inner(slug))')
  .eq('periods.companies.slug', 'freemelt')
```

## Beroenden

- `anthropic` - Claude API-klient
- `openpyxl` - Excel-filhantering
- `supabase` - Supabase Python-klient
- `python-dotenv` - Miljövariabler
