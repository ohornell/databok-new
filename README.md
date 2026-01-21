# Rapport Extraktor

Extraherar finansiell data från PDF-kvartalsrapporter och skapar professionella Excel-databöcker med Investment Bank-formatering. Data lagras i Supabase för snabb sökning och enkel koppling till frontend.

## Arkitektur

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Python CLI     │────▶│  Supabase    │◀────│  MCP Server │
│  (extraktion)   │     │  (PostgreSQL)│     │  (Claude)   │
└─────────────────┘     └──────────────┘     └─────────────┘
```

## Funktioner

### Grundläggande
- Automatisk extraktion av finansiell data från PDF-rapporter via Claude API
- Stöd för resultaträkning, balansräkning, kassaflödesanalys och alla tabeller
- Grafer och diagram extraheras med datapunkter
- Professionellt formaterad Excel-output (Investment Bank-stil)
- Supabase-lagring med semantisk sökning (Voyage AI embeddings)
- MCP-server för sömlös integration med Claude Desktop
- Parallell bearbetning av flera PDF-filer (upp till 10 samtidiga)
- Smart caching - redan extraherade rapporter hämtas från databasen
- Automatisk validering och retry vid extraktionsfel
- Multi-language stöd (sv, no, en) med cross-language jämförelser

### Multi-pass Pipeline ⭐ Standard
Optimerad extraktion med tre pass för bästa resultat:

```
                        PDF
                         │
                         ▼
              ┌─────────────────────┐
              │  Pass 1 (Haiku)     │
              │  Strukturkarta      │
              │  ~0.20 kr           │
              └──────────┬──────────┘
                         │
           ┌─────────────┴─────────────┐
           │                           │
           ▼                           ▼
┌─────────────────────┐     ┌─────────────────────┐
│  Pass 2 (Sonnet)    │     │  Pass 3 (Haiku)     │
│  Tabeller + Grafer  │     │  Textsektioner      │
│  ~4.00 kr           │     │  ~0.25 kr           │
└──────────┬──────────┘     └──────────┬──────────┘
           │      PARALLELLT!          │
           └─────────────┬─────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  Validering         │
              │  Saknas tabeller?   │
              │  Fel i data?        │
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
         OK ──┘                     └── Fel
              │                           │
              │                           ▼
              │              ┌─────────────────────┐
              │              │  Sonnet Retry       │
              │              │  Endast relevanta   │
              │              │  sidor (pypdf)      │
              │              │  ~0.20-0.30 kr      │
              │              └──────────┬──────────┘
              │                         │
              └─────────────┬───────────┘
                            │
                            ▼
              ┌─────────────────────┐
              │  Spara till DB      │
              │  + Embeddings       │
              │  (Voyage AI)        │
              └─────────────────────┘
```

- **Pass 1**: Haiku skapar strukturkarta över PDF (tabeller, sektioner, grafer)
- **Pass 2+3**: Körs parallellt - Sonnet extraherar tabeller, Haiku extraherar text
- **Validering**: Kontrollerar att alla tabeller extraherades korrekt
- **Retry**: Vid fel extraheras endast relevanta sidor (±1 sida) med pypdf, sedan körs Sonnet för bättre kvalitet
- **Embeddings**: Voyage AI genererar vektorer för semantisk sökning

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

### Miljövariabler

Skapa `.env` från mallen:
```bash
cp .env.example .env
```

Fyll i alla nycklar i `.env`:
```
# Anthropic API (obligatorisk)
ANTHROPIC_API_KEY=din-nyckel-här

# Supabase (obligatorisk)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=din-supabase-nyckel

# Voyage AI (för semantisk sökning, valfri men rekommenderad)
VOYAGE_API_KEY=din-voyage-nyckel
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

Systemet använder **PostgreSQL** via **Supabase** med **pgvector** för semantisk sökning.

### Teknologi-stack

| Komponent | Teknologi | Användning |
|-----------|-----------|------------|
| Databas | PostgreSQL (Supabase) | Relationell lagring med JSONB-stöd |
| Vektor-sökning | pgvector | Similarity search på embeddings |
| Embeddings | Voyage AI (voyage-4) | 1024-dimensionella vektorer för semantisk sökning |
| API | Supabase REST + RPC | CRUD-operationer och stored procedures |

### Voyage AI Embeddings

Systemet använder **Voyage AI** (modell `voyage-4`) för att generera semantiska embeddings:

- **Textsektioner** (`sections.embedding`) - Möjliggör semantisk sökning i VD-ord, marknadsöversikter etc.
- **Kunskapsposter** (`knowledge.embedding`) - RAG-sökning för finansiella definitioner och analysmetoder

**Hur det fungerar:**
1. Vid insättning av text genereras en 1024-dimensionell vektor via Voyage API
2. Vektorn lagras i PostgreSQL med pgvector-extension
3. Vid sökning genereras en vektor för sökfrågan
4. pgvector hittar de mest semantiskt liknande posterna via cosine similarity

**Fördelar med semantisk sökning:**
- "EBITDA-justering" matchar "justerat rörelseresultat" även om orden är olika
- Cross-language: "revenue" matchar "nettoomsättning"
- Konceptuell förståelse: "röda flaggor i kassaflödet" hittar relevanta varningssignaler

### Relationsdiagram

```
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
                                       ├─ content
                                       └─ embedding (vector)

                    periods (1) ─────< report_tables (N)
                                       ├─ title
                                       ├─ table_type
                                       ├─ columns (JSONB)
                                       └─ rows (JSONB)

                    periods (1) ─────< charts (N)
                                       ├─ title
                                       ├─ chart_type
                                       └─ data_points (JSONB)

knowledge (fristående kunskapsdatabas)
    ├─ id (UUID)
    ├─ domain           # nyckeltal, redovisning, bransch, värdering, kvalitativ
    ├─ category         # IFRS, K3, justeringar, röda_flaggor, etc.
    ├─ title
    ├─ content
    ├─ tags[]
    ├─ related_metrics[]
    ├─ source           # FAR, CFA, ESMA, intern
    └─ embedding (vector)

label_synonyms (för cross-language jämförelser)
    ├─ synonym          # "net sales", "nettointäkter"
    └─ canonical        # "revenue"
```

### Tabellbeskrivningar

| Tabell | Beskrivning | Typ |
|--------|-------------|-----|
| `companies` | Bolag med namn och URL-slug | Master |
| `periods` | Kvartalsrapporter med metadata (år, kvartal, valuta, språk) | Master |
| `financial_data` | Finansiella rader (resultat, balans, kassaflöde) | Transaktion |
| `sections` | Textsektioner (VD-ord, marknadsöversikt) med embeddings | Transaktion |
| `report_tables` | Flexibla tabeller (KPIs, segment, etc.) som JSONB | Transaktion |
| `charts` | Extraherade grafer med datapunkter | Transaktion |
| `knowledge` | RAG-kunskapsbas för finansiell analys | Fristående |
| `label_synonyms` | Mappning för cross-language jämförelser | Lookup |

### Datatyper

| Kolumn | PostgreSQL-typ | Beskrivning |
|--------|---------------|-------------|
| `id` | `UUID` | Primärnyckel (auto-genererad) |
| `embedding` | `vector(1024)` | Voyage-4 embedding för semantisk sökning |
| `columns`, `rows`, `data_points` | `JSONB` | Flexibel JSON-lagring |
| `tags`, `related_metrics` | `TEXT[]` | PostgreSQL-arrayer |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | Tidsstämplar med tidszon |

### Index och RPC-funktioner

**Index:**
- `idx_sections_embedding` - IVFFlat vektor-index för snabb similarity search
- `idx_knowledge_embedding` - IVFFlat vektor-index för knowledge-sökning
- `idx_sections_content_fts` - GIN fulltext-index (simple config för multi-language)

**RPC-funktioner:**
- `search_knowledge(query_embedding, match_count, domain_filter, category_filter)` - Semantisk sökning i kunskapsdatabasen
- `normalize_label_en(label)` - Normalisera finansiella termer via synonym-tabell

## Projektstruktur

```
rapport_extraktor/
├── main.py              # CLI-verktyg
├── pipeline.py          # Multi-pass extraktion (Haiku + Sonnet + Haiku)
├── validation.py        # Validering av extraherad data
├── excel_builder.py     # Excel-generering med IB-formatering
├── supabase_client.py   # Supabase databashantering + embeddings
├── prompts.py           # Extraktions-prompter för Claude
├── schema.sql           # Databasschema för Supabase
├── requirements.txt     # Python-beroenden
└── .env.example         # Mall för miljövariabler

mcp_server/
├── server.py            # MCP-server för Claude Desktop
└── generate_embeddings.py  # Script för att generera embeddings

scripts/
├── populate_knowledge.py              # IFRS/K3 redovisningskunskap
├── populate_adjustments_swedish.py    # Svenska justeringsposter
├── populate_valuation_knowledge.py    # DCF, multiplar, sektorvärdering
├── regenerate_all_embeddings.py       # Regenerera alla embeddings (voyage-4)
└── generate_knowledge_embeddings.py   # Batch-generera embeddings
```

## CLI-flaggor

| Flagga | Beskrivning |
|--------|-------------|
| `-i`, `--interactive` | Interaktivt läge |
| `--company`, `-c` | Bolagsnamn |
| `--output`, `-o` | Output Excel-fil |
| `--add` | Lägg till PDF(er) till befintlig databok |
| `--from-db` | Generera Excel från databas |
| `--period`, `-p` | Filtrera på specifika perioder |
| `--no-cache` | Ignorera cache, extrahera allt på nytt |
| `--list-companies` | Lista alla bolag i databasen |
| `--check-db` | Verifiera databassetup |

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

## MCP Server

MCP-servern exponerar finansiell data för Claude Desktop.

### Installation

Lägg till i `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "rapport-extraktor": {
      "command": "python",
      "args": ["/path/to/mcp_server/server.py"],
      "env": {
        "SUPABASE_URL": "https://xxx.supabase.co",
        "SUPABASE_KEY": "din-nyckel",
        "VOYAGE_API_KEY": "din-voyage-nyckel"
      }
    }
  }
}
```

### Tillgängliga verktyg

#### Finansiell data
| Verktyg | Beskrivning |
|---------|-------------|
| `list_companies` | Lista alla bolag |
| `get_periods` | Visa perioder för ett bolag |
| `get_financials` | Hämta finansiell data |
| `get_kpis` | Hämta nyckeltal |
| `get_sections` | Hämta textsektioner |
| `search_sections` | Hybrid sökning (text + semantisk) |
| `compare_periods` | Jämför två perioder |
| `compare_companies` | Jämför två bolag (cross-language) |
| `get_charts` | Hämta grafer med datapunkter |

#### Kunskapsdatabas (RAG)
| Verktyg | Beskrivning |
|---------|-------------|
| `search_knowledge` | Sök efter definitioner, formler och analysmetoder (semantisk sökning) |
| `add_knowledge` | Lägg till ny kunskap |
| `list_knowledge` | Lista all kunskap per domän/kategori |
| `update_knowledge` | Uppdatera kunskapspost |
| `delete_knowledge` | Ta bort kunskapspost |

## Kunskapsdatabas

Systemet innehåller en RAG-baserad kunskapsdatabas för finansiell analys. Kunskapen är organiserad i domäner och kategorier med semantisk sökning via Voyage AI embeddings.

### Domäner

| Domän | Beskrivning |
|-------|-------------|
| `nyckeltal` | Formler, definitioner och tolkningar av finansiella nyckeltal |
| `redovisning` | IFRS, K3, redovisningsprinciper och standarder |
| `bransch` | Branschspecifik kunskap (fastighet, bank, industri, etc.) |
| `värdering` | Värderingsmetoder och multiplar |
| `kvalitativ` | Röda flaggor, analysmetoder och best practices |

### Nuvarande innehåll (83 poster)

**Redovisning/IFRS (22 poster)**
- IFRS 15 intäktsredovisning (5-stegsmodellen, principal vs agent)
- IFRS 16 leasing (mekanik, analytikerjusteringar)
- PPA (förvärvsanalys, immateriella tillgångar)
- IAS 21 valutaomräkning, IAS 37 avsättningar
- IFRS 2 aktierelaterade ersättningar, IFRS 9 säkring

**Redovisning/K3 (10 poster)**
- K3 kapitel 23 intäktsredovisning
- K3 kapitel 20 leasing
- K3 goodwill (10 års avskrivning)
- K3 säkringsredovisning, aktierelaterade ersättningar

**Nyckeltal/Justeringar (9 poster)**
- Justerat EBITDA definition och syfte
- EBITDA-brygga analys
- Jämförelsestörande poster (svenska definitioner)
- ESMA APM-riktlinjer

**Nyckeltal/Serieförvärvare (3 poster)**
- EBITA definition och beräkning
- Förvärvsrelaterade kostnader

**Bransch/Justeringar (4 poster)**
- Svenska fastighetsbolag (förvaltningsresultat)
- Svenska banker (K/I-tal)
- Svenska industribolag (cykliska justeringar)
- Svenska investmentbolag (substansvärde/NAV)

**Kvalitativ/Röda flaggor (3 poster)**
- Återkommande engångsposter
- Ökande justeringsbelopp
- Kassaflöde som kvalitetstest

**Värdering/DCF (8 poster)**
- DCF-metodens grundprinciper och användning
- WACC-beräkning med svenska marknadsparametrar
- CAPM för svenska småbolag (storlekspremie, likviditetspremie)
- FCFF vs FCFE med praktiska exempel
- Terminalvärde och Gordon Growth Model
- Vad som driver terminalvärde
- Scenarioanalys i DCF
- DCF praktiska checklista

**Värdering/Multiplar (7 poster)**
- EV/EBITDA vs EV/EBITA val
- EV/Sales för tillväxtbolag
- P/E-talets begränsningar
- Trailing vs Forward multiplar (LTM vs NTM)
- Peer-gruppskonstruktion
- Multiplar vid förvärv (kontrollpremie)
- Implicita förväntningar i multiplar

**Värdering/Sektorspecifik (6 poster)**
- SaaS-värdering (ARR-multiplar, Rule of 40)
- Bankspecifik värdering (P/B, P/TBV, ROE)
- Fastighetsvärdering (NAV, P/NAV)
- Investmentbolag (SOTP, substansrabatt)
- Cyklisk industri (normaliserad EBITDA)
- Industrimultiplar referenstabell

**Värdering/Triangulering (2 poster)**
- Värderingstriangulering (DCF + peer + transaktioner)
- Fotbollsplanediagram

### Exempel på användning i Claude Desktop

```
Användare: Hur beräknas EBITA för serieförvärvare?
Claude: [söker i kunskapsdatabasen]
        EBITA = EBIT + Avskrivningar på förvärvade immateriella tillgångar
        Används av Lifco, Indutrade, Addtech för att visa underliggande lönsamhet...

Användare: Vilka justeringar är vanliga för svenska fastighetsbolag?
Claude: [söker i kunskapsdatabasen]
        Svenska fastighetsbolag använder förvaltningsresultat som primärt resultatmått.
        Typiska justeringar: värdeförändringar fastigheter, derivat, engångsposter...
```

## Beroenden

- `anthropic` - Claude API-klient
- `openpyxl` - Excel-filhantering
- `supabase` - Supabase Python-klient
- `python-dotenv` - Miljövariabler
- `requests` - HTTP-klient för Voyage AI
- `pypdf` - PDF-sidextraktion för optimerad retry
- `mcp` - Model Context Protocol
