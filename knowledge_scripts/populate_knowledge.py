#!/usr/bin/env python3
"""
Script för att populera knowledge-databasen med IFRS/K3 kunskap.
Körs en gång för att fylla på databasen.
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
# KUNSKAPSPOSTER ATT LÄGGA TILL
# =============================================================================

KNOWLEDGE_ITEMS = [
    # -----------------------------------------
    # IFRS 15 - INTÄKTSREDOVISNING (detaljerad)
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 15 Steg 1-2: Kontrakt och prestationsåtaganden",
        "content": """IFRS 15 kräver att man först identifierar kontraktet (Steg 1) - det måste ha kommersiell substans,
vara godkänt av parterna, ha identifierbara rättigheter/skyldigheter och betalningsvillkor.

Steg 2 är att identifiera separata prestationsåtaganden. Ett åtagande är separat (distinct) om:
1. Kunden kan dra nytta av varan/tjänsten ensam eller med andra lättillgängliga resurser
2. Åtagandet är separat identifierbart från andra åtaganden i kontraktet

Exempel: Mjukvarulicens + implementation = ofta separata. Skräddarsydd mjukvara + implementation = ofta kombinerade.

Praktisk tillämpning: Licensintäkt med obligatorisk konsulttjänst - om konsulttjänsten krävs för att licensen ska fungera är de EJ separata.""",
        "tags": ["IFRS 15", "intäkter", "prestationsåtaganden", "distinct", "kontrakt"],
        "related_metrics": ["Intäkter", "Uppskjutna intäkter"],
        "source": "IFRS 15.9-30"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 15 Steg 3: Bestämma transaktionspriset",
        "content": """Transaktionspriset är det belopp företaget förväntar sig ha rätt till. Inkluderar:

**Variabel ersättning**: Bonus, rabatter, återbetalningar, priskoncessioner, incitament, royalties.
Ska estimeras med antingen "Expected value" (sannolikhetsviktat) eller "Most likely amount".

**Constraint**: Variabel ersättning inkluderas ENDAST i den utsträckning det är "highly probable" att ingen
signifikant reversering sker. Faktorer att beakta:
- Extern påverkan (marknad, väder, kundbeteende)
- Lång tid till osäkerheten löses
- Begränsad erfarenhet av liknande kontrakt
- Praxis att erbjuda priskoncessioner

**Signifikant finansieringskomponent**: Om betalning sker >12 månader före/efter leverans - justera för ränta.

**Icke-kontant ersättning**: Värderas till verkligt värde.""",
        "tags": ["IFRS 15", "transaktionspris", "variabel ersättning", "constraint"],
        "related_metrics": ["Intäkter", "Kundfordringar"],
        "source": "IFRS 15.46-72"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 15 Steg 4-5: Allokering och intäktsredovisning",
        "content": """**Steg 4 - Allokera transaktionspriset:**
Fördela baserat på fristående försäljningspriser (standalone selling prices). Metoder:
1. Justerat marknadspris - vad kunder på marknaden skulle betala
2. Expected cost plus margin - kostnad + rimlig marginal
3. Residualmetod - endast om priset är mycket variabelt/osäkert

Rabatter allokeras proportionellt om inget annat indikerar specifik koppling.

**Steg 5 - Redovisa intäkt vid uppfyllelse:**
Två sätt att uppfylla prestationsåtaganden:

ÖVER TID om något av följande:
- Kunden samtidigt erhåller och konsumerar nyttan (städtjänst)
- Företagets prestation skapar/förbättrar en tillgång kunden kontrollerar
- Tillgången har ingen alternativ användning OCH företaget har rätt till betalning för utfört arbete

I ÖVRIGT: Vid en tidpunkt - när kontrollen övergår (leverans, godkännande, etc.)

Progress measurement för över-tid: Output (milstolpar, enheter) eller Input (kostnader, timmar).""",
        "tags": ["IFRS 15", "allokering", "over time", "point in time", "kontroll"],
        "related_metrics": ["Intäkter", "Pågående arbeten", "Uppskjutna intäkter"],
        "source": "IFRS 15.73-90"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 15 Principal vs Agent",
        "content": """Ett företag är PRINCIPAL om det kontrollerar varan/tjänsten innan den överförs till kunden.
Ett företag är AGENT om det endast arrangerar att en annan part tillhandahåller varan/tjänsten.

**Principal** = Redovisar intäkt brutto (hela beloppet)
**Agent** = Redovisar intäkt netto (provision/avgift)

Indikatorer på PRINCIPAL-status:
- Primärt ansvar för att uppfylla kontraktet
- Lagerrisk (innan/efter överföring, vid retur)
- Prissättningsfrihet

Exempel - Resebyrå:
- Om resebyrån köper hotellrum och säljer vidare med egen prissättning = Principal
- Om resebyrån bara förmedlar bokning och får provision = Agent

Exempel - E-handel:
- Amazon säljer egna produkter = Principal (brutto)
- Amazon marketplace där tredjepartssäljare säljer = Agent (netto, bara avgift)

OBSERVERA: Juridisk titel är INTE avgörande - det handlar om ekonomisk substans och kontroll.""",
        "tags": ["IFRS 15", "principal", "agent", "brutto", "netto", "kontroll"],
        "related_metrics": ["Intäkter", "Bruttomarginal"],
        "source": "IFRS 15.B34-B38"
    },

    # -----------------------------------------
    # K3 KAPITEL 23 - INTÄKTER
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "K3 Kapitel 23: Intäktsredovisning - Grundprinciper",
        "content": """K3 kapitel 23 baseras på äldre IAS 18 och är betydligt enklare än IFRS 15.

**Grundprincip**: Intäkt redovisas när:
1. Väsentliga risker och förmåner har överförts
2. Säljaren behåller inte kontroll eller engagemang
3. Inkomsten kan mätas tillförlitligt
4. Ekonomiska fördelar sannolikt tillfaller företaget
5. Utgifterna kan mätas tillförlitligt

**Varuförsäljning**: Normalt vid leverans (risken övergår)

**Tjänsteuppdrag**: Två metoder:
- Färdigställandemetoden (huvudregeln) - redovisa successivt baserat på färdigställandegrad
- Alternativregeln - redovisa först när uppdraget är slutfört (används om utfall ej kan bedömas)

**Räntor, royalties, utdelning**:
- Ränta: Effektivräntemetoden
- Royalty: Enligt avtalets ekonomiska innebörd
- Utdelning: När rätt till utdelning fastställs""",
        "tags": ["K3", "intäkter", "kapitel 23", "varuförsäljning", "tjänsteuppdrag"],
        "related_metrics": ["Intäkter", "Uppskjutna intäkter"],
        "source": "K3 Kapitel 23"
    },
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "K3 Alternativregeln för tjänsteuppdrag",
        "content": """K3 23.22-23 tillåter alternativregeln för tjänsteuppdrag till fast pris:

**Huvudregeln (successiv vinstavräkning)**:
- Intäkt redovisas i takt med färdigställandegrad
- Kräver att utfallet kan beräknas tillförlitligt
- Färdigställandegrad baseras på nedlagda kostnader vs totala kostnader

**Alternativregeln**:
- Intäkt = nedlagda kostnader (ingen vinst förrän slutfört)
- Används när utfallet INTE kan bedömas tillförlitligt
- Vinsten redovisas först när uppdraget är helt klart

**Skillnad mot IFRS 15**:
K3 fokuserar på "risker och förmåner" medan IFRS 15 fokuserar på "kontrollövergång".
K3 har inga explicita regler för separata prestationsåtaganden eller allokering av transaktionspris.

**Praktisk konsekvens**:
Entreprenader och konsultuppdrag kan redovisas med olika metoder - var uppmärksam på
konsistens och byte av metod (kräver not-upplysning).""",
        "tags": ["K3", "alternativregeln", "successiv vinstavräkning", "tjänsteuppdrag", "entreprenad"],
        "related_metrics": ["Intäkter", "Pågående arbeten"],
        "source": "K3 Kapitel 23.22-23"
    },

    # -----------------------------------------
    # IFRS 15 vs K3 SKILLNADER
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 15 vs K3: Praktiska skillnader",
        "content": """**Nyckelskillnader mellan IFRS 15 och K3 kapitel 23:**

| Område | IFRS 15 | K3 |
|--------|---------|-----|
| Grundprincip | Kontrollövergång | Risker & förmåner |
| Separata åtaganden | Explicit 5-stegsmodell | Ingen explicit vägledning |
| Allokering | Standalone selling price | Ingen systematisk allokering |
| Variabel ersättning | Constraint-test | Försiktighetsprincipen |
| Upplysningar | Omfattande krav | Begränsade krav |

**Konsekvenser vid jämförelse:**
1. Timing kan skilja - IFRS 15 kan ge tidigare/senare intäkt beroende på kontraktsstruktur
2. Mjukvaruföretag: IFRS 15 separerar ofta licens från support = ändrad timing
3. Entreprenad: Båda tillåter successiv vinstavräkning men med olika kriterier
4. Bundled deals: IFRS 15 kräver allokering, K3 är mer flexibelt

**Vid analys - fråga:**
- Vilka redovisningsprinciper tillämpas?
- Har bolaget bytt från K3 till IFRS (eller vice versa)?
- Finns väsentliga paketerade erbjudanden?""",
        "tags": ["IFRS 15", "K3", "jämförelse", "skillnader", "intäkter"],
        "related_metrics": ["Intäkter", "Uppskjutna intäkter"],
        "source": "Intern analys"
    },

    # -----------------------------------------
    # IFRS 16 - LEASING (detaljerad)
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 16 Leasing: Grundläggande mekanik",
        "content": """IFRS 16 kräver att leasetagare redovisar i princip alla leasingavtal i balansräkningen.

**Initialt (dag 1):**
- Nyttjanderättstillgång (ROU) = Leasingskuld + initiala direkta kostnader + förskottsbetalningar - incitament
- Leasingskuld = Nuvärdet av framtida leasingavgifter

**Efterföljande perioder:**
- ROU-tillgång: Avskrivning (normalt linjärt över leasingperioden)
- Leasingskuld: Amortering + Ränta (effektivräntemetod)

**Resultateffekt:**
- Avskrivning (EBITDA-neutral, påverkar EBIT)
- Räntekostnad (under finansnetto)
- Total kostnad = Avskrivning + Ränta (front-loaded, högre i början)

**Kassaflödeseffekt:**
- Operativt kassaflöde: FÖRBÄTTRAS (leasingavgiften är borta)
- Finansieringsflöde: FÖRSÄMRAS (amortering redovisas här)
- Ränta: Kan vara operativt eller finansiering (val)

**Undantag:**
- Kortfristiga leasingavtal (<12 månader)
- Lågvärdetillgångar (<5000 USD, t.ex. laptops)""",
        "tags": ["IFRS 16", "leasing", "ROU", "nyttjanderätt", "leasingskuld"],
        "related_metrics": ["EBITDA", "EBIT", "Skuldsättningsgrad", "Räntekostnad"],
        "source": "IFRS 16"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 16 Numeriskt exempel",
        "content": """**Exempel: 5-årigt hyresavtal, 100 MSEK/år, diskonteringsränta 5%**

Nuvärde av leasingavgifter = 100 × [(1-(1.05)^-5)/0.05] = 432.9 MSEK

**År 1:**
- Ingående leasingskuld: 432.9
- Ränta (5%): 21.6
- Betalning: 100.0
- Amortering: 78.4
- Utgående skuld: 354.5

**År 2:**
- Ingående: 354.5
- Ränta: 17.7
- Betalning: 100.0
- Amortering: 82.3
- Utgående: 272.2

**Amorteringsschema komplett:**
| År | IB Skuld | Ränta | Betalning | Amortering | UB Skuld |
|----|----------|-------|-----------|------------|----------|
| 1  | 432.9    | 21.6  | 100.0     | 78.4       | 354.5    |
| 2  | 354.5    | 17.7  | 100.0     | 82.3       | 272.2    |
| 3  | 272.2    | 13.6  | 100.0     | 86.4       | 185.8    |
| 4  | 185.8    | 9.3   | 100.0     | 90.7       | 95.1     |
| 5  | 95.1     | 4.9   | 100.0     | 95.1       | 0        |

Total räntekostnad: 67.1 MSEK (spridd över 5 år, avtagande)""",
        "tags": ["IFRS 16", "leasing", "beräkning", "amortering", "exempel"],
        "related_metrics": ["Räntekostnad", "Leasingskuld", "Skuldsättningsgrad"],
        "source": "Intern beräkning"
    },
    {
        "domain": "nyckeltal",
        "category": "IFRS",
        "title": "IFRS 16 Analytikerjusteringar",
        "content": """För att göra bolag jämförbara oavsett om de äger eller leasar behöver analytiker justera nyckeltal.

**EBITDA-justering:**
Före IFRS 16: Leasingavgift belastar EBITDA
Efter IFRS 16: Leasingavgift borta från EBITDA → Högre EBITDA

Justering: EBITDA (justerad) = EBITDA - Avskrivning ROU-tillgångar
ELLER: Lägg tillbaka hela leasingkostnaden för att jämföra med gamla siffror

**EV/EBITDA-justering:**
EV ska inkludera leasingskulder för att vara konsistent:
EV = Börsvärde + Räntebärande skulder + Leasingskulder - Kassa

**Skuldsättning:**
Nettoskuld inkl. leasing = Finansiella skulder + Leasingskulder - Kassa
Ofta rapporteras båda måtten (med/utan leasing)

**EBIT vs EBITDA:**
- EBITDA påverkas mest (förbättras)
- EBIT påverkas mindre (avskrivning istället för leasingkostnad)
- Nettoresultat: Marginell påverkan (omfördelning mellan kostnadsslag)

**Röda flaggor:**
- Mycket stora leasingskulder → Dold skuldsättning
- Kort återstående leasingtid → Förnyelserisk
- Sale-and-leaseback → Kan dölja dålig finansiell ställning""",
        "tags": ["IFRS 16", "EV/EBITDA", "analytikerjustering", "skuldsättning", "nyckeltal"],
        "related_metrics": ["EBITDA", "EV/EBITDA", "Nettoskuld", "Skuldsättningsgrad"],
        "source": "CFA/Intern"
    },

    # -----------------------------------------
    # K3 KAPITEL 20 - LEASING
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "K3 Kapitel 20: Leasing (operationell vs finansiell)",
        "content": """K3 kapitel 20 följer äldre IAS 17 och skiljer på operationell och finansiell leasing.

**Klassificering - Finansiell om:**
- Äganderätten övergår vid leasingperiodens slut
- Option att köpa till pris väsentligt under verkligt värde
- Leasingperiod = större delen av tillgångens ekonomiska livslängd
- Nuvärde av minimileaseavgifter ≈ verkligt värde
- Tillgången är specialanpassad för leasetagaren

**Finansiell leasing (K3):**
- Tillgång och skuld i balansräkningen
- Avskrivning + ränta i resultaträkningen
- Liknar IFRS 16 mekanik

**Operationell leasing (K3):**
- Ingen balansräkningseffekt
- Leasingavgiften kostnadsförs linjärt
- Upplysning om framtida minimileaseavgifter i noter

**STOR SKILLNAD MOT IFRS 16:**
K3: De flesta hyresavtal = operationell leasing → Off-balance
IFRS 16: Nästan alla leasingavtal → On-balance

**Konsekvens vid jämförelse:**
K3-bolag har ofta lägre synlig skuldsättning och lägre EBITDA än jämförbara IFRS-bolag.
Analytiker måste justera K3-bolag för att göra dem jämförbara.""",
        "tags": ["K3", "leasing", "operationell", "finansiell", "kapitel 20"],
        "related_metrics": ["EBITDA", "Skuldsättningsgrad", "Leasingkostnad"],
        "source": "K3 Kapitel 20"
    },

    # -----------------------------------------
    # PPA - FÖRVÄRVSREDOVISNING
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "PPA (Purchase Price Allocation): Grundprinciper",
        "content": """Purchase Price Allocation (PPA) är processen att fördela köpeskillingen vid ett rörelseförvärv.

**IFRS 3 kräver:**
1. Identifiera förvärvaren
2. Fastställa förvärvstidpunkten
3. Redovisa identifierbara tillgångar/skulder till verkligt värde
4. Beräkna goodwill

**Köpeskilling består av:**
- Kontant betalning
- Överlåtna tillgångar
- Övertagna skulder
- Egetkapitalinstrument (aktier)
- Villkorad köpeskilling (earn-out) - till verkligt värde vid förvärv

**Förvärvade tillgångar/skulder:**
Alla identifierbara tillgångar och övertagna skulder redovisas till verkligt värde, även:
- Immateriella tillgångar som EJ var redovisade hos säljaren (varumärken, kundrelationer)
- Uppskjutna skatteskulder på övervärden

**Goodwill:**
Goodwill = Köpeskilling - Verkligt värde av nettotillgångar

**K3-skillnad:**
K3 tillåter förenklad förvärvsanalys och kräver mindre detaljerad identifiering av immateriella tillgångar.""",
        "tags": ["PPA", "förvärv", "IFRS 3", "goodwill", "köpeskillingsallokering"],
        "related_metrics": ["Goodwill", "Immateriella tillgångar", "EBITA"],
        "source": "IFRS 3"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "PPA: Identifierbara immateriella tillgångar",
        "content": """Vid PPA ska alla identifierbara immateriella tillgångar värderas separat från goodwill.

**Vanliga immateriella tillgångar vid förvärv:**

1. **Kundrelationer**: Värdet av befintlig kundstock
   - Typisk avskrivningstid: 5-15 år
   - Värdering: Diskonterade framtida kassaflöden från befintliga kunder

2. **Varumärken**: Värdet av varumärket/branding
   - Obestämd livslängd om starkt varumärke → ingen avskrivning, årlig nedskrivningstest
   - Bestämd livslängd → avskrivning

3. **Teknologi/Patent**: Proprietär teknologi
   - Avskrivningstid: Patentets/teknikens återstående livslängd

4. **Order backlog**: Befintliga beställningar/kontrakt
   - Kort avskrivningstid (kontraktslängd)

5. **Icke-konkurrensavtal**: Avtal om att nyckelpersoner ej konkurrerar
   - Avskrivning: Avtalstid

**Effekt på EBITA:**
Avskrivningar på förvärvade immateriella tillgångar (PPA-avskrivningar) exkluderas ofta
i justerad EBITA för att visa underliggande lönsamhet.""",
        "tags": ["PPA", "immateriella tillgångar", "kundrelationer", "varumärke", "EBITA"],
        "related_metrics": ["EBITA", "Immateriella tillgångar", "Avskrivningar"],
        "source": "IFRS 3/IAS 38"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "PPA Numeriskt exempel",
        "content": """**Exempel: Förvärv av TechCo för 500 MSEK**

**Köpeskilling:**
- Kontant: 400 MSEK
- Earn-out (villkorad): 100 MSEK (verkligt värde vid förvärv)
- Total: 500 MSEK

**Verkligt värde förvärvade nettotillgångar:**
| Post | Bokfört | Verkligt | Övervärde |
|------|---------|----------|-----------|
| Materiella tillgångar | 80 | 100 | 20 |
| Kundrelationer | 0 | 120 | 120 |
| Varumärke | 0 | 50 | 50 |
| Teknologi | 20 | 80 | 60 |
| Rörelsekapital | 50 | 50 | 0 |
| Skulder | -100 | -100 | 0 |
| Uppskjuten skatt (20.6%) | 0 | -52 | -52 |
| **Nettotillgångar** | **50** | **248** | **198** |

**Goodwill:**
500 - 248 = 252 MSEK

**Avskrivningar framåt (årlig):**
- Kundrelationer (10 år): 12 MSEK
- Teknologi (5 år): 12 MSEK
- Varumärke (obestämd): 0
- Total PPA-avskrivning: 24 MSEK/år

**Konsekvens:** EBIT minskar med 24 MSEK/år från PPA-avskrivningar.""",
        "tags": ["PPA", "förvärv", "beräkning", "exempel", "goodwill"],
        "related_metrics": ["Goodwill", "EBITA", "Avskrivningar"],
        "source": "Intern beräkning"
    },
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "Goodwill: IFRS vs K3",
        "content": """Goodwill-behandlingen skiljer sig väsentligt mellan IFRS och K3.

**IFRS (IAS 36/IFRS 3):**
- Ingen avskrivning
- Årligt nedskrivningstest (eller oftare vid indikation)
- Testas på kassagenererande enhetsnivå
- Nedskrivning kan EJ reverseras

**K3 (Kapitel 18/19):**
- Avskrivning över nyttjandeperiod
- Max 10 år om perioden ej kan fastställas tillförlitligt
- Ofta 5-10 år i praktiken
- Nedskrivning vid behov

**Konsekvenser:**

För IFRS-bolag:
- Klumprisk - stora nedskrivningar kan komma plötsligt
- "Earnings management" - nedskrivning kan skjutas upp
- Lägre löpande avskrivningar = högre EBIT

För K3-bolag:
- Jämnare resultat över tid
- Förutsägbarhet i avskrivningar
- Lägre EBIT pga löpande goodwillavskrivning

**Analytikerjustering:**
Vid jämförelse av IFRS- och K3-bolag, använd EBITA som exkluderar
goodwillavskrivningar för bättre jämförbarhet.""",
        "tags": ["goodwill", "nedskrivning", "avskrivning", "IFRS", "K3"],
        "related_metrics": ["Goodwill", "EBIT", "EBITA"],
        "source": "IAS 36/K3 Kap 18"
    },
    {
        "domain": "nyckeltal",
        "category": "serieförvärvare",
        "title": "EBITA för serieförvärvare",
        "content": """EBITA (Earnings Before Interest, Tax and Amortization) är viktigt för serieförvärvare.

**Definition:**
EBITA = EBIT + Avskrivningar på förvärvade immateriella tillgångar (PPA-avskrivningar)

**Varför EBITA för serieförvärvare:**
1. PPA-avskrivningar är "non-cash" och representerar ej löpande kostnad
2. Möjliggör jämförelse oavsett förvärvshistorik
3. Visar underliggande operativ lönsamhet
4. Organisk tillväxt blir synlig

**EBITA vs EBITDA:**
- EBITA: Exkluderar PPA-avskrivningar, inkluderar övriga avskrivningar
- EBITDA: Exkluderar alla avskrivningar

**Skillnad i praktiken:**
| Måttet | Inkluderar avskr. på |
|--------|---------------------|
| EBIT | Allt |
| EBITA | Materiella + internt genererade immateriella |
| EBITDA | Ingenting |

**Serieförvärvare-specifikt:**
Bolag som Lifco, Indutrade, Addtech rapporterar ofta EBITA som huvudmått.
Vid analys: Kontrollera definition - vissa inkluderar endast goodwillavskrivning i justering.""",
        "tags": ["EBITA", "serieförvärvare", "PPA", "avskrivning", "nyckeltal"],
        "related_metrics": ["EBITA", "EBIT", "EBITDA", "PPA-avskrivningar"],
        "source": "Intern/CFA"
    },

    # -----------------------------------------
    # EARN-OUT
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "Earn-out redovisning (tilläggsköpeskilling)",
        "content": """Earn-out (villkorad köpeskilling) är vanligt vid förvärv för att hantera osäkerhet.

**IFRS 3-regler:**
1. Redovisas till verkligt värde vid förvärv (dag 1)
2. Klassificeras som skuld eller eget kapital
3. Om skuld: Omvärderas varje period med effekt i resultaträkningen
4. Om eget kapital: Ingen omvärdering

**Klassificering:**
- Skuld: Om det beror på framtida händelser (vanligast)
- Eget kapital: Om det endast beror på antal aktier

**Resultateffekt:**
Ändring i earn-out-skuld → Finansiell kostnad/intäkt (under finansnetto)
PÅVERKAR EJ EBIT/EBITA

**Exempel:**
Förvärv med earn-out på 50 MSEK baserat på 3 års EBIT-mål.
År 1: Verkligt värde = 40 MSEK (redovisas som skuld)
År 2: Bedömning uppåt → Skuld = 55 MSEK → Kostnad 15 MSEK i finansnetto
År 3: Faktiskt utfall = 60 MSEK → Utbetalning, justering av skuld

**Analytisk uppmärksamhet:**
- Earn-outs kan dölja verklig förvärvskostnad
- Omvärderingar skapar resultatvolatilitet
- Stora earn-outs kan indikera osäkerhet om förvärvets värde""",
        "tags": ["earn-out", "villkorad köpeskilling", "förvärv", "IFRS 3"],
        "related_metrics": ["Goodwill", "Finansnetto", "Förvärvskostnad"],
        "source": "IFRS 3"
    },

    # -----------------------------------------
    # VALUTAOMRÄKNING (IAS 21)
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IAS 21 Valutaomräkning: Grundprinciper",
        "content": """IAS 21 reglerar hur valutatransaktioner och utländska verksamheter redovisas.

**Funktionell valuta:**
Den valuta som används i företagets primära ekonomiska miljö.
Faktorer: Vilken valuta påverkar försäljningspriser, kostnader, finansiering.

**Transaktioner i utländsk valuta:**
1. Initial redovisning: Transaktionsdagskurs
2. Vid bokslutet:
   - Monetära poster (fordringar, skulder): Balansdagskurs → Valutadifferens i resultat
   - Icke-monetära poster: Historisk kurs (eller verkligt värde-kurs)

**Omräkning av utländska dotterbolag:**
1. Tillgångar/skulder: Balansdagskurs
2. Intäkter/kostnader: Transaktionskurs (eller genomsnittskurs som approximation)
3. Omräkningsdifferens: Direkt i övrigt totalresultat (OCI)

**Ackumulerad omräkningsdifferens:**
- Samlas i eget kapital
- Vid avyttring av dotterbolag → Omklassificeras till resultaträkningen

**Praktisk effekt:**
Stärkt SEK = Lägre redovisade intäkter från utlandet
Försvagad SEK = Högre redovisade intäkter
Stora valutarörelser kan dölja organisk tillväxt/nedgång.""",
        "tags": ["IAS 21", "valuta", "omräkning", "funktionell valuta", "OCI"],
        "related_metrics": ["Intäkter", "Eget kapital", "Valutaeffekt"],
        "source": "IAS 21"
    },
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "K3 Kapitel 30: Valutaomräkning",
        "content": """K3 kapitel 30 följer i huvudsak IAS 21 men med vissa förenklingar.

**Transaktioner i utländsk valuta:**
- Initialt: Transaktionsdagens kurs
- Monetära poster vid bokslut: Balansdagskurs
- Kursvinst/förlust i resultaträkningen

**Omräkning av utländska filialer/dotterbolag:**
K3 erbjuder två metoder:

1. **Dagskursmetoden** (vanligast):
   - Alla tillgångar/skulder: Balansdagskurs
   - Resultatposter: Genomsnittskurs eller transaktionskurs
   - Differens till eget kapital

2. **Monetär/icke-monetär metod**:
   - Monetära poster: Balansdagskurs
   - Icke-monetära poster: Historisk kurs

**Skillnad mot IFRS:**
- K3 har mindre detaljerade regler för funktionell valuta
- K3 tillåter viss valfrihet i metod för dotterbolagsomräkning
- Upplysningskraven är mindre omfattande

**Analyskonsekvens:**
Bolag med betydande utlandsverksamhet - analysera valutaeffekter separat.
Många bolag redovisar "organisk tillväxt" exklusive valuta.""",
        "tags": ["K3", "valuta", "omräkning", "dagskursmetoden", "kapitel 30"],
        "related_metrics": ["Intäkter", "Valutaeffekt", "Eget kapital"],
        "source": "K3 Kapitel 30"
    },

    # -----------------------------------------
    # SÄKRINGSREDOVISNING
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 9 Säkringsredovisning: Grundprinciper",
        "content": """Säkringsredovisning (hedge accounting) matchar vinster/förluster på säkringsinstrument med den säkrade posten.

**Tre typer av säkringar:**

1. **Verkligt värde-säkring (Fair value hedge)**
   - Säkrar verkligt värde på tillgång/skuld
   - Både säkringsinstrument och säkrad post värderas till verkligt värde i resultat
   - Exempel: Ränteswap för att säkra fasträntelån

2. **Kassaflödessäkring (Cash flow hedge)**
   - Säkrar framtida kassaflöden
   - Effektiv del → OCI (eget kapital)
   - Ineffektiv del → Resultaträkning
   - Exempel: Valutatermin för prognisticerad export

3. **Säkring av nettoinvestering i utlandsverksamhet**
   - Säkrar valutaexponering i utländskt dotterbolag
   - Till OCI (som omräkningsdifferenser)

**Kvalifikationskriterier:**
- Formell dokumentation och säkringsrelation
- Säkringen förväntas vara effektiv
- Effektivitet kan mätas tillförlitligt

**Kassaflödessäkringsreserven:**
Ligger i eget kapital under "Övrigt totalresultat". Omklassificeras till resultat när
den säkrade transaktionen påverkar resultatet.""",
        "tags": ["IFRS 9", "säkring", "hedge accounting", "kassaflödessäkring", "OCI"],
        "related_metrics": ["Finansnetto", "Eget kapital", "Kassaflödessäkringsreserv"],
        "source": "IFRS 9"
    },
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "K3 Säkringsredovisning (Kapitel 11-12)",
        "content": """K3 har mer begränsade säkringsregler än IFRS 9.

**Grundregel K3 (Kapitel 11):**
Finansiella instrument värderas till anskaffningsvärde eller upplupet anskaffningsvärde.

**Kapitel 12 - Verkligt värde-metoden (valfri):**
Företag kan välja att värdera vissa finansiella instrument till verkligt värde.
Om valt → Värdeförändringar i resultaträkningen.

**Säkringsredovisning K3:**
Tillåter enkel form av säkringsredovisning:
- Säkringsinstrument och säkrad post redovisas ihop
- Vinst/förlust matchas i tid
- Mindre formella krav än IFRS 9

**Skillnad mot IFRS 9:**
- K3 har ingen explicit "kassaflödessäkringsreserv" i OCI
- Dokumentationskraven är lägre
- Effektivitetstester är mindre rigorösa

**Praktisk konsekvens:**
K3-bolag med valutasäkring visar ofta resultatpåverkan direkt, utan OCI-buffert.
IFRS-bolag kan ha stor "dold" säkringsreserv i eget kapital som påverkar
framtida resultat.""",
        "tags": ["K3", "säkring", "kapitel 11", "kapitel 12", "verkligt värde"],
        "related_metrics": ["Finansnetto", "Eget kapital"],
        "source": "K3 Kapitel 11-12"
    },

    # -----------------------------------------
    # PERIODISERINGAR OCH AVSÄTTNINGAR
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IAS 37 Avsättningar: Redovisningskrav",
        "content": """IAS 37 reglerar redovisning av avsättningar, eventualförpliktelser och eventualtillgångar.

**Avsättning redovisas om:**
1. Befintlig förpliktelse (legal eller informell) från tidigare händelse
2. Sannolikt (>50%) att utflöde av resurser krävs
3. Beloppet kan uppskattas tillförlitligt

**Värdering:**
- Bästa uppskattning av utgiften
- Om väsentlig tidseffekt → Nuvärdesberäkning
- Risker och osäkerheter beaktas

**Vanliga avsättningar:**
- Garantiåtaganden
- Omstrukturering (endast om beslutad och kommunicerad)
- Miljöåtaganden
- Rättstvister
- Förlustkontrakt

**Eventualförpliktelse (not-upplysning):**
Redovisas EJ i balansräkningen om:
- Möjlig förpliktelse vars existens bekräftas av framtida händelse
- Förpliktelse finns men utflöde ej sannolikt
- Beloppet kan ej uppskattas

**Röda flaggor:**
- Ökande avsättningar kan indikera operativa problem
- Minskande avsättningar kan vara aggressiv redovisning
- Stora upplösningar av avsättningar → Granska kritiskt""",
        "tags": ["IAS 37", "avsättningar", "eventualförpliktelse", "garanti", "omstrukturering"],
        "related_metrics": ["Avsättningar", "EBIT", "Kassaflöde"],
        "source": "IAS 37"
    },
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "Upplupna kostnader och periodiseringar",
        "content": """Periodiseringar säkerställer att intäkter och kostnader hamnar i rätt period.

**Upplupna kostnader (accrued expenses):**
- Kostnader som hör till perioden men ej fakturerats/betalats
- Exempel: Löner, räntor, revision, el
- Redovisas som kortfristig skuld

**Förutbetalda kostnader (prepaid expenses):**
- Betalningar för kostnader som hör till framtida period
- Exempel: Försäkring, hyra i förskott
- Redovisas som omsättningstillgång

**Upplupna intäkter (accrued revenue):**
- Intäkter intjänade men ej fakturerade
- Ofta kopplat till successiv vinstavräkning
- Redovisas som fordran

**Förutbetalda intäkter (deferred revenue):**
- Betalning mottagen för ej levererat
- Vanligt vid prenumerationer, licenser
- Redovisas som skuld

**Analytiska frågor:**
- Stora förändringar i periodiseringar kan påverka kassaflöde
- Uppskjutna intäkter = "backlog" av framtida intäkter
- Upplupna kostnader kan dölja kommande utbetalningar""",
        "tags": ["periodisering", "upplupna kostnader", "uppskjutna intäkter", "accrual"],
        "related_metrics": ["Rörelsekapital", "Kassaflöde", "Uppskjutna intäkter"],
        "source": "IAS 1/Allmänt"
    },

    # -----------------------------------------
    # AKTIERELATERADE ERSÄTTNINGAR
    # -----------------------------------------
    {
        "domain": "redovisning",
        "category": "IFRS",
        "title": "IFRS 2 Aktierelaterade ersättningar",
        "content": """IFRS 2 reglerar redovisning av optioner, aktier och andra aktierelaterade ersättningar till anställda.

**Tre kategorier:**

1. **Egetkapitalreglerade (Equity-settled)**
   - Motparten får aktier eller optioner
   - Värderas till verkligt värde vid tilldelning
   - Kostnad periodiseras över intjänandeperiod
   - Ingen omvärdering efter tilldelning

2. **Kontantreglerade (Cash-settled)**
   - Motparten får kontant baserat på aktiekurs
   - Omvärderas varje period till verkligt värde
   - Skuld i balansräkningen

3. **Val mellan kontant/aktie**
   - Klassificering beror på vem som har valet

**Värdering av optioner:**
- Black-Scholes eller binomialmodell
- Beaktar: Lösenpris, löptid, volatilitet, riskfri ränta, utdelning

**Intjänandevillkor:**
- Service condition: Måste arbeta viss tid
- Performance condition: Mål måste uppnås
- Market condition: Aktiekurs måste nå nivå (inbakat i värdering)

**EBITDA-justering:**
Många bolag justerar EBITDA för aktierelaterade ersättningar (non-cash).""",
        "tags": ["IFRS 2", "optioner", "aktieersättning", "incitament", "Black-Scholes"],
        "related_metrics": ["Personalkostnader", "EBITDA", "Eget kapital"],
        "source": "IFRS 2"
    },
    {
        "domain": "redovisning",
        "category": "K3",
        "title": "K3 Aktierelaterade ersättningar (Kapitel 26)",
        "content": """K3 kapitel 26 hanterar aktierelaterade ersättningar förenklat jämfört med IFRS 2.

**Egetkapitalreglerade:**
- Värderas till verkligt värde vid tilldelning
- Kostnad periodiseras över intjänandeperiod
- I princip samma som IFRS 2

**Kontantreglerade:**
- Skuld värderas till verkligt värde varje period
- Värdeförändring i resultaträkningen

**Förenklingar i K3:**
- Mindre detaljerad vägledning för värdering
- Färre specifika regler för olika villkorstyper
- Tillåter pragmatiska värderingsmetoder

**Syntetiska optioner (vanligt i Sverige):**
- Ger rätt till kontant utbetalning baserat på aktiekurs
- Kontantreglerad = omvärdering varje period
- Kan skapa resultatvolatilitet

**Praktisk konsekvens:**
K3-bolag har ofta enklare incitamentsprogram.
Vid analys: Kontrollera om aktierelaterade ersättningar är inkluderade i justerat EBITDA.""",
        "tags": ["K3", "aktieersättning", "syntetiska optioner", "kapitel 26"],
        "related_metrics": ["Personalkostnader", "EBITDA"],
        "source": "K3 Kapitel 26"
    },

    # -----------------------------------------
    # RÖDA FLAGGOR
    # -----------------------------------------
    {
        "domain": "kvalitativ",
        "category": "röda_flaggor",
        "title": "Röda flaggor: Intäktsredovisning",
        "content": """Varningssignaler vid analys av intäktsredovisning:

**Tidpunktsfrågor:**
- Aggressiv intäktsredovisning vid kvartalsslutet (channel stuffing)
- Intäkter från related parties utan kommersiell substans
- Stora intäktsjusteringar efter periodens slut

**Kvalitetsfrågor:**
- Intäkter växer snabbare än kassaflöde från verksamheten
- Kundfordringar växer snabbare än intäkter (DSO ökar)
- Stora förändringar i uppskjutna intäkter utan förklaring

**Redovisningsprinciper:**
- Byte av intäktsredovisningsprincip utan tydlig anledning
- Avvikande principer jämfört med branschpraxis
- Vaga eller ändrade definitioner av "organisk tillväxt"

**IFRS 15-specifikt:**
- Ovanligt hög andel "over time"-intäkter jämfört med peers
- Aggressiv allokering av transaktionspris till tidiga åtaganden
- Variabel ersättning redovisas utan synlig constraint

**Kontrollfrågor:**
1. Matchar kassaflöde intäktstillväxten?
2. Är redovisningsprinciperna konsekventa över tid?
3. Hur ser branschpraxis ut?""",
        "tags": ["röda flaggor", "intäkter", "kvalitet", "warning signs"],
        "related_metrics": ["Intäkter", "DSO", "Kassaflöde"],
        "source": "Intern/CFA"
    },
    {
        "domain": "kvalitativ",
        "category": "röda_flaggor",
        "title": "Röda flaggor: Leasing och skuldsättning",
        "content": """Varningssignaler relaterade till leasing och skuldsättning:

**Leasingrelaterat:**
- Mycket stora leasingskulder relativt balansräkningen
- Kort återstående leasingtid → förnyelserisk
- Sale-and-leaseback-transaktioner som genererar vinst
- Operationell leasing hos K3-bolag utan tillräcklig upplysning

**Dolda skulder:**
- Garantiåtaganden för andras skulder (off-balance)
- Factoring av kundfordringar (kan vara dold finansiering)
- Stora eventualförpliktelser i noter
- Pensionsskulder (särskilt förmånsbestämda)

**Finansiell struktur:**
- Snabbt ökande skuldsättning utan motsvarande tillväxt
- Korta skuldlöptider med stora refinansieringsbehov
- Covenants nära bristningsgräns
- Negativ räntetäckningsgrad

**Kassaflödesmanipulation:**
- Klassificering av leasingbetalningar (operativt vs finansiering)
- Stora förändringar i leverantörsskulder
- Negativ trend i kassakonvertering

**Kontrollfrågor:**
1. Hur ser skuldsättningen ut inklusive leasing?
2. Finns det off-balance-förpliktelser i noterna?
3. Matchar kassaflödet den redovisade skuldsättningen?""",
        "tags": ["röda flaggor", "leasing", "skuldsättning", "off-balance"],
        "related_metrics": ["Skuldsättningsgrad", "Nettoskuld", "Räntetäckningsgrad"],
        "source": "Intern/CFA"
    },
    {
        "domain": "kvalitativ",
        "category": "röda_flaggor",
        "title": "Röda flaggor: Förvärv och goodwill",
        "content": """Varningssignaler vid analys av förvärvsintensiva bolag:

**PPA-relaterat:**
- Mycket hög goodwill-andel av förvärvspris (>70%)
- Ovanligt långa avskrivningstider på immateriella tillgångar
- Stora earn-outs som indikerar osäkerhet
- Ändring av PPA-värdering efter initial period

**Goodwill-hantering:**
- Ingen nedskrivning trots svag utveckling i förvärvade enheter
- Omfördelning av goodwill mellan kassagenererande enheter
- Orealistiska antaganden i nedskrivningstester
- Snabbt ackumulerande goodwill utan motsvarande vinsttillväxt

**Förvärvstakt:**
- Intäktstillväxt nästan uteslutande från förvärv
- Integrationsresultat som aldrig materialiseras
- Ökande avkastningskrav från förvärv till förvärv
- Nyckelpersoner som lämnar förvärvade bolag

**Finansiering:**
- Ökande skuldsättning för att finansiera förvärv
- Utspädning genom aktieemissioner utan värdeskapande
- Earn-outs som skjuter upp verklig betalning

**Kontrollfrågor:**
1. Vad är avkastningen på investerat kapital (ROIC)?
2. Hur utvecklas förvärvade enheter efter 2-3 år?
3. Skapar förvärven verkligt värde eller bara tillväxt?""",
        "tags": ["röda flaggor", "förvärv", "goodwill", "PPA", "serieförvärvare"],
        "related_metrics": ["Goodwill", "ROIC", "EBITA"],
        "source": "Intern/CFA"
    },
]


def main():
    """Huvudfunktion för att populera databasen."""
    print("=" * 60)
    print("POPULERAR KNOWLEDGE-DATABASEN")
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
