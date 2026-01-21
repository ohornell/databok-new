# TODO - Databok Deployment

## Kvar att göra

### 1. Deploya till Railway
- [ ] Skapa Railway-projekt
- [ ] Koppla GitHub-repo
- [ ] Sätt miljövariabler:
  - `ANTHROPIC_API_KEY`
  - `SUPABASE_URL`
  - `SUPABASE_KEY`
  - `VOYAGE_API_KEY` (för semantisk sökning)
  - `USE_CLOUD_STORAGE=true` (för Supabase Storage)

### 2. Testa MCP-anslutningar
- [ ] **Lokal MCP** - Verifiera att `mcp_server/server.py` fungerar med Claude Desktop lokalt
- [ ] **Remote MCP** - Testa `/mcp/sse` endpoint efter deploy
  - Claude Desktop → Settings → MCP → Add Remote Server
  - URL: `https://din-railway-url.com/mcp/sse`

### 4. GitHub → Railway auto-deploy
- [ ] Aktivera auto-deploy i Railway från GitHub
  - Railway Dashboard → Settings → Deployments → Enable GitHub Integration
  - Välj branch: `main`
- [ ] Verifiera att push till GitHub triggar ny deploy
- [ ] (Valfritt) Sätt upp deploy-notifikationer

**Flöde:**
```
VS Code → git commit → git push → GitHub → Railway auto-deploy → Ny version live
```

---

### 5. Skalbar mappstruktur med listor (OMX30, Large Cap, etc.)
- [ ] Utöka databasschema med `lists`-tabell
- [ ] Lägg till koppling mellan bolag och listor (many-to-many)
- [ ] Uppdatera API med endpoints för listhantering
- [ ] Uppdatera MCP med verktyg för att filtrera på lista

**Förslag på databasschema:**
```sql
-- Listor (OMX30, Large Cap, etc.)
CREATE TABLE lists (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,        -- "OMX30", "Large Cap", "Mid Cap", "Small Cap"
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Koppling bolag <-> listor (many-to-many)
CREATE TABLE company_lists (
  company_id UUID REFERENCES companies(id),
  list_id UUID REFERENCES lists(id),
  added_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (company_id, list_id)
);
```

**Nya API-endpoints:**
- `GET /lists` - Lista alla listor
- `GET /lists/{name}/companies` - Bolag i en lista
- `POST /lists/{name}/companies/{slug}` - Lägg till bolag i lista
- `DELETE /lists/{name}/companies/{slug}` - Ta bort bolag från lista

**MCP-verktyg:**
- `list_by_category` - "Visa alla Large Cap-bolag"
- `get_financials` utökat med `list`-filter

---

### 6. Organiserad PDF-lagring med auto-rename
- [ ] Integrera rename-skriptet i upload-flödet
- [ ] Spara PDF:er i strukturerad mappstruktur
- [ ] Lagra i Supabase Storage med organiserad sökväg

**Filstruktur i Supabase Storage:**
```
pdfs/
├── companies/
│   ├── vitrolife/
│   │   ├── 2024/
│   │   │   ├── Vitrolife_Q1_2024.pdf
│   │   │   ├── Vitrolife_Q2_2024.pdf
│   │   │   └── Vitrolife_Q3_2024.pdf
│   │   └── 2023/
│   │       └── Vitrolife_Q4_2023.pdf
│   └── mycronic/
│       └── 2024/
│           └── Mycronic_Q3_2024.pdf
└── results/
    └── {job_id}/
        └── {company}_databok.xlsx
```

**Namnkonvention:** `{Bolag}_Q{kvartal}_{år}.pdf`

**Implementation:**
1. Efter extraktion - hämta metadata (bolag, kvartal, år)
2. Rename PDF enligt konvention
3. Flytta till rätt mapp i Supabase Storage
4. Spara sökväg i `periods`-tabellen

**Kod att lägga till i `api/main.py`:**
```python
async def organize_pdf(job_id: str, company_slug: str, quarter: int, year: int, original_path: str):
    """Flytta och rename PDF till organiserad struktur."""
    new_filename = f"{company_slug.title()}_Q{quarter}_{year}.pdf"
    storage_path = f"companies/{company_slug}/{year}/{new_filename}"

    # Ladda upp till organiserad plats
    client = get_client()
    with open(original_path, "rb") as f:
        client.storage.from_("pdfs").upload(storage_path, f.read())

    return storage_path
```

---

### 3. Supabase Storage

**Vad används det till?**

Supabase Storage lagrar filer i molnet istället för lokalt på servern:

| Fil | Bucket | Syfte |
|-----|--------|-------|
| PDF-uppladdningar | `pdfs/uploads/{job_id}/` | Spara uppladdade kvartalsrapporter |
| Excel-resultat | `pdfs/results/{job_id}/` | Spara genererade databoks-Excel |

**Varför behövs det?**
- Railway/containers är stateless - filer försvinner vid omstart
- Användare kan ladda ner Excel-filer även efter server-restart
- Möjliggör delning av resultat mellan användare

**Setup:**
- [ ] Gå till Supabase Dashboard → Storage
- [ ] Skapa bucket `pdfs`
- [ ] Sätt RLS-policies (eller gör public för enkel setup)
- [ ] Sätt `USE_CLOUD_STORAGE=true` i Railway

---

## Redan klart

- [x] FastAPI backend med alla endpoints
- [x] Batch-upload med progress per fil
- [x] Remote MCP Server med SSE
- [x] Supabase Storage-integration (kod klar)
- [x] Dockerfile redo för deploy
