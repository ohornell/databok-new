# Rapport Extraktor

Extraherar finansiell data från PDF-kvartalsrapporter och skapar professionella Excel-databöcker med Investment Bank-formatering.

## Funktioner

- Automatisk extraktion av finansiell data från PDF-rapporter via Claude API
- Stöd för resultaträkning, balansräkning och kassaflödesanalys
- Professionellt formaterad Excel-output (Goldman Sachs-inspirerad stil)
- Intelligent caching för snabbare omkörningar
- Parallell bearbetning av flera PDF-filer
- Smart radnormalisering för att matcha liknande radnamn mellan kvartal

## Installation

```bash
cd rapport_extraktor
pip install -r requirements.txt
```

## Konfiguration

Exportera din Anthropic API-nyckel:

```bash
export ANTHROPIC_API_KEY='din-api-nyckel'
```

## Användning

### Skapa ny databok från PDF-rapporter

```bash
python main.py ./rapporter/ -o databok.xlsx
```

### Uppdatera befintlig databok med nya rapporter

```bash
python main.py --update databok.xlsx --add ny_rapport.pdf
```

### Ignorera cache och extrahera allt på nytt

```bash
python main.py ./rapporter/ -o databok.xlsx --no-cache
```

### Rensa all cachad data

```bash
python main.py --clear-cache
```

## Projektstruktur

```
rapport_extraktor/
├── main.py           # CLI-verktyg
├── extractor.py      # Async PDF-extraktion via Claude API
├── excel_builder.py  # Excel-generering med formatering
├── prompts.py        # Extraktions-prompt för Claude
├── requirements.txt  # Python-beroenden
└── .cache/           # Cachade extraktioner
```

## Beroenden

- `anthropic` - Claude API-klient
- `openpyxl` - Excel-filhantering
- `pandas` - Databearbetning
