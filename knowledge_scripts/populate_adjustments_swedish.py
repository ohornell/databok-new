#!/usr/bin/env python3
"""
Script för att populera knowledge-databasen med kunskap om justeringsposter.
Fokuserat på svenska bolag och svensk redovisningsstandard (K3/IFRS).
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
# KUNSKAPSPOSTER - SVENSKA JUSTERINGSPOSTER
# =============================================================================

KNOWLEDGE_ITEMS = [
    # -----------------------------------------
    # GRUNDLÄGGANDE - JUSTERAT RESULTAT
    # -----------------------------------------
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Justerat EBITDA: Definition och syfte",
        "content": """**Justerat EBITDA** är ett icke-officiellt resultatmått som svenska bolag använder
för att visa "underliggande" lönsamhet.

**Rapporterat EBITDA:**
EBITDA = Rörelseresultat + Avskrivningar + Nedskrivningar

**Justerat EBITDA:**
Justerat EBITDA = EBITDA + Jämförelsestörande poster

**Varför svenska bolag justerar:**
1. Visa lönsamhet exklusive engångshändelser
2. Underlätta jämförelse mellan perioder
3. Matcha hur ledningen styr verksamheten

**Notera:**
- Ingen standard för vad som får justeras
- Varje bolag definierar själv
- ESMA (EU) har vägledning men det är inte bindande för svenska bolag
- Nasdaq Stockholm kräver avstämning mot närmaste officiella mått

**Tumregel:**
Jämför alltid justerat EBITDA med kassaflöde från rörelsen. Om de divergerar
kraftigt finns anledning att granska justeringarna kritiskt.""",
        "tags": ["justerat EBITDA", "jämförelsestörande", "underliggande", "ESMA"],
        "related_metrics": ["EBITDA", "Justerat EBITDA", "Rörelseresultat"],
        "source": "ESMA/Nasdaq"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "EBITA för svenska serieförvärvare",
        "content": """**EBITA** är standardmåttet för svenska serieförvärvare som Lifco, Indutrade,
Addtech, Lagercrantz och Storskogen.

**Definition:**
EBITA = EBIT + Avskrivningar på förvärvade immateriella tillgångar

**Varför EBITA:**
Vid förvärv enligt IFRS 3 identifieras immateriella tillgångar (kundrelationer,
varumärken, teknologi) som sedan skrivs av. Dessa avskrivningar:
- Är icke-kassaflödespåverkande
- Varierar kraftigt beroende på förvärvshistorik
- Gör EBIT-jämförelser mellan bolag missvisande

**Svenska serieförvärvare - typiska PPA-avskrivningar:**
- Kundrelationer: 5-15 års avskrivning
- Varumärken: Obestämd livslängd (ingen avskrivning) eller 10-20 år
- Teknologi: 3-10 års avskrivning

**EBITA-marginal benchmark för svenska serieförvärvare:**
- Lifco: ~18-20%
- Indutrade: ~14-16%
- Addtech: ~12-14%

**Analytisk kommentar:**
EBITA är ett legitimt mått för serieförvärvare. Men kontrollera att
förvärvsrelaterade transaktionskostnader INTE justeras bort - dessa är
en löpande kostnad för bolag vars affärsmodell är att förvärva.""",
        "tags": ["EBITA", "serieförvärvare", "PPA", "Lifco", "Indutrade", "Addtech"],
        "related_metrics": ["EBITA", "EBIT", "PPA-avskrivningar"],
        "source": "Intern/CFA"
    },

    # -----------------------------------------
    # VANLIGA JÄMFÖRELSESTÖRANDE POSTER
    # -----------------------------------------
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Jämförelsestörande poster: Svenska definitioner",
        "content": """**Jämförelsestörande poster** är den svenska termen för poster som bolag
justerar bort för att visa underliggande resultat.

**Vanliga jämförelsestörande poster i svenska bolag:**

1. **Omstruktureringskostnader**
   - Personalavveckling, nedläggning av verksamheter
   - Ska vara tydligt avgränsade program

2. **Förvärvsrelaterade kostnader**
   - Due diligence, transaktionsrådgivning
   - Integration av förvärvade bolag

3. **Nedskrivningar**
   - Goodwill, immateriella tillgångar
   - Fastigheter och anläggningar

4. **Realisationsresultat**
   - Försäljning av dotterbolag/verksamheter
   - Fastighetsförsäljningar

5. **Avsättningar av engångskaraktär**
   - Rättsliga tvister
   - Miljöåtaganden

**ESMA:s vägledning (gäller inom EU):**
- Ska vara tydligt definierade
- Konsistent tillämpning över tid
- Avstämning mot officiellt mått
- Får inte ges större prominence än officiella mått""",
        "tags": ["jämförelsestörande", "engångsposter", "ESMA", "APM"],
        "related_metrics": ["EBITDA", "Rörelseresultat"],
        "source": "ESMA APM Guidelines"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Omstruktureringskostnader i svenska bolag",
        "content": """**Omstruktureringskostnader** redovisas enligt IAS 37 (IFRS) eller K3 kap 21.

**Vad krävs för att redovisa avsättning:**
1. Formellt beslut om omstrukturering
2. Detaljerad plan kommunicerad till berörda
3. Förväntad tidsram för genomförande

**Typiska kostnader:**
- Avgångsvederlag enligt LAS/kollektivavtal
- Uppsägningslöner under uppsägningstid
- Konsultkostnader för omställning
- Nedskrivning av tillgångar

**Legitimt att justera om:**
- Tydligt avgränsat program med start/slut
- Inte återkommande varje år
- Väsentligt belopp

**Varningssignaler:**
- "Omstrukturering" varje år = löpande kostnad
- Omstruktureringsprogram som "förlängs"
- Kostnader som inte leder till förbättrat resultat

**Svenska exempel:**
Ericssons omstruktureringsprogram 2017-2020 kostade ~30 Mdr SEK.
Varje kvartal hade "omstruktureringskostnader" - var det verkligen engångs?""",
        "tags": ["omstrukturering", "IAS 37", "avgångsvederlag", "nedskrivning"],
        "related_metrics": ["Rörelseresultat", "Kassaflöde", "Avsättningar"],
        "source": "IAS 37/K3"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Förvärvsrelaterade kostnader - vad får justeras?",
        "content": """**Förvärvsrelaterade kostnader** ska enligt IFRS 3 kostnadsföras direkt.

**Transaktionskostnader (typiskt engångs):**
- Due diligence (juridisk, finansiell, teknisk)
- M&A-rådgivning och mäklararvoden
- Stämpelskatt och registreringsavgifter
- Typiskt 1-3% av transaktionsvärdet

**Integrationskostnader (diskutabelt):**
- IT-systemintegration
- Varumärkesintegration
- Organisatorisk sammanslagning
- Typiskt 3-10% av transaktionsvärdet

**Svenska serieförvärvares praxis:**
- Lifco: Justerar EJ för förvärvsrelaterade kostnader
- Indutrade: Justerar EJ för förvärvsrelaterade kostnader
- Addtech: Redovisar separat, marginellt belopp

**Analytisk bedömning:**
Om bolaget gör 5+ förvärv per år är förvärvsrelaterade kostnader
en LÖPANDE kostnad, inte engångs. Acceptera eventuellt justering
för enstaka stora transformerande förvärv, men inte för "bolt-on".""",
        "tags": ["förvärv", "M&A", "transaktionskostnader", "IFRS 3", "integration"],
        "related_metrics": ["EBITA", "Förvärvskostnad", "Goodwill"],
        "source": "IFRS 3"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Nedskrivningar som jämförelsestörande post",
        "content": """**Nedskrivningar** justeras ofta bort i svenska bolag.

**Goodwillnedskrivning (IFRS):**
- Årlig nedskrivningsprövning krävs
- Signalerar att förvärv inte levererat förväntat värde
- Icke-kassaflödespåverkande
- Ofta legitimt att justera för underliggande analys

**Goodwillavskrivning (K3):**
- Max 10 års avskrivning
- Löpande kostnad, inte jämförelsestörande
- Bör INTE justeras bort

**Nedskrivning av materiella anläggningstillgångar:**
- Kan signalera strukturella problem
- Granska orsaken - är marknaden permanent försämrad?

**Nedskrivning av varulager:**
- Normalt INTE jämförelsestörande
- Del av löpande verksamhet

**Svenska exempel:**
- H&M nedskrivning av butiksrätter 2018-2020
- Telia nedskrivning av Eurasien-verksamhet 2013
- SEB nedskrivning av goodwill i Baltikum 2008

**Tumregel:**
Stora engångsnedskrivningar kan justeras.
Återkommande mindre nedskrivningar är en löpande kostnad.""",
        "tags": ["nedskrivning", "goodwill", "impairment", "IAS 36"],
        "related_metrics": ["Goodwill", "EBIT", "Tillgångar"],
        "source": "IAS 36/K3"
    },

    # -----------------------------------------
    # KASSAFLÖDESANALYS OCH KVALITET
    # -----------------------------------------
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Kassaflöde som kvalitetstest för justeringar",
        "content": """**Kassaflödet avslöjar kvaliteten** på justerade resultatmått.

**Grundprincip:**
Om justerat EBITDA är "rättvisande" bör operativt kassaflöde följa samma mönster.

**Kassakonvertering:**
Cash Conversion = Kassaflöde från rörelsen / Justerat EBITDA

**Svenska benchmark:**
- Industribolag: 80-100%
- Serieförvärvare: 90-110%
- Fastighetsbolag: 60-80% (pga förvaltningskostnader)
- SaaS/Tech: 70-90%

**Varningssignaler:**
1. Justerat EBITDA växer men kassaflöde stagnerar
2. Kassakonvertering < 70% över flera år
3. Rörelsekapitalet växer snabbare än omsättningen

**Analysmetod:**
1. Summera justeringar över 3-5 år
2. Summera kassaflöde från rörelsen över samma period
3. Jämför trenderna - de bör följa varandra

**Exempel:**
Bolag X visar 15% EBITDA-tillväxt justerat men kassaflödet är oförändrat.
→ Justeringarna döljer troligen löpande kostnader""",
        "tags": ["kassaflöde", "kassakonvertering", "kvalitet", "rörelsekapital"],
        "related_metrics": ["Kassaflöde från rörelsen", "Justerat EBITDA", "Kassakonvertering"],
        "source": "Intern/CFA"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "EBITDA-brygga: Läs den kritiskt",
        "content": """**EBITDA-bryggan** visar hur bolaget går från rapporterat till justerat resultat.

**Typisk presentation i svenska kvartalsrapporter:**

```
Rörelseresultat                            100 MSEK
+ Avskrivningar materiella                  20
+ Avskrivningar immateriella (PPA)          15
= EBITDA                                   135 MSEK

Jämförelsestörande poster:
+ Omstruktureringskostnader                 25
+ Förvärvsrelaterade kostnader               5
+ Nedskrivning goodwill                     40
= Justerat EBITDA                          205 MSEK
```

**Analysera proportionerna:**
- Justeringar = 70 MSEK på 135 MSEK = 52% (HÖGT!)
- Om justeringar > 20% av EBITDA → granska kritiskt

**Frågor att ställa:**
1. Är omstruktureringen verkligen engångs?
2. Hur många förvärv gör bolaget per år?
3. Varför nedskrivning av goodwill - vad gick fel?

**Skapa egen "sanerad" brygga:**
Ta bolagets justerade EBITDA och lägg tillbaka poster som är återkommande:
- Om omstrukturering skett 3 år i rad → lägg tillbaka 1/3 per år
- Om bolaget gör regelbundna förvärv → lägg tillbaka förvärvsrelaterade kostnader""",
        "tags": ["brygga", "EBITDA", "justering", "reconciliation"],
        "related_metrics": ["EBITDA", "Justerat EBITDA", "Rörelseresultat"],
        "source": "Intern"
    },

    # -----------------------------------------
    # RÖDA FLAGGOR
    # -----------------------------------------
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Röda flaggor: Återkommande engångsposter",
        "content": """**Återkommande "engångsposter"** är den viktigaste röda flaggan.

**Definition:**
Om en post är "engångs" ska den inte upprepas. Om samma typ av kostnad
uppstår varje år är det en LÖPANDE kostnad.

**Så analyserar du (3-5 års historik):**
1. Lista alla jämförelsestörande poster
2. Kategorisera: Omstrukturering, förvärv, nedskrivning, övrigt
3. Räkna hur ofta varje kategori förekommer

**Röd flagga-tabell:**
| Post | Röd flagga om |
|------|---------------|
| Omstrukturering | > 2 år i följd |
| Förvärvsrelaterat | Varje kvartal (serieförvärvare) |
| Nedskrivning goodwill | > 1 gång på 3 år |
| "Övriga poster" | Ospecificerat belopp |

**Svenska exempel:**
- Ericsson: Omstruktureringskostnader 10+ år i rad
- SAS: "Engångseffekter" varje kvartal under krisen
- Telia: Nedskrivningar i Eurasien flera år

**Beräkna normaliserat EBITDA:**
Genomsnittlig "engångskostnad" över 5 år = Löpande kostnad att dra av""",
        "tags": ["röda flaggor", "engångsposter", "återkommande", "normalisering"],
        "related_metrics": ["Justerat EBITDA", "EBITDA"],
        "source": "Intern/CFA"
    },
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Röda flaggor: Ökande justeringsbelopp",
        "content": """**Ökande justeringar** relativt EBITDA är en allvarlig varningssignal.

**Analysera trenden:**
| År | EBITDA | Justeringar | % av EBITDA |
|----|--------|-------------|-------------|
| 2021 | 100 | 10 | 10% |
| 2022 | 110 | 18 | 16% |
| 2023 | 115 | 28 | 24% |
| 2024 | 120 | 40 | 33% |

→ Justeringarna växer snabbare än EBITDA = stor röd flagga

**Vad det kan betyda:**
1. Operativa problem döljs som "engångs"
2. Aggressiv redovisning
3. Affärsmodellen genererar strukturella kostnader

**Krav på transparens (ESMA):**
- Exakt specifikation av vad justeringen avser
- Varför den är av engångskaraktär
- Förväntad kassaflödeseffekt

**Tumregler:**
- Justeringar < 10% av EBITDA: Normalt
- Justeringar 10-20%: Granska noga
- Justeringar > 20%: Troligen överdrivna justeringar
- Justeringar > 30%: Ifrågasätt hela det justerade måttet""",
        "tags": ["röda flaggor", "trend", "ökande", "transparens", "ESMA"],
        "related_metrics": ["Justerat EBITDA", "EBITDA"],
        "source": "ESMA/Intern"
    },
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "Checklista: Analys av justeringsposter",
        "content": """**Systematisk genomgång av justeringsposter:**

**STEG 1: Datainsamling (3-5 år)**
□ Lista alla jämförelsestörande poster per period
□ Belopp och beskrivning
□ Total justering som % av EBITDA

**STEG 2: Kategorisera**
□ Omstrukturering
□ Förvärvsrelaterat
□ Nedskrivningar
□ Realisationsresultat
□ Övriga/oklara

**STEG 3: Mönsteranalys**
□ Vilka poster återkommer? (>2 år = löpande)
□ Ökar eller minskar justeringarna?
□ Finns ospecificerade "övriga" poster?

**STEG 4: Kassaflödestest**
□ Kassakonvertering (OCF/Justerat EBITDA)
□ Trend i kassaflöde vs justerat EBITDA
□ Rörelsekapitalutveckling

**STEG 5: Peer-jämförelse**
□ Använder konkurrenter liknande justeringar?
□ Relativ storlek på justeringar

**STEG 6: Eget normaliserat mått**
□ Lägg tillbaka återkommande "engångsposter"
□ Inkludera förvärvsrelaterade kostnader för serieförvärvare
□ Dokumentera dina justeringar""",
        "tags": ["checklista", "analysmetodik", "justeringar"],
        "related_metrics": ["Justerat EBITDA", "Kassaflöde"],
        "source": "Intern"
    },

    # -----------------------------------------
    # BRANSCHSPECIFIKT - SVENSKA BOLAG
    # -----------------------------------------
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Svenska fastighetsbolag: Förvaltningsresultat",
        "content": """**Förvaltningsresultat** är det centrala lönsamhetsmåttet för svenska fastighetsbolag.

**Definition (enligt EPRA och svensk praxis):**
Förvaltningsresultat = Driftsnetto - Central administration - Finansnetto

**Vad som INTE ingår (justeras bort):**
- Orealiserade värdeförändringar på fastigheter
- Orealiserade värdeförändringar på derivat
- Realiserade försäljningsresultat

**Varför detta mått:**
IFRS kräver värdering till verkligt värde (IAS 40). Stora svängningar i
marknadsvärden skulle göra resultaträkningen missvisande för den
underliggande kassagenereringen.

**Svenska fastighetsbolag - praxis:**
- Castellum, Fabege, Wihlborgs: Förvaltningsresultat som huvudmått
- SBB: Förvaltningsresultat + "driftsöverskottsbaserat kassaflöde"
- Balder: Förvaltningsresultat per aktie

**EPRA-nyckeltal (European Public Real Estate Association):**
- EPRA Earnings: Standardiserad definition av förvaltningsresultat
- EPRA NAV: Substansvärde justerat för latent skatt
- EPRA NRV: NAV exkl. vissa justeringar

**Röda flaggor:**
- "Justerat förvaltningsresultat" - dubbeljustering?
- Stora skillnader mellan bolagets mått och EPRA""",
        "tags": ["fastigheter", "förvaltningsresultat", "EPRA", "IAS 40", "NAV"],
        "related_metrics": ["Förvaltningsresultat", "Driftsnetto", "NAV"],
        "source": "EPRA/FAR"
    },
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Svenska banker: K/I-tal och justeringar",
        "content": """**Svenska banker** använder specifika lönsamhetsmått.

**K/I-tal (Kostnads/Intäktskvot):**
K/I = Kostnader / Intäkter före kreditförluster

**Jämförelsestörande poster i svenska banker:**
1. Omstruktureringskostnader (IT-investeringar, filialstängningar)
2. Integrationskostnader vid förvärv
3. Stora kreditförluster (av "engångskaraktär")
4. Regulatoriska böter och sanktioner

**Svenska storbankernas praxis:**
- SEB: Rapporterar "underliggande" K/I-tal
- Handelsbanken: Justerar minimalt
- Swedbank: Justerat rörelseresultat (exkl. engångsposter)
- Nordea: Justerat resultat och K/I-tal

**Kritiskt att granska:**
- Vad räknas som "engångs" kreditförlust?
- IT-investeringar som "transformation" varje år?
- Regulatoriska böter - är de verkligen engångs?

**Tumregel:**
Om banken har "engångsrelaterade" IT-kostnader varje kvartal i 5+ år
är det en LÖPANDE kostnad för att hänga med i den digitala utvecklingen.""",
        "tags": ["banker", "K/I-tal", "kreditförluster", "SEB", "Handelsbanken"],
        "related_metrics": ["K/I-tal", "Rörelseresultat", "Kreditförluster"],
        "source": "Intern/FI"
    },
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Svenska industribolag: Cykliska justeringar",
        "content": """**Svenska industribolag** har ofta cykliska mönster som påverkar justeringar.

**Typiska jämförelsestörande poster:**

1. **Omstrukturering vid nedgång:**
   - Kapacitetsanpassning
   - Personalneddragningar
   - Fabriksstängningar

2. **Garantiavsättningar:**
   - Kan svänga kraftigt
   - Produktansvar

3. **Lagernedskrivningar:**
   - Vanligt vid konjunkturnedgång
   - Ofta delvis reverseras

**Cyklisk analys - normalisering:**
Istället för enskilt år, titta på:
- Genomsnittsmarginal över 5-7 år
- Peak-to-peak (topp till topp)
- Genom-cykeln-EBITDA

**Svenska exempel:**
- Volvo AB: Stora omstruktureringar 2009, 2020
- SKF: Kapacitetsanpassningar vid nedgång
- Sandvik: Omstruktureringsprogram 2013-2016

**Fråga dig:**
Om bolaget har omstruktureringskostnader i varje lågkonjunktur
→ Det är en NORMAL del av att vara ett cykliskt industribolag
→ Ska inte justeras bort som "engångs" om det sker varje cykel""",
        "tags": ["industri", "cyklisk", "omstrukturering", "Volvo", "SKF"],
        "related_metrics": ["EBITDA", "Marginal", "Kassaflöde"],
        "source": "Intern"
    },
    {
        "domain": "bransch",
        "category": "justeringar",
        "title": "Svenska investmentbolag: Substansvärde och justeringar",
        "content": """**Svenska investmentbolag** värderas på substansvärde, inte resultat.

**Substansvärde (NAV):**
NAV = Marknadsvärde på portföljbolagen - Nettoskuld

**Komponenter:**
- Noterade innehav: Börskurs × antal aktier
- Onoterade innehav: Intern värdering (kräver granskning)
- Nettoskuld: Räntebärande skulder - likvida medel

**Svenska investmentbolag:**
- Investor, Industrivärden, Kinnevik, Latour, Lundbergs

**Substansrabatt/premie:**
Substansrabatt = (NAV - Börsvärde) / NAV

Typiska nivåer:
- Investor: 0-10% premie (hög kvalitet)
- Industrivärden: 5-15% rabatt
- Kinnevik: Varierar kraftigt (onoterad andel)

**Kritiska justeringar att granska:**
1. Värdering av onoterade innehav
   - Vilken metod? DCF, multiplar, senaste transaktion?
   - Vem gör värderingen?

2. Latent skatt på övervärden
   - Normalt 20.6% på orealiserade vinster
   - Men skatten realiseras sällan - justera ned?

3. Holdingkostnader
   - Dras normalt av från NAV

**Röda flaggor:**
- Aggressiv uppvärdering av onoterade utan transaktionsstöd
- Stora skillnader mellan intern värdering och analys""",
        "tags": ["investmentbolag", "NAV", "substansvärde", "Investor", "Kinnevik"],
        "related_metrics": ["NAV", "Substansrabatt", "Totalavkastning"],
        "source": "Intern"
    },

    # -----------------------------------------
    # REGULATORISKT RAMVERK
    # -----------------------------------------
    {
        "domain": "kvalitativ",
        "category": "justeringar",
        "title": "ESMA:s riktlinjer för alternativa nyckeltal (APM)",
        "content": """**ESMA (Europeiska värdepappers- och marknadsmyndigheten)** har utfärdat
riktlinjer för alternativa nyckeltal som gäller svenska noterade bolag.

**Krav enligt ESMA APM Guidelines (2015):**

1. **Definition och begriplighet:**
   - Tydlig definition av måttet
   - Meningsfull beteckning

2. **Avstämning:**
   - Avstämning mot närmaste IFRS-mått
   - Förklaring av avstämningsposter

3. **Förklaringar:**
   - Varför måttet ger användbar information
   - Varför justeringar görs

4. **Jämförelser:**
   - Jämförelsetal för tidigare perioder
   - Konsistent beräkning

5. **Prominence:**
   - Officiella IFRS-mått ska ha minst lika framträdande plats

**Praktisk tillämpning i Sverige:**
- Nasdaq Stockholm övervakar efterlevnad
- FI kan ingripa vid missvisande information
- Revision granskar normalt APM-sektioner

**Vanliga brister:**
- Otydliga definitioner
- Bristfällig avstämning
- Ändrade definitioner mellan perioder
- Alternativa mått mer framträdande än officiella""",
        "tags": ["ESMA", "APM", "reglering", "Nasdaq", "IFRS"],
        "related_metrics": ["Justerat EBITDA", "Alternativa nyckeltal"],
        "source": "ESMA APM Guidelines"
    },
    {
        "domain": "nyckeltal",
        "category": "justeringar",
        "title": "Justeringar i värdering: EV/EBITDA",
        "content": """**Vid värdering** måste justeringar hanteras konsekvent.

**Grundprincip:**
Om du justerar EBITDA, justera även Enterprise Value för relaterade poster.

**Beräkning Enterprise Value:**
EV = Börsvärde + Nettoskuld + Minoritet + Pensionsskuld + Leasingskulder

**Vanliga misstag:**

1. **Inkonsekvent justering:**
   Använder justerat EBITDA (högt) men missar skulder
   → Ger för låg EV/EBITDA-multipel

2. **Engångsposter i prognoser:**
   Justerar historiskt EBITDA för engångsposter
   Men prognostiserar framtiden UTAN dessa kostnader
   → Dubbelräkning om de egentligen är återkommande

3. **Peer-jämförelse:**
   Jämför bolagets justerade EBITDA med peers rapporterade
   → Äpplen och päron

**Best practice:**
1. Beräkna EV/EBITDA på BÅDE rapporterat och justerat
2. Om stor skillnad → analysera justeringarna
3. Gör egen normalisering
4. Säkerställ att peers behandlas likadant

**Svenska serieförvärvare:**
Använd EV/EBITA (inte EV/EBITDA) för Lifco, Indutrade etc.
PPA-avskrivningar är så stora att EBITDA blir missvisande.""",
        "tags": ["EV/EBITDA", "EV/EBITA", "värdering", "multipel"],
        "related_metrics": ["EV/EBITDA", "EV/EBITA", "Enterprise Value"],
        "source": "Intern/CFA"
    },
]


def main():
    """Huvudfunktion för att populera databasen."""
    print("=" * 60)
    print("POPULERAR KNOWLEDGE - SVENSKA JUSTERINGSPOSTER")
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
