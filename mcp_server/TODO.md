# TODO - MCP Server

## Att göra

-

## Idéer

- Knowledge-tabell för finansiell analyskunskap (AI-analytiker)
  - Skapa `knowledge` tabell med category, title, content, tags, embedding
  - Lägg till `search_knowledge` verktyg i MCP
  - Fyll på med: nyckeltalsformler, redovisningsregler (IFRS/K3), branschmetrics, värderingsmetodik, kvalitativ analys
  - Claude söker automatiskt rätt kunskap vid analys
  - Skalbart: kan ladda 1000+ kunskapsstycken utan prestandapåverkan

- Spara språk (sv/en) från extraktion till sections-tabellen
  - Lägg till `language` kolumn i sections
  - Uppdatera save_sections() att spara metadata.sprak
  - Möjliggör språkfiltrering vid sökning
  - Bra för analys och viktning av sökresultat

- Utvärdera specialiserad vektordatabas (Pinecone, Weaviate, Qdrant) för bättre hybrid-sökning
  - Fördelar: Optimerad vektor-sökning, bättre hybrid-algoritmer (RRF), inbyggd re-ranking
  - Nackdel: Två databaser att synka (Supabase för strukturerad data + vektordatabas för sökning)

- Chunking av sektioner för bättre sökning
  - Nu: 1 sektion = 1 embedding (kan vara 5000+ tecken)
  - Bättre: Dela upp i chunks à 500-1000 tecken med överlapp
  - Fördelar: Bättre semantisk matchning, kan peka på exakt stycke
  - Nackdelar: Fler embeddings, mer komplext, kostar mer i Voyage API

## Kända problem

- Claude Desktop visar inte källor från JSON-responsen
- Claude Desktop visar inte ASCII-visualiseringar

## Klart

- [x] Optimerat DB-queries (kombinerade source_file/pdf_hash i period SELECT)
- [x] Fixat "Failed to attach prompt" (GetPromptResult return type)
- [x] Tagit bort oanvänd format_chart_data() funktion
