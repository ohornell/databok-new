# TODO - MCP Server

## Att göra

- [ ] Lägg till sidnummer på all extraherad data
  - [ ] Uppdatera rapport_extraktor: spara page_number på alla tabeller (inte bara KPIs/charts)
  - [ ] Uppdatera databasschemat om nödvändigt
  - [ ] Lägg till `get_page(company, period, page_number)` verktyg i MCP
  - [ ] Re-extrahera befintliga rapporter för att få sidnummer

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

- Sidnummer saknas på finansiella tabeller (resultat, balans, kassaflöde)
  - Gör det omöjligt att svara på "Visa sida X" eller "Summera sida Y"
  - Behöver fixas i rapport_extraktor för att spara page_number på alla tabeller
  - MCP behöver nytt verktyg: `get_page(company, period, page_number)`

- Claude Desktop visar inte källor från JSON-responsen
- Claude Desktop visar inte ASCII-visualiseringar

## Långsiktiga idéer

- Styrelseanalytiker - extrahera och analysera styrelsematerial
  - Nya dokumenttyper: styrelseprotokoll, beslutsunderlag, strategidokument
  - Knowledge-tabell med strategikunskap, governance best practices, beslutsanalys
  - Skills för: strategiutvärdering, riskanalys, ESG-bedömning, succession planning
  - Kombinera med finansiell data för helhetsbild

## Klart

- [x] Optimerat DB-queries (kombinerade source_file/pdf_hash i period SELECT)
- [x] Fixat "Failed to attach prompt" (GetPromptResult return type)
- [x] Tagit bort oanvänd format_chart_data() funktion
