#!/usr/bin/env python3
"""
Script för att populera knowledge-databasen med kunskap om justeringsposter.
Fokus på att förstå underliggande lönsamhet och otillåtna justeringar.
"""

import os
import sys
import requests
from dotenv import load_dotenv
from supabase import create_client

# Ladda miljövariabler
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://zgynsljvyympqiengxyp.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_Y2IvRKczw9afOobEeXRgww_PZxOs9kl")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")

client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_embedding(text: str) -> list[float] | None:
    """Skapa embedding via Voyage API."""
    if not VOYAGE_API_KEY:
        return None
    try:
        response = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {VOYAGE_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "voyage-4",
                "input": [text],
                "input_type": "document"
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]
    except Exception as e:
        print(f"  Embedding-fel: {e}")
        return None


def add_knowledge(domain: str, category: str, title: str, content: str,
                  tags: list[str] = None, related_metrics: list[str] = None,
                  source: str = None) -> dict:
    """Lägg till en kunskapspost."""

    # Skapa embedding
    text_for_embedding = f"{title}\n\n{content}"
    embedding = get_embedding(text_for_embedding)

    data = {
        "domain": domain,
        "category": category,
        "title": title,
        "content": content,
        "tags": tags or [],
        "related_metrics": related_metrics or [],
        "source": source
    }

    if embedding:
        data["embedding"] = embedding

    try:
        result = client.table("knowledge").insert(data).execute()
        if result.data:
            return {"success": True, "id": result.data[0]["id"], "title": title}
        return {"error": "Kunde inte spara"}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# KUNSKAPSPOSTER OM JUSTERINGSPOSTER OCH JUSTERAT RESULTAT
# =============================================================================

KNOWLEDGE_ITEMS = [
    # -----------------------------------------
    # GRUNDLÄGGANDE OM JUSTERAT RESULTAT
    # -----------------------------------------
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Justerat EBITDA vs Rapporterat EBITDA",
        "content": """**Justerat EBITDA** (Adjusted EBITDA) är ett icke-GAAP-mått där bolaget exkluderar poster
som de anser inte representerar underliggande lönsamhet.

**Rapporterat EBITDA:**
EBITDA = Rörelseresultat + Avskrivningar + Nedskrivningar

**Justerat EBITDA:**
Justerat EBITDA = EBITDA + Justeringsposter

**Vanliga justeringar (potentiellt legitima):**
- Omstruktureringskostnader (engångs)
- Förvärvsrelaterade kostnader
- Nedskrivningar av goodwill
- Valutaeffekter (orealiserade)
- Aktiebaserade ersättningar

**Varför bolag justerar:**
1. Visa "underliggande" lönsamhet utan störningar
2. Underlätta jämförbarhet över tid
3. Matcha hur ledningen styr verksamheten

**KRITISKT:**
Det finns INGEN standard för vad som får justeras. Varje bolag definierar själv.
Jämför alltid justerat resultat med kassaflöde för att verifiera kvaliteten.""",
        "tags": ["justerat EBITDA", "adjusted EBITDA", "non-GAAP", "justeringar", "underliggande"],
        "related_metrics": ["EBITDA", "Justerat EBITDA", "Rörelseresultat"],
        "source": "SEC/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Justerat EBIT (EBITA) vs Rapporterat EBIT",
        "content": """**Justerat EBIT** exkluderar poster som bolaget anser vara av engångskaraktär.

**Beräkning:**
Justerat EBIT = EBIT + Justeringsposter

**Skillnad mot EBITA:**
- EBITA = EBIT + Avskrivningar på förvärvade immateriella (PPA-avskrivningar)
- Justerat EBIT = EBIT + Diverse justeringar (bredare definition)

**Serieförvärvare använder ofta EBITA** eftersom PPA-avskrivningar:
- Är "non-cash"
- Varierar kraftigt beroende på förvärvshistorik
- Gör jämförelser mellan bolag svåra

**Justerat EBIT används för att exkludera:**
- Omstruktureringskostnader
- Transaktionskostnader vid förvärv
- Nedskrivningar
- Engångsposter

**Analytisk approach:**
1. Titta på justeringarnas storlek relativt EBIT
2. Om justeringar > 20% av EBIT → Granska kritiskt
3. Är justeringarna verkligen engångs eller återkommande?""",
        "tags": ["justerat EBIT", "EBITA", "adjusted EBIT", "justeringar"],
        "related_metrics": ["EBIT", "EBITA", "Justerat EBIT", "PPA-avskrivningar"],
        "source": "CFA/Intern"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Justerat nettoresultat och justerad vinst per aktie",
        "content": """**Justerat nettoresultat** exkluderar poster för att visa "underliggande" vinst.

**Beräkning:**
Justerat nettoresultat = Nettoresultat + Justeringar (efter skatt)

**Viktigt: Skatteeffekt**
Justeringar ska göras EFTER skatt för att vara korrekta:
- Kostnad på 100 MSEK med 20.6% skatt = 79.4 MSEK nettojustering
- Vissa poster (t.ex. goodwillnedskrivning) är ej avdragsgilla

**Justerad EPS (Earnings Per Share):**
Justerad EPS = Justerat nettoresultat / Antal aktier

**Vanliga justeringar:**
- Omstruktureringskostnader
- Nedskrivningar
- Förvärvsrelaterade kostnader
- Avvecklad verksamhet
- Orealiserade valutaeffekter
- Orealiserade finansiella derivat

**Röda flaggor:**
- Justeringar varje kvartal under flera år = EJ engångs
- Justerad EPS konsekvent mycket högre än rapporterad EPS
- Skatteeffekt saknas eller är felaktig""",
        "tags": ["justerat nettoresultat", "justerad EPS", "adjusted earnings", "vinst per aktie"],
        "related_metrics": ["Nettoresultat", "EPS", "Justerad EPS"],
        "source": "CFA/SEC"
    },

    # -----------------------------------------
    # KATEGORIER AV JUSTERINGSPOSTER
    # -----------------------------------------
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Omstruktureringskostnader som justeringspost",
        "content": """**Omstruktureringskostnader** är bland de vanligaste justeringsposterna.

**Vad ingår:**
- Avgångsvederlag och uppsägningskostnader
- Kostnader för att stänga anläggningar
- Flytt av verksamhet
- Nedskrivning av tillgångar kopplade till omstruktureringen
- Konsultkostnader för omstrukturering

**Legitim justering OM:**
- Tydligt definierad omstruktureringsplan
- Engångskaraktär (avslutad inom 12-24 månader)
- Kan verifieras mot kassaflöde
- Inte återkommande varje år

**Röda flaggor:**
- "Omstrukturering" varje år i 5+ år = INTE engångs
- Vaga beskrivningar utan konkreta åtgärder
- Omstruktureringskostnader som växer trots att bolaget säger att de "effektiviserar"
- Kostnaden rullar vidare kvartal efter kvartal

**Analysera:**
1. Hur länge har bolaget haft omstruktureringskostnader?
2. Har tidigare omstruktureringar gett resultat?
3. Matchar kassaflödet kostnaderna?""",
        "tags": ["omstrukturering", "restructuring", "engångskostnader", "avgångsvederlag"],
        "related_metrics": ["EBITDA", "Justerat EBITDA", "Kassaflöde"],
        "source": "IAS 37/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Förvärvsrelaterade kostnader som justeringspost",
        "content": """**Förvärvsrelaterade kostnader** uppstår vid företagsförvärv och justeras ofta bort.

**Transaktionskostnader (IFRS 3):**
Kostnadsförs direkt enligt IFRS, får EJ aktiveras:
- Due diligence (juridik, revision, finansiell analys)
- Rådgivningsarvoden (M&A-banker)
- Registreringsavgifter
- Integrationskostnader

**Typiska belopp:**
- 2-5% av transaktionsvärdet för transaktionskostnader
- 5-15% av transaktionsvärdet för integration

**Legitim justering OM:**
- Tydligt kopplade till specifika förvärv
- Engångskaraktär per förvärv
- Rimlig storlek

**Röda flaggor för serieförvärvare:**
- "Förvärvsrelaterade kostnader" varje kvartal = Del av affärsmodellen
- Om förvärv är kärnverksamhet, är kostnaden EJ engångs
- Integrationssynergier utlovade men kostnader fortsätter

**Analytisk syn:**
För serieförvärvare är förvärvsrelaterade kostnader en LÖPANDE kostnad.
Acceptera max justering för själva transaktionen, EJ integration.""",
        "tags": ["förvärv", "M&A", "transaktionskostnader", "integration", "due diligence"],
        "related_metrics": ["EBITDA", "Förvärvskostnad", "Goodwill"],
        "source": "IFRS 3/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Aktiebaserade ersättningar som justeringspost",
        "content": """**Aktiebaserade ersättningar** (Share-based compensation, SBC) justeras ofta bort.

**Argument FÖR justering:**
- "Non-cash" - påverkar inte kassaflödet direkt
- Svårt att uppskatta verkligt värde
- Stor variation mellan bolag

**Argument MOT justering:**
- Det ÄR en reell kostnad - utspädning för aktieägare
- Om SBC inte fanns skulle bolaget betala kontant lön istället
- Tech-bolag med hög SBC "gömmer" stor del av personalkostnaden

**SEC:s och analytikernas syn:**
SBC är en REELL kostnad och bör INTE justeras bort i de flesta fall.
Om man justerar för SBC, måste man också:
1. Visa justerat kassaflöde inkl. återköp för att motverka utspädning
2. Visa utspädning från optioner/aktier

**Typiska nivåer:**
- Tech/SaaS: 15-25% av intäkterna kan vara SBC
- Industribolag: 1-5% av intäkterna
- Finansbolag: 5-15% av intäkterna

**Analysera:**
1. Hur stor är SBC relativt intäkter och EBITDA?
2. Hur mycket utspädning sker?
3. Gör bolaget återköp för att motverka utspädning?""",
        "tags": ["SBC", "aktieersättning", "optioner", "utspädning", "non-cash"],
        "related_metrics": ["EBITDA", "Personalkostnader", "Utspädning"],
        "source": "SEC/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Nedskrivningar som justeringspost",
        "content": """**Nedskrivningar** exkluderas ofta för att visa "underliggande" lönsamhet.

**Typer av nedskrivningar:**

1. **Goodwill-nedskrivning:**
   - Signalerar att förvärv inte levererat
   - Ofta mycket stora belopp
   - Kassaflödesneutral

2. **Nedskrivning av immateriella tillgångar:**
   - Teknik, varumärken, kundrelationer
   - Indikerar försämrad värdering

3. **Nedskrivning av materiella tillgångar:**
   - Fabriker, maskiner, fastigheter
   - Kan vara förvarning om avyttring/stängning

4. **Nedskrivning av finansiella tillgångar:**
   - Aktieinnehav, lån
   - Kan indikera problem hos dotterbolag/intressebolag

**Legitim justering OM:**
- Tydlig engångskaraktär
- Ingen cash-effekt (redan betalt)
- Signifikant belopp

**Röda flaggor:**
- Upprepade nedskrivningar = systematiska felaktiga förvärv/investeringar
- "Strategisk översyn" som leder till nedskrivning = Döljer operativa problem
- Nedskrivning följd av försäljning till lågt pris""",
        "tags": ["nedskrivning", "impairment", "goodwill", "tillgångar"],
        "related_metrics": ["Goodwill", "EBIT", "Tillgångar"],
        "source": "IAS 36/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Valutaeffekter som justeringspost",
        "content": """**Valutaeffekter** delas upp i realiserade och orealiserade.

**Orealiserade valutaeffekter:**
- Omvärdering av tillgångar/skulder i utländsk valuta vid bokslut
- Ingen kassaflödeseffekt förrän realisering
- Kan svänga kraftigt mellan perioder

**Realiserade valutaeffekter:**
- Faktisk vinst/förlust vid betalning/mottagande
- Påverkar kassaflöde

**Var redovisas valutaeffekter:**
- Operativa: I rörelseresultatet (om valutakontrakt säkrar rörelsen)
- Finansiella: I finansnetto (omvärdering av lån etc.)
- OCI: Omräkning av utländska dotterbolag

**Legitim justering för OREALISERADE effekter OM:**
- Tydligt separerade från realiserade
- Konsistent behandling över tid
- Underliggande exponering förklaras

**Röda flaggor:**
- Justerar bort alla valutaeffekter (även realiserade)
- Stor valutaexponering utan säkring
- Valuta som permanent "ursäkt" för dåligt resultat

**Analysera:**
1. Är valutaeffekten realiserad eller orealiserad?
2. Hur stor är bolagets naturliga valutaexponering?
3. Har bolaget säkrat exponeringen?""",
        "tags": ["valuta", "valutaeffekt", "orealiserad", "realiserad", "hedging"],
        "related_metrics": ["Finansnetto", "EBIT", "Kassaflöde"],
        "source": "IAS 21/CFA"
    },

    # -----------------------------------------
    # RÖDA FLAGGOR OCH OTILLÅTNA JUSTERINGAR
    # -----------------------------------------
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Röda flaggor: Återkommande engångsposter",
        "content": """**Återkommande "engångsposter"** är den vanligaste röda flaggan vid resultatjusteringar.

**Definition av problemet:**
Om en post är "engångs" bör den inte upprepas. Om samma typ av kostnad uppstår
varje år eller varje kvartal är det en LÖPANDE kostnad.

**Kontrollera:**
1. Gå tillbaka 3-5 år i rapporterna
2. Lista alla justeringsposter
3. Beräkna: Hur många gånger har varje typ justerats?

**Typiska mönster:**

| Justeringspost | Röd flagga om |
|----------------|---------------|
| Omstrukturering | > 2 år i rad |
| Förvärvsrelaterat | Varje kvartal (serieförvärvare) |
| "Övriga engångsposter" | Odefinierad kategori varje period |
| Nedskrivning | > 1 gång på 3 år |

**Beräkna "normaliserat" resultat:**
Ta genomsnittet av justeringsposterna över 3-5 år och dra av det från justerat EBITDA.
Detta ger ett mer rättvisande mått på underliggande lönsamhet.

**Exempel:**
Bolag justerar 50 MSEK/år för "engångsposter" i 5 år.
Totalt: 250 MSEK = Ca 50 MSEK/år i "löpande engångskostnader"
→ Dra av 50 MSEK från justerat EBITDA för realistisk bild.""",
        "tags": ["röda flaggor", "engångsposter", "återkommande", "normalisering"],
        "related_metrics": ["Justerat EBITDA", "EBITDA"],
        "source": "CFA/Intern"
    },
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Röda flaggor: Justeringar utan kassaflödesmatchning",
        "content": """**Kassaflöde avslöjar kvaliteten** på justerade resultatmått.

**Grundprincip:**
Om justerat EBITDA är "rättvisande" bör operativt kassaflöde följa samma trend.

**Beräkna kassakonvertering:**
Cash Conversion = Operativt kassaflöde / Justerat EBITDA

**Förväntade nivåer:**
- Hälsosamt: 70-100%+ kassakonvertering
- Varning: 50-70% - granska rörelsekapital
- Röd flagga: <50% konsekvent

**Varför låg kassakonvertering är en varning:**
1. Justeringar kanske inte är "riktiga" engångsposter
2. Rörelsekapitalet växer okontrollerat
3. CapEx klassificeras fel
4. Aggressiv intäktsredovisning

**Specifik analys:**
1. Jämför justerat EBITDA-trend med kassaflödestrend
2. Om justerat EBITDA växer men kassaflöde stagnerar → Granska kritiskt
3. Summera alla justeringar över 3 år - motsvaras de av kassaflödeseffekter?

**Exempel på manipulation:**
Justerat EBITDA: +15% tillväxt
Operativt kassaflöde: -5%
→ Något stämmer inte. Justeringarna döljer sannolikt löpande kostnader.""",
        "tags": ["kassaflöde", "kassakonvertering", "kvalitet", "röda flaggor"],
        "related_metrics": ["Operativt kassaflöde", "Justerat EBITDA", "Kassakonvertering"],
        "source": "CFA/Intern"
    },
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Röda flaggor: Vaga och ökande justeringar",
        "content": """**Vaga justeringsposter** och **ökande justeringsbelopp** är allvarliga varningssignaler.

**Vaga poster - exempel:**
- "Övriga justeringar"
- "Diverse engångsposter"
- "Poster av engångskaraktär"
- "Speciella poster"
→ Dessa säger INGENTING om vad kostnaden faktiskt är

**Krav på transparens:**
Bolaget bör specificera:
1. Exakt vad justeringen avser
2. Varför den är av engångskaraktär
3. När den förväntas upphöra
4. Kassaflödeseffekt

**Ökande justeringar över tid:**
| År | Justerat EBITDA | Justeringar | % av EBITDA |
|----|-----------------|-------------|-------------|
| 2021 | 100 | 10 | 10% |
| 2022 | 110 | 20 | 18% |
| 2023 | 120 | 35 | 29% |
| 2024 | 130 | 50 | 38% |

→ Justeringarna växer snabbare än EBITDA = stor röd flagga

**Analysera:**
1. Hur stor andel av justerat EBITDA utgörs av justeringar?
2. Ökar eller minskar andelen över tid?
3. Är justeringarna tydligt definierade?

**Tumregel:**
Om justeringar > 25% av justerat EBITDA, granska bolaget extra kritiskt.""",
        "tags": ["röda flaggor", "vaga justeringar", "transparens", "ökande"],
        "related_metrics": ["Justerat EBITDA", "EBITDA", "Justeringsposter"],
        "source": "CFA/Intern"
    },
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Otillåtna justeringar: SEC-guidning",
        "content": """**SEC (US Securities and Exchange Commission)** har utfärdat vägledning om otillåtna non-GAAP-justeringar.

**Explicit otillåtna justeringar:**

1. **Normal, återkommande kassaposition:**
   - Rörelsekapitalförändringar som är del av normal verksamhet
   - Reguljära underhållsinvesteringar (CapEx)

2. **Intäktsjusteringar utan tydlig grund:**
   - Exkludera intäkter som "inte representerar kärnverksamhet"
   - Justera för "mix-effekter"

3. **Kostnader som är nödvändiga för verksamheten:**
   - Löpande legala kostnader
   - Normal forskning och utveckling
   - Normala marknadsföringskostnader

4. **Individuellt anpassa justeringar:**
   - Selektivt välja vilka perioder som justeras
   - Ändra justeringsdefinitioner mellan perioder

**SEC kräver:**
- Reconciliation till närmaste GAAP-mått
- Lika framträdande presentation av GAAP och non-GAAP
- Förklaring till varför non-GAAP är relevant
- Konsistent tillämpning över tid

**Svenska bolag:**
Sverige har inte lika strikta regler, men analytiker bör tillämpa samma principer.""",
        "tags": ["SEC", "non-GAAP", "otillåtet", "reglering", "compliance"],
        "related_metrics": ["Justerat EBITDA", "Non-GAAP"],
        "source": "SEC Regulation G/S-K"
    },

    # -----------------------------------------
    # PRAKTISK ANALYSMETODIK
    # -----------------------------------------
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Checklista för analys av justeringsposter",
        "content": """**Systematisk genomgång av bolagets justeringsposter:**

**STEG 1: Samla data (3-5 år)**
□ Lista alla justeringsposter per kvartal/år
□ Belopp och beskrivning för varje post
□ Beräkna total justering per period

**STEG 2: Kategorisera**
□ Omstrukturering
□ Förvärvsrelaterat
□ Nedskrivningar
□ Valutaeffekter
□ Aktiebaserade ersättningar
□ Övriga/oklara

**STEG 3: Analysera mönster**
□ Vilka poster återkommer?
□ Ökar eller minskar justeringarna?
□ Hur stor andel av EBITDA?

**STEG 4: Kassaflödestest**
□ Kassakonvertering (OCF/Justerat EBITDA)
□ Följer kassaflöde och justerat EBITDA samma trend?
□ Har justeringarna faktisk kassaeffekt?

**STEG 5: Jämför med peers**
□ Använder konkurrenter liknande justeringar?
□ Hur stora är justeringarna relativt peers?

**STEG 6: Skapa eget normaliserat mått**
□ Behåll endast verkliga engångsposter
□ Lägg tillbaka återkommande "engångskostnader"
□ Inkludera SBC om stor

**Dokumentera din analys och avvikelser från bolagets justerade mått.**""",
        "tags": ["checklista", "analysmetodik", "justeringar", "normalisering"],
        "related_metrics": ["Justerat EBITDA", "Kassaflöde", "EBITDA"],
        "source": "Intern/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Brygga: Rapporterat till Justerat resultat",
        "content": """**EBITDA-brygga** visar hur bolaget går från rapporterat till justerat resultat.

**Typisk presentation i kvartalsrapporter:**

```
Rörelseresultat (EBIT)                     100
+ Avskrivningar                             30
+ Nedskrivningar                            10
= EBITDA (rapporterat)                     140

Justeringar:
+ Omstruktureringskostnader                 15
+ Förvärvsrelaterade kostnader               8
+ Nedskrivning goodwill                     25
+ Aktiebaserade ersättningar                12
= Justerat EBITDA                          200
```

**Vad du ska leta efter:**
1. **Proportioner**: Justeringar = 60 MSEK på 140 MSEK EBITDA (43%!) = Högt
2. **Kategorier**: Är alla poster verkligen engångs?
3. **Trend**: Jämför med tidigare perioder

**Skapa egen brygga:**
Ta bolagets justerade EBITDA och gör din egen bedömning:

```
Bolagets justerade EBITDA                  200
- Återkommande "engångsposter" (3-års snitt) -20
- SBC (om ej inkluderat)                   -12
= Analytikerjusterat EBITDA                168
```

**Jämför ditt mått med kassaflöde:**
Operativt kassaflöde / Ditt justerade EBITDA = Kvalitetsindikator""",
        "tags": ["brygga", "reconciliation", "EBITDA", "justering"],
        "related_metrics": ["EBITDA", "Justerat EBITDA", "Rörelseresultat"],
        "source": "Intern"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Justeringar vid värdering: EV/EBITDA",
        "content": """**Vid värdering** måste justeringar hanteras konsekvent för att undvika fel.

**Grundprincip:**
Om du använder justerat EBITDA i nämnaren, justera även Enterprise Value för relaterade poster.

**EV/Justerat EBITDA:**
```
EV = Börsvärde + Nettoskuld + Leasingskulder - Kassa

Justerat EBITDA = EBITDA + Justeringar
```

**Vanliga misstag:**

1. **Inkonsekvent justering:**
   - Använder justerat EBITDA (högt tal)
   - Men utelämnar skulder relaterade till justeringarna
   → Ger för låg multipel

2. **Dubbelräkning:**
   - Justerar för omstruktureringskostnader i EBITDA
   - Men har redan avsättning i balansräkningen (skuld)

3. **Engångsposter i prognoser:**
   - Använder historiskt justerat EBITDA för värdering
   - Men prognostiserar framtida år UTAN engångskostnader
   → Övervärderar bolaget

**Best practice:**
1. Beräkna EV/EBITDA på BÅDE rapporterat och justerat
2. Om stor skillnad → Analysera justeringarna
3. Gör egen normalisering för värdering
4. Jämför multipelspridning med peers på samma basis""",
        "tags": ["EV/EBITDA", "värdering", "multipel", "justeringar"],
        "related_metrics": ["EV/EBITDA", "Enterprise Value", "Justerat EBITDA"],
        "source": "CFA/Värdering"
    },

    # -----------------------------------------
    # BRANSCHSPECIFIKA JUSTERINGAR
    # -----------------------------------------
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "SaaS-bolag: Vanliga justeringar",
        "content": """**SaaS-bolag** har ofta specifika justeringsposter som kräver särskild analys.

**Typiska justeringar:**

1. **Aktiebaserade ersättningar (SBC)**
   - Ofta 15-30% av intäkterna
   - Nödvändigt för att rekrytera/behålla talang
   - Skapa STOR utspädning
   - BÖR troligen INTE justeras bort helt

2. **Kundförvärvskostnader (CAC)**
   - Sälj- och marknadsföringskostnader
   - Vissa bolag vill kapitalisera och amortera
   - Normalt ej tillåtet under GAAP/IFRS

3. **Integrationskostnader vid förvärv**
   - Datamigrering, systemintegration
   - Kan vara legitim engångsjustering

**SaaS-specifika mått:**
- ARR (Annual Recurring Revenue) - intäktsbaserat
- Rule of 40: Tillväxt + EBITDA-marginal ≥ 40%
- LTV/CAC - unit economics

**Analysera SaaS-justeringar:**
1. Hur stor är SBC relativt ARR?
2. Vad är utspädningstakten?
3. Görs återköp för att motverka utspädning?
4. Hur definierar bolaget "recurring" revenue?

**Röda flaggor:**
- SBC > 25% av intäkter utan motsvarande återköp
- "Adjusted ARR" som inkluderar icke-återkommande intäkter""",
        "tags": ["SaaS", "SBC", "CAC", "ARR", "tech"],
        "related_metrics": ["ARR", "EBITDA", "SBC", "Rule of 40"],
        "source": "SaaS Capital/CFA"
    },
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Serieförvärvare: Justeringar och EBITA",
        "content": """**Serieförvärvare** (Lifco, Indutrade, Addtech, Lagercrantz etc.) har unika justeringsbehov.

**Standardjusteringar för serieförvärvare:**

1. **PPA-avskrivningar → EBITA**
   - Avskrivningar på kundrelationer, varumärken, teknologi
   - Non-cash, varierar med förvärvshistorik
   - EBITA = EBIT + PPA-avskrivningar
   - Allmänt accepterat som relevant mått

2. **Förvärvsrelaterade kostnader**
   - Due diligence, transaktionsrådgivning
   - OBS: Om förvärv är kärnverksamhet = LÖPANDE kostnad
   - Transaktionskostnader kan vara engångs, integration är ofta inte det

3. **Omvärdering av tilläggsköpeskilling**
   - Earn-out uppjusteringar/nedjusteringar
   - Finansiell post, ej operativ
   - Kan vara volatil

**Vad ska INTE justeras:**
- Integrationskostnader (del av affärsmodellen)
- Löpande transaktionskostnader (om man gör 5-10 förvärv/år)
- Nedskrivning av goodwill (om det sker återkommande)

**Analysera serieförvärvare:**
1. EBITA-marginal och utveckling
2. Avkastning på sysselsatt kapital (ROCE)
3. Cash conversion (OCF/EBITA)
4. Förvärvsmultiplar över tid (blir de dyrare?)""",
        "tags": ["serieförvärvare", "EBITA", "PPA", "förvärv", "Lifco", "Indutrade"],
        "related_metrics": ["EBITA", "EBIT", "PPA-avskrivningar", "ROCE"],
        "source": "Intern/CFA"
    },
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Fastighetsbolag: FFO och justerat resultat",
        "content": """**Fastighetsbolag** använder FFO (Funds From Operations) som justerat mått.

**FFO-definition:**
FFO = Nettoresultat + Avskrivningar på fastigheter + Nedskrivningar - Vinst vid fastighetsförsäljning

**Varför FFO:**
- Fastigheter skrivs av men ökar ofta i värde
- Avskrivningar är "artificiella" för fastigheter
- FFO visar kassagenerering bättre

**AFFO (Adjusted FFO):**
AFFO = FFO - Underhållsinvesteringar - Leasing-kostnader

AFFO är närmare verkligt kassaflöde eftersom underhåll krävs.

**Värdeförändring på fastigheter (IFRS):**
- Redovisas i resultaträkningen
- Orealiserad - ingen kassaeffekt
- Ofta justeras bort för att visa "underliggande" drift

**Röda flaggor för fastighetsbolag:**
- Aggressiva uppvärderingar utan transaktionsstöd
- Yield som ser "för bra" ut
- Stora skillnader mellan FFO och AFFO
- Hög belåning i kombination med värdefall

**Analysera:**
1. FFO/Aktie-utveckling (utdelningskapacitet)
2. AFFO/FFO-kvot (underhållsbehov)
3. NAV-rabatt/premie vs peers
4. LTV (Loan-to-Value) och räntetäckningsgrad""",
        "tags": ["fastigheter", "FFO", "AFFO", "NAV", "fastighetsbolag"],
        "related_metrics": ["FFO", "AFFO", "NAV", "Driftsnetto"],
        "source": "EPRA/CFA"
    },
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Industribolag: Cykliska justeringar",
        "content": """**Industribolag** har ofta cykliska mönster som komplicerar justeringsanalys.

**Typiska justeringsposter:**

1. **Omstrukturering vid nedgång:**
   - Kapacitetsanpassning
   - Personalminskningar
   - Anläggningsstängningar
   - Kan vara legitim engångspost i en cykel

2. **Garantiavsättningar:**
   - Kan svänga kraftigt
   - Justering om "onormal" nivå
   - Granska historik för att bedöma "normal"

3. **Lagernedskrivningar:**
   - Vanligt i nedgång
   - Ofta reverseras delvis i uppgång
   - Granska om återkommande

4. **Valutaeffekter:**
   - Stor exponering vanligt
   - Orealiserade vs realiserade

**Cyklisk analys:**
Jämför INTE bara år-för-år. Analysera:
- Peak-to-peak (topp till topp)
- Trough-to-trough (botten till botten)
- Genom-cykeln-marginal

**Normalisering för cykliska bolag:**
Använd 5-7 års genomsnittsmarginal istället för enskilt år.
Justera även ned toppår för att undvika övervärdering.

**Röda flaggor:**
- "Engångskostnader" i varje nedgång sedan 20 år tillbaka
- Marginaltoppar som inte kan upprätthållas""",
        "tags": ["industri", "cyklisk", "omstrukturering", "normalisering"],
        "related_metrics": ["EBITDA", "Marginal", "Rörelsekapital"],
        "source": "CFA/Intern"
    },
]


def main():
    """Huvudfunktion för att populera databasen."""
    print("=" * 60)
    print("POPULERAR KNOWLEDGE - JUSTERINGSPOSTER")
    print("=" * 60)

    success_count = 0
    error_count = 0

    for i, item in enumerate(KNOWLEDGE_ITEMS, 1):
        print(f"\n[{i}/{len(KNOWLEDGE_ITEMS)}] {item['title'][:50]}...")
        result = add_knowledge(
            domain=item["domain"],
            category=item["category"],
            title=item["title"],
            content=item["content"],
            tags=item.get("tags"),
            related_metrics=item.get("related_metrics"),
            source=item.get("source")
        )

        if result.get("success"):
            print(f"  ✓ Tillagd (ID: {result['id'][:8]}...)")
            success_count += 1
        else:
            print(f"  ✗ Fel: {result.get('error')}")
            error_count += 1

    print("\n" + "=" * 60)
    print(f"KLART! Lyckade: {success_count}, Fel: {error_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
