# MCP Server för Rapport Extraktor

Låter Claude Desktop fråga direkt mot din finansiella databas i Supabase.

## Tillgängliga verktyg

| Verktyg | Beskrivning |
|---------|-------------|
| `list_companies` | Lista alla bolag med antal perioder |
| `get_periods` | Visa tillgängliga kvartal för ett bolag (inkl. source_file, pdf_hash) |
| `get_financials` | Hämta resultat/balans/kassaflöde |
| `get_kpis` | Hämta nyckeltal (marginaler, tillväxt etc.) |
| `get_sections` | Hämta textsektioner (VD-kommentar, etc.) |
| `search_sections` | Sök i alla textsektioner (stöder embedding-sökning) |
| `compare_periods` | Jämför två perioder |
| `get_charts` | Hämta extraherade grafer med axelinfo och datapunkter |

## Installation

### 1. Installera beroenden

```bash
cd mcp_server
pip install -r requirements.txt
```

### 2. Konfigurera miljövariabler

Kopiera din befintliga `.env` från `rapport_extraktor/` eller skapa ny:

```bash
cp ../.env .env
# eller
cp .env.example .env
# och fyll i SUPABASE_URL och SUPABASE_KEY
```

### 3. Konfigurera Claude Desktop

Öppna Claude Desktop-konfigurationen:

**macOS:**
```bash
code ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

**Windows:**
```bash
code %APPDATA%\Claude\claude_desktop_config.json
```

**Linux:**
```bash
code ~/.config/Claude/claude_desktop_config.json
```

Lägg till MCP-servern (ersätt `/path/to/mcp_server` med din faktiska sökväg):

```json
{
  "mcpServers": {
    "rapport-extraktor": {
      "command": "python",
      "args": ["/path/to/mcp_server/server.py"],
      "env": {
        "SUPABASE_URL": "https://xxx.supabase.co",
        "SUPABASE_KEY": "din-nyckel-här"
      }
    }
  }
}
```

**Alternativt**, om du har `.env`-fil:

```json
{
  "mcpServers": {
    "rapport-extraktor": {
      "command": "python",
      "args": ["/path/to/mcp_server/server.py"],
      "cwd": "/path/to/mcp_server"
    }
  }
}
```

### 4. Starta om Claude Desktop

Stäng och öppna Claude Desktop. Du bör se "rapport-extraktor" i verktygslistan.

## Användningsexempel

När MCP:n är aktiv kan du fråga Claude:

```
"Vilka bolag finns i databasen?"

"Visa Vitrolifes resultaträkning för Q3 2024"

"Jämför Fremelts omsättning Q2 2024 vs Q2 2023"

"Sök efter VD-kommentarer som nämner tillväxt"

"Vad säger VD:n i Vitrolifes senaste rapport?"

"Vilka grafer finns i Q3-rapporten för Vitrolife?"

"Visa nyckeltal för Vitrolife Q3 2025"

"Sök semantiskt efter 'lönsamhetsförbättring'" (kräver embeddings)
```

## Felsökning

### MCP syns inte i Claude Desktop

1. Kontrollera att sökvägen i `claude_desktop_config.json` är korrekt
2. Kör `python server.py` manuellt för att se eventuella fel
3. Kontrollera att Supabase-credentials är korrekta

### "Bolag hittades inte"

Servern söker både på `slug` och `name`. Prova:
- Exakt namn: "Vitrolife AB"
- Slug: "vitrolife-ab"
- Del av namn: "vitrolife"

### Inga sektioner/charts

Kontrollera att du har kört extraktion med `--multi-pass` eller `--full`.
Legacy-extraktioner (`EXTRACTION_PROMPT`) sparar endast finansiella tabeller.

## Arkitektur

```
Claude Desktop
     │
     │ MCP Protocol (stdio)
     ▼
┌─────────────┐
│ server.py   │
│             │
│ list_companies()
│ get_financials()
│ search_sections()
│ ...         │
└──────┬──────┘
       │
       │ Supabase Client
       ▼
┌─────────────┐
│  Supabase   │
│  (samma DB  │
│  som rapport│
│  _extraktor)│
└─────────────┘
```

MCP-servern är **read-only** och påverkar inte Rapport Extraktors funktionalitet.
