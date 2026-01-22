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

- Automatisk extraktion av finansiell data från PDF-rapporter
- Stöd för resultaträkning, balansräkning, kassaflödesanalys och alla tabeller
- Grafer och diagram extraheras med datapunkter
- Professionellt formaterad Excel-output (Investment Bank-stil)
- Supabase-lagring med semantisk sökning (Voyage AI embeddings)
- MCP-server för sömlös integration med Claude Desktop
- Smart caching - redan extraherade rapporter hämtas från databasen
- Multi-language stöd (sv, no, en) med cross-language jämförelser

## Pipelines

### Claude Pipeline (Haiku + Sonnet)
Multi-pass extraktion med tre pass för bästa kvalitet:

```
PDF → Pass 1 (Haiku) → Pass 2+3 (Sonnet + Haiku parallellt) → Validering → DB
```

- **Pass 1**: Haiku skapar strukturkarta över PDF
- **Pass 2**: Sonnet extraherar tabeller och grafer
- **Pass 3**: Haiku extraherar textsektioner
- **Kostnad**: ~4-6 kr per rapport

### Mistral Pipeline (OCR + Pixtral) ⭐ Snabbast
OCR-baserad extraktion med Mistral OCR 2512:

```
PDF → OCR (mistral-ocr-2512) → Pixtral (grafanalys) → DB
```

- Hanterar långa PDFs utan 8-sidorsbegränsning
- Parallell bearbetning av sidor
- **Kostnad**: ~1-3 kr per rapport

## Installation

```bash
cd rapport_extraktor
pip install -r requirements.txt
```

## Konfiguration

### Miljövariabler

Skapa `.env` från mallen:
```bash
cp .env.example .env
```

Fyll i nycklar i `.env`:
```bash
# Anthropic API (för Claude-pipeline)
ANTHROPIC_API_KEY=din-nyckel

# Mistral API (för Mistral-pipeline)
MISTRAL_API_KEY=din-nyckel

# Supabase (obligatorisk)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=din-supabase-nyckel

# Voyage AI (för semantisk sökning)
VOYAGE_API_KEY=din-voyage-nyckel

# Lagring: "local" eller "cloud" (Supabase Storage)
STORAGE_MODE=local
```

### Supabase Setup

1. Skapa ett projekt på [supabase.com](https://supabase.com)
2. Fyll i credentials i `.env` från **Project Settings > API**
3. Verifiera setup:
   ```bash
   python main.py --check-db
   ```

## Användning

### Interaktivt läge (rekommenderat)

```bash
python main.py -i
```

Guidat flöde:
1. Välj bolag från listan eller skapa nytt
2. Välj åtgärd: Skapa databok eller extrahera nytt kvartal
3. Välj pipeline: Claude eller Mistral
4. Skapa databok från extraherad data

### CLI-läge

```bash
# Extrahera med Claude-pipeline
python main.py ./rapporter/ --company "Bolagsnamn" -o databok.xlsx

# Extrahera med Mistral-pipeline (snabbare)
python main.py ./rapporter/ --company "Bolagsnamn" -o databok.xlsx --model mistral

# Generera Excel från databas (utan ny extraktion)
python main.py --company "Bolagsnamn" --from-db -o databok.xlsx

# Lista alla bolag
python main.py --list-companies
```

## Kostnader

| Pipeline | Kostnad (SEK) | Beskrivning |
|----------|---------------|-------------|
| Claude | ~4-6 kr | Haiku + Sonnet + Haiku |
| Mistral | ~1-3 kr | OCR + Pixtral |

## Projektstruktur

```
databok-new/
├── rapport_extraktor/          # Huvudmodul för PDF-extraktion
│   ├── main.py                 # CLI-verktyg
│   ├── pipeline.py             # Claude pipeline (Haiku + Sonnet)
│   ├── pipeline_mistral_v2.py  # Mistral pipeline (OCR + Pixtral)
│   ├── excel_builder.py        # Excel-generering
│   ├── supabase_client.py      # Databaskoppling + embeddings
│   ├── logger.py               # Logghantering
│   ├── validation.py           # Validering för Claude-pipeline
│   ├── prompts.py              # Claude-prompter
│   ├── checkpoint.py           # Batch-checkpoint
│   ├── extraction_log.py       # Statistik och loggfiler
│   ├── schema.sql              # Databasschema
│   └── .env.example            # Mall för miljövariabler
│
├── api/                        # FastAPI backend (för webb-deploy)
│   ├── main.py                 # API-endpoints
│   ├── mcp_remote.py           # Remote MCP-stöd
│   └── requirements.txt
│
├── mcp_server/                 # MCP-server för Claude Desktop
│   └── server.py
│
├── knowledge_scripts/          # Script för kunskapsdatabas
│   ├── populate_knowledge.py
│   ├── populate_adjustments_*.py
│   └── regenerate_all_embeddings.py
│
├── alla_rapporter/             # PDF-lagring (per bolag)
│   ├── {bolag}/
│   │   ├── skall_extractas/    # PDFs att extrahera
│   │   └── ligger_i_databasen/ # Extraherade PDFs
│   └── rename_pdf.py           # Namngivnings-script
│
├── README.md
├── TODO.md
└── Dockerfile
```

## CLI-flaggor

| Flagga | Beskrivning |
|--------|-------------|
| `-i`, `--interactive` | Interaktivt läge |
| `--company`, `-c` | Bolagsnamn |
| `--output`, `-o` | Output Excel-fil |
| `--model`, `-m` | Pipeline: `claude` (default) eller `mistral` |
| `--add` | Lägg till PDF(er) till befintlig databok |
| `--from-db` | Generera Excel från databas |
| `--period`, `-p` | Filtrera på specifika perioder |
| `--no-cache` | Ignorera cache, extrahera allt på nytt |
| `--list-companies` | Lista alla bolag i databasen |
| `--check-db` | Verifiera databassetup |

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
| `search_knowledge` | Sök i kunskapsdatabasen (RAG) |

## Databasschema

Systemet använder **PostgreSQL** via **Supabase** med **pgvector** för semantisk sökning.

```
companies (1) ─────< periods (N) ─────< financial_data (N)
                         │
                         ├─────< sections (N)
                         ├─────< report_tables (N)
                         └─────< charts (N)

knowledge (fristående kunskapsdatabas för RAG)
label_synonyms (cross-language mappning)
```

## Beroenden

- `anthropic` - Claude API-klient
- `mistralai` - Mistral API-klient
- `openpyxl` - Excel-filhantering
- `supabase` - Supabase Python-klient
- `python-dotenv` - Miljövariabler
- `PyMuPDF` - PDF-sidextraktion
- `mcp` - Model Context Protocol
