# Knowledge Modul - Finansiell Analytiker

## Syfte

Skapa en AI-analytiker med djup domänkunskap genom att lagra expertis i en sökbar databas istället för långa prompts. Claude hämtar automatiskt relevant kunskap vid analys.

## Varför databas istället för prompt/skill?

| Approach | Fördel | Nackdel |
|----------|--------|---------|
| **Lång prompt** | Alltid tillgänglig | Dyrt, långsamt, begränsad storlek (~100k tokens max) |
| **Skill-fil** | Strukturerad | Måste postas varje gång, blir för lång |
| **Databas + RAG** | Obegränsad kunskap, hämtar endast relevant del | Kräver bra chunking och sökning |

**Slutsats:** Databas är överlägsen för en "kunnig" analytiker.

---

## Databasstruktur

### Alternativ 1: Enkel flat struktur (Rekommenderad start)

```sql
CREATE TABLE knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Kategorisering
    domain VARCHAR(50) NOT NULL,       -- Huvudområde
    category VARCHAR(100) NOT NULL,    -- Underkategori

    -- Innehåll
    title VARCHAR(200) NOT NULL,       -- Rubrik (sökbar)
    content TEXT NOT NULL,             -- Kunskapen (500-1500 tecken)

    -- Sökmetadata
    tags TEXT[],                       -- Nyckelord för sökning
    related_metrics TEXT[],            -- Kopplar till finansiell data

    -- Vektor
    embedding VECTOR(1024),

    -- Metadata
    source VARCHAR(200),               -- Källa (FAR, CFA, intern)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Fördelar:**
- Enkelt att implementera
- Fungerar med befintlig embedding-infrastruktur
- Lätt att fylla på och underhålla

**Nackdelar:**
- Ingen hierarki mellan kunskapsposter
- Svårt att hantera beroenden (A förutsätter B)

---

### Alternativ 2: Hierarkisk struktur med relationer

```sql
-- Huvudtabell
CREATE TABLE knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain VARCHAR(50) NOT NULL,
    category VARCHAR(100) NOT NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,

    -- Hierarki
    parent_id UUID REFERENCES knowledge(id),  -- För nästlade koncept
    depth INT DEFAULT 0,                       -- 0=grundläggande, 1=avancerat, 2=expert

    -- Prereqs
    prerequisites UUID[],              -- Måste förstås först

    tags TEXT[],
    related_metrics TEXT[],
    embedding VECTOR(1024),
    source VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Relationer mellan kunskapsposter
CREATE TABLE knowledge_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_id UUID REFERENCES knowledge(id),
    to_id UUID REFERENCES knowledge(id),
    relation_type VARCHAR(50),         -- 'prerequisite', 'related', 'contradicts', 'extends'
    strength FLOAT DEFAULT 1.0         -- Hur stark är relationen
);
```

**Fördelar:**
- Kan bygga kunskapsgrafer
- Hämta prereqs automatiskt
- Bättre för komplexa ämnen

**Nackdelar:**
- Mer komplext att implementera
- Kräver mer arbete vid datainmatning
- Osäkert om komplexiteten ger värde

---

### Alternativ 3: Dokument + Chunk-struktur

```sql
-- Källdokument
CREATE TABLE knowledge_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    domain VARCHAR(50) NOT NULL,
    full_content TEXT NOT NULL,        -- Hela dokumentet
    source VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Chunks från dokumenten
CREATE TABLE knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES knowledge_documents(id),
    chunk_index INT NOT NULL,          -- Position i dokumentet
    content TEXT NOT NULL,             -- 500-1000 tecken
    embedding VECTOR(1024),

    -- Kontext
    prev_chunk_id UUID,                -- För att kunna hämta omgivning
    next_chunk_id UUID
);
```

**Fördelar:**
- Behåller dokumentkontext
- Kan expandera till omgivande chunks
- Bra för längre texter (läroböcker, standarder)

**Nackdelar:**
- Mer komplext
- Chunking-kvalitet avgörande
- Kanske overkill för punktkunskap

---

## Min rekommendation

**Börja med Alternativ 1** (enkel flat struktur) av följande skäl:

1. **Snabbt att implementera** - Kan vara igång på en dag
2. **Lätt att iterera** - Upptäcker du begränsningar kan du migrera
3. **Passar kunskapstypen** - Finansiell kunskap är ofta fristående fakta/formler
4. **Bevisat mönster** - Samma som fungerar för sections-sökning

Alternativ 2 och 3 kan övervägas senare om:
- Du märker att Claude missar prereqs
- Du vill importera hela läroböcker
- Kunskapen blir så stor (10,000+ poster) att struktur krävs

---

## Domäner och kategorier

### Förslag på taxonomi

```
nyckeltal/
├── lönsamhet/        (EBITDA, ROE, ROIC, marginaler)
├── likviditet/       (kassaflöde, current ratio, quick ratio)
├── skuldsättning/    (soliditet, nettoskuld, räntetäckning)
├── tillväxt/         (organisk, förvärvad, CAGR)
├── värdering/        (P/E, EV/EBITDA, P/S, DCF)
└── effektivitet/     (asset turnover, working capital)

redovisning/
├── IFRS/             (IFRS 15, 16, 9, etc.)
├── K3/               (svenska regler)
├── koncern/          (konsolidering, minoritet, goodwill)
└── periodisering/    (intäktsredovisning, avsättningar)

bransch/
├── SaaS/             (ARR, NRR, CAC, LTV, churn)
├── retail/           (LFL, butiksförsäljning, e-handel)
├── industri/         (kapacitet, orderbok, leveranstid)
├── fastighet/        (yield, vakans, hyresintäkter)
└── bank/             (NIM, K/I-tal, kreditförluster)

värdering/
├── multiplar/        (val av multipel, peers)
├── DCF/              (WACC, terminalvärde, tillväxt)
├── substans/         (NAV, break-up)
└── sum-of-parts/     (konglomerat)

kvalitativ/
├── röda_flaggor/     (earnings quality, aggressive accounting)
├── ledning/          (incitament, track record)
├── konkurrens/       (moat, switching costs)
└── ESG/              (miljö, social, governance)
```

---

## Optimal storlek per kunskapspost

| Typ | Tecken | Exempel |
|-----|--------|---------|
| **Definition** | 100-300 | "EBITDA = Rörelseresultat + Avskrivningar" |
| **Formel + förklaring** | 300-600 | Definition + när/varför använda |
| **Metod** | 600-1200 | Steg-för-steg process |
| **Kontext/bakgrund** | 1000-1500 | Fördjupning, historik, undantag |

**Tumregel:** En post ska svara på EN fråga väl.

---

## Index för prestanda

```sql
-- Vektor-sökning
CREATE INDEX knowledge_embedding_idx ON knowledge
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Kategorifiltrering
CREATE INDEX knowledge_domain_idx ON knowledge(domain);
CREATE INDEX knowledge_category_idx ON knowledge(category);

-- Tag-sökning
CREATE INDEX knowledge_tags_idx ON knowledge USING GIN(tags);
CREATE INDEX knowledge_metrics_idx ON knowledge USING GIN(related_metrics);
```

---

## Sökfunktion (Supabase RPC)

```sql
CREATE OR REPLACE FUNCTION search_knowledge(
    query_embedding VECTOR(1024),
    match_count INT DEFAULT 5,
    domain_filter TEXT DEFAULT NULL,
    category_filter TEXT DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    title TEXT,
    content TEXT,
    domain TEXT,
    category TEXT,
    tags TEXT[],
    related_metrics TEXT[],
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        k.id,
        k.title,
        k.content,
        k.domain,
        k.category,
        k.tags,
        k.related_metrics,
        1 - (k.embedding <=> query_embedding) AS similarity
    FROM knowledge k
    WHERE
        (domain_filter IS NULL OR k.domain = domain_filter)
        AND (category_filter IS NULL OR k.category = category_filter)
    ORDER BY k.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;
```

---

## MCP-verktyg

```python
Tool(
    name="search_knowledge",
    description="Sök i kunskapsdatabasen för analysmetoder, formler och best practices",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Sökfråga, t.ex. 'hur beräknas EBITDA-marginal'"
            },
            "domain": {
                "type": "string",
                "enum": ["nyckeltal", "redovisning", "bransch", "värdering", "kvalitativ"],
                "description": "Filtrera på domän (valfritt)"
            },
            "limit": {
                "type": "integer",
                "description": "Max antal resultat (default 5)"
            }
        },
        "required": ["query"]
    }
)
```

---

## Flöde vid analys

```
Användare: "Analysera Vitrolifes lönsamhet och jämför med förra året"

Claude:
1. Hämtar finansiell data
   → get_financials("vitrolife", "Q3 2025")
   → get_financials("vitrolife", "Q3 2024")

2. Söker relevant kunskap
   → search_knowledge("lönsamhetsanalys marginal EBITDA jämförelse")

   Returnerar:
   - "EBITDA-marginal - definition och beräkning"
   - "Jämförande analys - perioder"
   - "Marginalkompression - orsaker och tolkning"

3. Kombinerar data + kunskap
   → Beräknar marginaler enligt rätt formel
   → Identifierar förändringar
   → Förklarar med korrekt terminologi

4. Levererar analys med källhänvisning
```

---

## Exempel på kunskapsposter

### Post 1: Definition
```json
{
    "domain": "nyckeltal",
    "category": "lönsamhet",
    "title": "EBITDA-marginal",
    "content": "EBITDA-marginal = EBITDA / Nettoomsättning × 100\n\nEBITDA (Earnings Before Interest, Tax, Depreciation & Amortization) är rörelseresultat före av- och nedskrivningar.\n\nTypiska nivåer:\n- Mjukvara/SaaS: 25-40%\n- Industri: 10-20%\n- Retail: 5-10%\n- Tjänster: 15-25%",
    "tags": ["EBITDA", "marginal", "lönsamhet"],
    "related_metrics": ["EBITDA", "Nettoomsättning", "Rörelseresultat"]
}
```

### Post 2: Metod
```json
{
    "domain": "kvalitativ",
    "category": "röda_flaggor",
    "title": "Röda flaggor i kassaflöde vs resultat",
    "content": "Varningssignaler när kassaflöde och resultat divergerar:\n\n1. Vinst men negativt operativt kassaflöde\n   - Kundfordringar växer snabbare än försäljning\n   - Lager byggs upp utan motsvarande efterfrågan\n   - Aggressiv intäktsredovisning\n\n2. Kontrollera:\n   - Förändring i rörelsekapital som % av omsättning\n   - Days Sales Outstanding (DSO) trend\n   - Inventory turnover\n\n3. Godtagbara förklaringar:\n   - Säsongsvariation\n   - Stora kundkontrakt med lång betalningstid\n   - Strategisk lageruppbyggnad",
    "tags": ["kassaflöde", "röda flaggor", "earnings quality", "rörelsekapital"],
    "related_metrics": ["Kassaflöde från löpande verksamheten", "Nettoresultat", "Rörelsekapital"]
}
```

### Post 3: Branschspecifik
```json
{
    "domain": "bransch",
    "category": "SaaS",
    "title": "Rule of 40 för SaaS-bolag",
    "content": "Rule of 40: Tillväxt(%) + Marginal(%) >= 40%\n\nExempel:\n- 30% tillväxt + 15% marginal = 45% ✓\n- 10% tillväxt + 20% marginal = 30% ✗\n\nTolkning:\n- >40%: Utmärkt balans\n- 30-40%: Acceptabelt\n- <30%: Varningsflagga\n\nVanliga mått:\n- Tillväxt: ARR-tillväxt eller revenue growth\n- Marginal: EBITDA-marginal eller FCF-marginal\n\nBegränsningar:\n- Fungerar bäst för mogna SaaS-bolag\n- Tidiga bolag kan prioritera tillväxt\n- Beakta inte kapitalintensitet",
    "tags": ["SaaS", "Rule of 40", "tillväxt", "marginal"],
    "related_metrics": ["ARR", "Tillväxt", "EBITDA-marginal"]
}
```

---

## Nästa steg

1. [ ] Skapa tabellen i Supabase
2. [ ] Lägg till RPC-funktion för sökning
3. [ ] Implementera `search_knowledge` i MCP server
4. [ ] Skapa script för att generera embeddings
5. [ ] Börja fylla på med kunskapsposter (börja med 20-50)
6. [ ] Testa och iterera

---

## Öppna frågor

1. **Ska knowledge-sökning ske automatiskt?**
   - Alt A: Claude avgör själv när den behöver söka
   - Alt B: MCP returnerar alltid relevant kunskap med finansiell data

2. **Hur hantera motstridiga rekommendationer?**
   - T.ex. olika värderingsmetoder för samma situation

3. **Versionering av kunskap?**
   - Redovisningsregler ändras (IFRS uppdateringar)
   - Behövs `valid_from`/`valid_to`?

4. **Flerspråkigt?**
   - Kunskap på svenska och engelska?
   - Eller lita på Voyage-3 cross-lingual?
