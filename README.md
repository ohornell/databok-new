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
- Stöd för resultaträkning, balansräkning, kassaflödesanalys och alla tabeller
- Professionellt formaterad Excel-output (Investment Bank-stil)
- Supabase-lagring för snabb sökning och frontend-koppling
- Parallell bearbetning av flera PDF-filer (upp till 5 samtidiga)
- Smart caching - redan extraherade rapporter hämtas från databasen
- Token-tracking i realtid med kostnadssammanfattning efter körning
- Smart AI-driven radnormalisering för att matcha liknande radnamn mellan kvartal

### Multi-pass Pipeline (`--multi-pass`) ⭐ Rekommenderad
Optimerad extraktion med tre pass för bästa resultat:

```
Pass 1 (Haiku)  → Strukturidentifiering (~1-2s)
Pass 2 (Sonnet) → Tabellextraktion med hög precision (~3-5s)  ┐
Pass 3 (Haiku)  → Textextraktion (~1-2s)                      ┘ Körs parallellt!
```

- Pass 2 & 3 körs parallellt för snabbare extraktion
- Haiku för enklare uppgifter (billigare), Sonnet för tabeller (högre precision)
- Detaljerad timing och kostnad per pass visas efter körning

### Interaktivt läge (`-i`)
- Guidat flöde för att välja bolag och perioder
- Skapa databöcker för enskilda kvartal eller alla perioder
- Extrahera nya rapporter och spara direkt till databasen

## Installation

```bash
cd rapport_extraktor
pip install -r requirements.txt
```

Om `pip` inte fungerar, prova:
```bash
py -m pip install -r requirements.txt
```

## Konfiguration

### Anthropic API

Skapa `.env` från mallen:
```bash
cp .env.example .env
```

Lägg in din API-nyckel i `.env`:
```
ANTHROPIC_API_KEY=din-nyckel-här
```

### Supabase Setup

1. Skapa ett projekt på [supabase.com](https://supabase.com)
2. Fyll i credentials i `.env` från **Project Settings > API**:
   - `SUPABASE_URL` - Project URL
   - `SUPABASE_KEY` - anon/public key

3. Verifiera setup:
   ```bash
   python main.py --check-db
   ```
   Om tabeller saknas visas en länk till SQL Editor där du klistrar in `schema.sql`

## Användning

### Interaktivt läge (rekommenderat)

```bash
python main.py -i
```

Guidat flöde:
1. Välj bolag från listan eller skapa nytt
2. Välj åtgärd: Skapa databok eller extrahera nytt kvartal
3. Välj extraktionstyp (Standard/Full/Multi-pass)
4. Skapa databok från extraherad data

### Multi-pass extraktion (rekommenderat)

```bash
python main.py ./rapporter/ --company "Bolagsnamn" -o databok.xlsx --multi-pass
```

Output med detaljerad timing:
```
📄 Hittade 1 PDF-fil(er) i ./rapporter/
🏢 Bolag: Bolagsnamn
🔄 Multi-pass pipeline aktiverad (Haiku → Sonnet → Haiku)

[X] q3_2025.pdf    185,000 tok | 4.65 kr | 5.4s
    Totalt: 185,000 tokens | 4.65 kr | 5.4s

══════════════════════════════════════════════════
✅ Lyckades:  1

📊 Q3 2025 - Pipeline detaljer:
   Pass     Modell   Tid      Input      Output     Kostnad
   ------------------------------------------------------
   Pass 1   haiku     1.2s      85,000      3,500    0.2205 kr
   Pass 2   sonnet    3.5s      92,000      8,200    4.1790 kr
   Pass 3   haiku     1.8s      88,000      4,100    0.2464 kr
   ------------------------------------------------------
   Totalt             5.2s                           4.65 kr

📊 Databok skapad: databok.xlsx
   Innehåller 1 period(er)
```

### Standard extraktion

```bash
python main.py ./rapporter/ --company "Bolagsnamn" -o databok.xlsx
```

### Full extraktion (text, alla tabeller, grafer)

```bash
python main.py ./rapporter/ --company "Bolagsnamn" -o databok.xlsx --full
```

### Lägg till nya rapporter

```bash
python main.py --company "Bolagsnamn" --add ny_rapport.pdf -o databok.xlsx --multi-pass
```

### Generera Excel från databas (utan ny extraktion)

```bash
python main.py --company "Bolagsnamn" --from-db -o databok.xlsx
```

### Filtrera på specifika perioder

```bash
python main.py --company "Bolagsnamn" --from-db -o databok.xlsx --period "Q1 2025" "Q2 2025"
```

### Lista alla bolag

```bash
python main.py --list-companies
```

## Kostnader

### Token-priser (USD per 1M tokens)

| Modell | Input | Output |
|--------|-------|--------|
| Haiku  | $0.80 | $4.00  |
| Sonnet | $3.00 | $15.00 |

### Typiska kostnader per rapport

| Läge | Kostnad (SEK) | Beskrivning |
|------|---------------|-------------|
| Multi-pass | ~4-6 kr | Haiku + Sonnet + Haiku |
| Standard | ~1-2 kr | Endast Sonnet |
| Full | ~4-5 kr | Sonnet med all text |

## Pipeline

### Multi-pass flöde

```
┌─────────────────────────────────────────────────────────────┐
│                    MULTI-PASS PIPELINE                       │
└─────────────────────────────────────────────────────────────┘

  PDF
   │
   ▼
┌──────────────────────────────────────┐
│  PASS 1: Strukturidentifiering       │
│  Modell: Haiku (billig, snabb)       │
│                                      │
│  • Identifiera alla tabeller         │
│  • Identifiera textsektioner         │
│  • Identifiera grafer                │
│  • Returnera "strukturkarta"         │
└──────────────┬───────────────────────┘
               │
       ┌───────┴───────┐
       │               │
       ▼               ▼
┌─────────────┐  ┌─────────────┐
│  PASS 2     │  │  PASS 3     │
│  Tabeller   │  │  Text       │
│  (Sonnet)   │  │  (Haiku)    │
│             │  │             │
│  • Extrahera│  │  • Extrahera│
│    tabeller │  │    sektioner│
│  • Konvert. │  │  • Citat    │
│    tal      │  │  • Kontakt  │
│  • Grafer   │  │  • Kalender │
└──────┬──────┘  └──────┬──────┘
       │                │
       │    PARALLELLT! │
       └───────┬────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  MERGE & SPARA                       │
│  • Kombinera resultat                │
│  • Spara till Supabase               │
│  • Generera Excel                    │
└──────────────────────────────────────┘
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
           │ (0 kr)        │           │ (~4-6 kr)     │
           └───────────────┘           └───────────────┘
```

## Databasschema

```sql
companies (1) ─────< periods (N) ─────< financial_data (N)
    │                   │                    │
    ├─ id (UUID)        ├─ id (UUID)         ├─ id (UUID)
    ├─ name             ├─ company_id (FK)   ├─ period_id (FK)
    └─ slug             ├─ quarter           ├─ statement_type
                        ├─ year              ├─ row_name
                        ├─ pdf_hash          ├─ value
                        └─ valuta            └─ row_type

                    periods (1) ─────< sections (N)
                                       ├─ title
                                       ├─ section_type
                                       └─ content

                    periods (1) ─────< report_tables (N)
                                       ├─ title
                                       ├─ table_type
                                       ├─ columns (JSONB)
                                       └─ rows (JSONB)

                    periods (1) ─────< charts (N)
                                       ├─ title
                                       ├─ chart_type
                                       └─ data_points (JSONB)
```

## Projektstruktur

```
rapport_extraktor/
├── main.py              # CLI-verktyg
├── pipeline.py          # Multi-pass extraktion (Haiku + Sonnet + Haiku)
├── extractor.py         # Legacy single-pass extraktion
├── excel_builder.py     # Excel-generering med IB-formatering
├── supabase_client.py   # Supabase databashantering
├── prompts.py           # Extraktions-prompter för Claude
├── schema.sql           # Databasschema för Supabase
├── requirements.txt     # Python-beroenden
└── .env.example         # Mall för miljövariabler
```

## CLI-flaggor

| Flagga | Beskrivning |
|--------|-------------|
| `-i`, `--interactive` | Interaktivt läge |
| `--multi-pass` | Multi-pass pipeline (Haiku + Sonnet + Haiku) |
| `--full` | Full extraktion (all text och alla tabeller) |
| `--company`, `-c` | Bolagsnamn |
| `--output`, `-o` | Output Excel-fil |
| `--add` | Lägg till PDF(er) till befintlig databok |
| `--from-db` | Generera Excel från databas |
| `--period`, `-p` | Filtrera på specifika perioder |
| `--no-cache` | Ignorera cache, extrahera allt på nytt |
| `--list-companies` | Lista alla bolag i databasen |
| `--check-db` | Verifiera databassetup |
| `--model` | Välj modell: sonnet (default) eller haiku |
| `--streaming` | Använd streaming API |

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
  .eq('periods.companies.slug', 'bolagsnamn')
```

## Beroenden

- `anthropic` - Claude API-klient
- `openpyxl` - Excel-filhantering
- `supabase` - Supabase Python-klient
- `python-dotenv` - Miljövariabler
