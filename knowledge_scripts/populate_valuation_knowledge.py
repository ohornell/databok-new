#!/usr/bin/env python3
"""
Script för att populera knowledge-databasen med värderingsmetodik.
Fokus på svenska förhållanden och praktisk tillämpning för analytiker.
"""

import os
import time
import requests
from dotenv import load_dotenv
from supabase import create_client

# Ladda miljövariabler
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL och SUPABASE_KEY måste vara satta")

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
# VÄRDERINGSKUNSKAP
# =============================================================================

VALUATION_KNOWLEDGE = [
    # -----------------------------------------
    # DCF - GRUNDLÄGGANDE TEORI
    # -----------------------------------------
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "DCF-värdering: Teoretisk grund och fundamentalt samband",
        "content": """Discounted Cash Flow (DCF) baseras på principen att ett bolags värde är nuvärdet av alla framtida kassaflöden.

**Det fundamentala sambandet:**
Enterprise Value = Σ (FCFt / (1+WACC)^t) + Terminal Value / (1+WACC)^n

**Två huvudsakliga kassaflödesmått:**

**FCFF (Free Cash Flow to Firm):**
= EBIT × (1-skatt) + Avskrivningar - ΔRörelsekapital - Capex
Diskonteras med WACC → Ger Enterprise Value

**FCFE (Free Cash Flow to Equity):**
= FCFF - Räntekostnader × (1-skatt) + Nettoupplåning
Diskonteras med Cost of Equity → Ger Equity Value direkt

**Svenska förhållanden:**
- Bolagsskatt 20.6% (2024)
- FCFF är standardmetod för svenska bolag
- FCFE används sällan pga skuldsättningsantaganden

**När DCF fungerar bra:**
- Stabila kassaflöden (Assa Abloy, Atlas Copco)
- Långa kontrakt (infrastruktur, utilities)
- Mogna bolag med förutsägbar capex

**När DCF är problematiskt:**
- Förlustbolag (negativa kassaflöden)
- Tidiga tillväxtbolag (Spotify tidigt)
- Cykliska (SSAB, Boliden) - svårt normalisera""",
        "tags": ["DCF", "FCFF", "FCFE", "nuvärde", "kassaflöde", "Enterprise Value"],
        "related_metrics": ["EBIT", "Avskrivningar", "Capex", "Rörelsekapital"],
        "source": "CFA/Damodaran"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "WACC: Fullständig formel och komponenter",
        "content": """WACC (Weighted Average Cost of Capital) är den vägda genomsnittliga kapitalkostnaden.

**Fullständig formel:**
WACC = (E/V) × Re + (D/V) × Rd × (1-T)

Där:
- E = Marknadsvärde eget kapital
- D = Marknadsvärde skulder
- V = E + D (totalt kapital)
- Re = Cost of Equity
- Rd = Cost of Debt
- T = Bolagsskattesats (20.6% i Sverige)

**Kapitalstruktur - viktiga val:**
1. Använd MARKNADSVÄRDEN, inte bokförda
2. Målkapitalstruktur vs aktuell: Använd målstruktur om rimlig
3. Skuld = räntebärande skulder + leasingskulder (IFRS 16)

**Typiska svenska förhållanden:**
- Industribolag: 20-40% skuld
- Fastighetsbolag: 40-60% skuld (högre hävstång)
- Tillväxtbolag: 0-10% skuld

**Beräkningsexempel:**
Bolag med 70% eget kapital, 30% skuld
Re = 9%, Rd = 4%, T = 20.6%
WACC = 0.70 × 9% + 0.30 × 4% × (1-0.206) = 6.3% + 0.95% = 7.25%

**Fallgrop:**
Använd inte bokförd skuldsättning för tillväxtbolag - de har ofta ingen skuld men ska ändå ha teoretisk optimal struktur i WACC.""",
        "tags": ["WACC", "kapitalkostnad", "skuldsättning", "kapitalstruktur"],
        "related_metrics": ["Skuldsättningsgrad", "Nettoskuld", "Enterprise Value"],
        "source": "CFA/Damodaran"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "Cost of Equity via CAPM för svenska bolag",
        "content": """Cost of Equity beräknas vanligen med CAPM (Capital Asset Pricing Model).

**CAPM-formeln:**
Re = Rf + β × (Rm - Rf)

**Riskfri ränta (Rf) - Svenska val:**
- Svensk 10-årig statsobligation (standardval)
- Alternativt: Tysk 10-årig Bund + landpremie
- 2024-nivå: ca 2.0-2.5%
- Vid långa DCF (10+ år): Överväg normaliserad ränta 2.5-3.0%

**Beta (β) - Systematisk risk:**
1. Rådata: 2-5 års veckodata mot OMXS30 eller MSCI World
2. Blume-justering: Adjusted β = (2/3) × Raw β + (1/3) × 1.0
3. Unlevered beta: βu = βL / (1 + (1-T) × D/E)
4. Relevered beta: βL = βu × (1 + (1-T) × D/E_mål)

**Marknadens riskpremium (Rm - Rf):**
- Historisk (Sverige 1900-2024): ca 4-5%
- Framåtblickande (survey): ca 5-6%
- Damodaran ERP (2024): ca 5.0% för Sverige
- Praktiker använder ofta 5-6%

**Komplett svensk CAPM-exempel:**
Rf = 2.5%, β = 1.1, ERP = 5.5%
Re = 2.5% + 1.1 × 5.5% = 8.55% ≈ 8.5%

**Small-cap premium:**
För mindre bolag (< 10 mdr SEK): Lägg till 1-3% premium
Motivering: Högre risk, lägre likviditet, mindre diversifierade""",
        "tags": ["CAPM", "Cost of Equity", "beta", "riskpremium", "riskfri ränta"],
        "related_metrics": ["Beta", "Avkastningskrav"],
        "source": "CFA/Damodaran"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "Cost of Debt och räntekostnad för svenska bolag",
        "content": """Cost of Debt (Rd) är kostnaden för lånat kapital.

**Grundprincip:**
Använd marginell lånekostnad (inte genomsnittlig historisk)

**Metoder att estimera Rd:**

1. **Observerade obligationer:**
   Om bolaget har utestående obligationer - använd YTM (yield to maturity)

2. **Kreditspread-metod:**
   Rd = Riskfri ränta + Kreditspread
   - Investment grade (BBB+): +100-150 bps
   - Crossover (BB): +200-350 bps
   - High yield (B): +400-600 bps

3. **Syntetisk rating:**
   Basera på räntetäckningsgrad (EBIT/Räntekostnad)
   | ICR | Rating | Spread |
   |-----|--------|--------|
   | >8  | A      | 80 bps |
   | 6-8 | BBB    | 120 bps|
   | 4-6 | BB     | 250 bps|
   | 2-4 | B      | 450 bps|

**Svenska förhållanden (2024):**
- Storbankslån till stora bolag: STIBOR + 100-200 bps
- Obligationsmarknaden: Mer utvecklad för fastighet/infrastruktur
- Medelstora bolag: Ofta 4-6% totalt

**WACC-påverkan:**
Rd används EFTER SKATT: Rd × (1-T)
Med Rd = 5% och T = 20.6%: Rd efter skatt = 3.97%

**Leasingskulder:**
Inkludera leasingskulder i D med implicit ränta (ofta 3-5%)""",
        "tags": ["Cost of Debt", "kreditspread", "lånekostnad", "rating"],
        "related_metrics": ["Räntetäckningsgrad", "Räntekostnad", "Nettoskuld"],
        "source": "CFA/Moody's"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "Kassaflödesprognoser: Explicit period och drivers",
        "content": """Kassaflödesprognoser är DCF-modellens kärna. Kräver detaljerad modellering.

**Längd på explicit prognosperiod:**
- Standard: 5-7 år
- Cykliska bolag: Minst en full cykel (7-10 år)
- Tillväxtbolag: Tills stabil tillväxt nås (kan vara 10+ år)

**Viktiga drivers att modellera:**

**1. Omsättningstillväxt:**
- Organisk vs förvärvsdriven
- Volym vs pris vs mix
- Svensk marknad vs export
- Sektorspecifika drivare

**2. Marginaler:**
- Bruttomarginal: Inputkostnader, pricing power
- EBIT-marginal: Skalekonomi, kostnadsstruktur
- Konvergera mot branschgenomsnitt på sikt

**3. Capex (investeringar):**
- Underhålls-capex ≈ Avskrivningar (steady state)
- Tillväxt-capex: Expansionsinvesteringar
- Typisk kvot Capex/Avskrivningar:
  - Mogna bolag: 1.0-1.2x
  - Tillväxtbolag: 1.5-2.5x

**4. Rörelsekapital:**
- DSO (Days Sales Outstanding): Kundfordringar
- DIO (Days Inventory Outstanding): Lager
- DPO (Days Payables Outstanding): Leverantörsskulder
- NWC = (DSO + DIO - DPO) / 365 × Omsättning

**Rörelsekapitalnormalisering:**
- Analysera historisk trend
- Jämför med peers
- Undvik att extrapolera "extrema" år

**Svensk branschdata (typiska värden):**
| Sektor | DSO | DIO | DPO | NWC/Sales |
|--------|-----|-----|-----|-----------|
| Industri | 60 | 90 | 60 | 25% |
| Retail | 10 | 60 | 45 | 7% |
| SaaS | 30 | 0 | 30 | 0% |""",
        "tags": ["kassaflöde", "prognos", "capex", "rörelsekapital", "tillväxt"],
        "related_metrics": ["DSO", "DIO", "DPO", "Capex", "FCFF"],
        "source": "CFA/McKinsey"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "Terminalvärde: Gordon Growth vs Exit Multiple",
        "content": """Terminalvärdet representerar värdet efter explicit prognosperiod. Ofta 60-80% av totalt DCF-värde!

**Metod 1: Gordon Growth Model (Perpetuity)**

TV = FCF_n+1 / (WACC - g)
   = FCF_n × (1+g) / (WACC - g)

**Val av evig tillväxtakt (g):**
- Teoretiskt max: Långsiktig BNP-tillväxt + inflation
- Sverige: ca 2.0-2.5% nominellt
- Defensivt antagande: 2.0%
- Cykliska: 1.5-2.0%
- Tillväxtbolag: Högre men konvergera via fade period

**Metod 2: Exit Multiple**

TV = EBITDA_n × EV/EBITDA_exit

**Val av exit multiple:**
- Peers-genomsnitt vid moget stadium
- Historiskt genomsnitt för sektorn
- Ofta 8-12x EBITDA för industribolag

**Fade period:**
Om bolaget har hög tillväxt i explicit period, lägg in fade:
- År 1-5: Hög tillväxt (15%)
- År 6-8: Fade (15% → 5% → 2%)
- Terminal: 2%

**Känslighetsanalys (KRITISKT):**
| g \ WACC | 7.0% | 7.5% | 8.0% |
|----------|------|------|------|
| 1.5% | 18.2x | 16.7x | 15.4x |
| 2.0% | 20.0x | 18.2x | 16.7x |
| 2.5% | 22.2x | 20.0x | 18.2x |

(x = implicit TV/FCF-multipel)

**Sanity check:**
- Implicit TV EV/EBITDA: Bör vara rimlig (8-15x)
- Terminalvärdets andel: Om >80% - ifrågasätt antaganden""",
        "tags": ["terminalvärde", "Gordon Growth", "exit multiple", "perpetuity", "evig tillväxt"],
        "related_metrics": ["FCFF", "EV/EBITDA", "WACC"],
        "source": "CFA/Damodaran"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "DCF: Brygga från Enterprise Value till Equity Value",
        "content": """Efter DCF-beräkning måste Enterprise Value översättas till Equity Value per aktie.

**Grundformel:**
Equity Value = Enterprise Value - Nettoskuld - Minoritetsintressen - Preferensaktier + Intressebolagsandelar

**Nettoskuld (detaljerad):**
+ Räntebärande skulder (kort + lång)
+ Leasingskulder (IFRS 16)
+ Pensionsskulder (netto efter skatt)
+ Minoritetsägares andel av skuld
- Kassa och kortfristiga placeringar
- Överfinansierade pensioner

**Minoritetsintressen:**
Om dotterbolag inte är helägda:
- Konsoliderade siffror inkluderar 100% av dotterbolaget
- Men moderbolaget äger bara X%
- Dra av minoritetens andel av värdet

**Intressebolag (20-50% ägande):**
- INTE konsoliderade i EBIT/FCFF
- Värde ska ADDERAS till EV
- Värdera separat eller använd bokfört värde

**Preferensaktier:**
- Har företräde före stamaktier
- Dra av marknadsvärde

**Per-aktie-beräkning:**
Equity Value per aktie = Equity Value / Antal utestående aktier

**Antal aktier:**
- Använd UTSPÄTT antal (inkl optioner, konvertibler)
- Treasury stock method för optioner

**Svenskt exempel:**
| Post | MSEK |
|------|------|
| Enterprise Value (DCF) | 15,000 |
| - Räntebärande skulder | -3,000 |
| - Leasingskulder | -500 |
| - Pensionsskuld (netto) | -200 |
| + Kassa | +1,200 |
| - Minoritet | -300 |
| + Intressebolag | +400 |
| **Equity Value** | **12,600** |
| Antal aktier (utspätt) | 100 m |
| **Värde per aktie** | **126 SEK** |""",
        "tags": ["Enterprise Value", "Equity Value", "nettoskuld", "brygga", "per aktie"],
        "related_metrics": ["Nettoskuld", "Minoritetsintresse", "Aktiekurs"],
        "source": "CFA/McKinsey"
    },
    {
        "domain": "värdering",
        "category": "DCF",
        "title": "DCF: Scenarioanalys och känslighet",
        "content": """DCF-värdet är extremt känsligt för antaganden. Scenarioanalys är obligatoriskt.

**Känslighetsvariabler att testa:**

**1. WACC (±50-100 bps):**
- Liten förändring → Stor värdeeffekt
- Testa 6.5%, 7.0%, 7.5%, 8.0%

**2. Terminal tillväxt (±50 bps):**
- 1.5%, 2.0%, 2.5%
- Får ALDRIG överstiga WACC

**3. EBIT-marginal (±100-200 bps):**
- Normaliserad marginal i terminalår
- Jämför med historik och peers

**4. Omsättningstillväxt:**
- Bull: Marknaden växer snabbt, bolaget tar share
- Base: Marknadstillväxt, bibehållen share
- Bear: Motvind, share loss

**Scenariouppställning:**

| Scenario | Sannolikhet | Tillväxt | Marginal | Värde/aktie |
|----------|-------------|----------|----------|-------------|
| Bull | 25% | 8% | 18% | 180 SEK |
| Base | 50% | 5% | 15% | 130 SEK |
| Bear | 25% | 2% | 12% | 80 SEK |
| **Viktat** | 100% | - | - | **130 SEK** |

**Monte Carlo (avancerat):**
- Definiera sannolikhetsfördelningar för inputs
- Simulera tusentals scenarion
- Få fördelning av möjliga värden

**Praktisk regel:**
Om DCF ger värde >50% över/under börskurs:
1. Dubbelkolla antaganden
2. Jämför med peers-multiplar
3. Gör extra scenarioanalys

**Röd flagga:**
Om terminalvärdet är >85% av totalt värde → modellen är för känslig för terminalantaganden.""",
        "tags": ["känslighet", "scenarioanalys", "Bull Base Bear", "Monte Carlo"],
        "related_metrics": ["WACC", "EBIT-marginal", "Tillväxt"],
        "source": "CFA/McKinsey"
    },

    # -----------------------------------------
    # PEER-VÄRDERING OCH MULTIPLAR
    # -----------------------------------------
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "Trailing vs Forward multiplar: Definition och användning",
        "content": """Multiplar kan baseras på historiska (trailing) eller prognosticerade (forward) siffror.

**Trailing multiplar:**
- Baseras på senaste 12 månaders utfall (LTM = Last Twelve Months)
- Alternativt: Senaste helårsresultat
- Fördelar: Faktiska siffror, ingen prognosrisk
- Nackdelar: Bakåtblickande, kan inkludera engångsposter

**Forward multiplar:**
- Baseras på prognoser (NTM = Next Twelve Months, eller specifikt år)
- Konsensusprognoser från analytiker
- Fördelar: Framåtblickande, prissätter förväntad utveckling
- Nackdelar: Prognosrisk, bias i estimates

**Vanliga varianter:**

| Multipel | Trailing | Forward |
|----------|----------|---------|
| P/E | P/E LTM | P/E NTM, P/E 2025E |
| EV/EBITDA | EV/EBITDA LTM | EV/EBITDA NTM |
| EV/Sales | EV/Sales LTM | EV/Sales NTM |

**När använda vilken:**

**Trailing (LTM):**
- Stabila bolag med förutsägbar utveckling
- När du är skeptisk till prognoser
- Cykliska bolag nära topp (försiktighet)

**Forward (NTM/2025E):**
- Tillväxtbolag där framtiden skiljer sig från historik
- Vid stora strukturella förändringar
- Standard bland institutionella investerare

**Svenska marknaden:**
- Analytiker rapporterar oftast forward multiplar
- Bloomberg/Refinitiv visar båda
- Årsrapporter ger trailing-data

**Beräkningsexempel:**
Aktiekurs 150 SEK, EPS LTM = 8 SEK, EPS 2025E = 10 SEK
- P/E LTM = 150/8 = 18.8x
- P/E 2025E = 150/10 = 15.0x

**Viktig distinction:**
NTM = Rullande 12 månader framåt
FY 2025E = Kalenderår/räkenskapsår 2025""",
        "tags": ["trailing", "forward", "LTM", "NTM", "P/E", "EV/EBITDA"],
        "related_metrics": ["P/E", "EV/EBITDA", "EPS"],
        "source": "CFA/Goldman Sachs"
    },
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "Enterprise Value: Beräkning och komponenter",
        "content": """Enterprise Value (EV) är det totala värdet av rörelsen, oavsett finansiering.

**Grundformel:**
EV = Börsvärde + Nettoskuld + Minoritetsintressen + Preferensaktier - Intressebolag

**Detaljerad nettoskuld:**
+ Räntebärande skulder (obligationer, banklån)
+ Leasingskulder (IFRS 16) - VIKTIGT!
+ Pensionsskulder (diskonterad, netto)
+ Hybridkapital (beroende på klassificering)
- Kassa och likvida medel
- Kortfristiga finansiella placeringar

**Varför EV istället för börsvärde:**
- Jämförbarhet oavsett kapitalstruktur
- Inkluderar alla kapitalleverantörer
- EBITDA/EBIT är före finansieringskostnader

**Svenska IFRS 16-effekten:**
Före 2019: Operationella leasingavtal = off-balance
Efter 2019: Leasingskulder i balansräkningen
→ EV ska ALLTID inkludera leasingskulder för konsistens

**Beräkningsexempel (svensk industri):**
| Post | MSEK |
|------|------|
| Börsvärde (aktiekurs × antal) | 25,000 |
| + Räntebärande skulder | 5,000 |
| + Leasingskulder | 2,000 |
| + Pensionsskuld | 500 |
| - Kassa | -3,000 |
| - Kortfr. placeringar | -500 |
| + Minoritet | 800 |
| - Intressebolag (50% × FV) | -1,200 |
| **Enterprise Value** | **28,600** |

**Vanliga fel:**
1. Glömmer leasingskulder
2. Använder bokfört eget kapital istället för börsvärde
3. Glömmer preferensaktier
4. Inkluderar inte minoritet""",
        "tags": ["Enterprise Value", "EV", "nettoskuld", "börsvärde", "leasing"],
        "related_metrics": ["Börsvärde", "Nettoskuld", "Leasingskuld"],
        "source": "CFA/Damodaran"
    },
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "EV/EBITDA: Standardmultipel och användning",
        "content": """EV/EBITDA är den vanligaste multipeln för bolagsvärdering.

**Formel:**
EV/EBITDA = Enterprise Value / EBITDA

**Varför EV/EBITDA:**
- Kapitalstruktur-neutral (jämförbart oavsett skuldsättning)
- Före avskrivningar (jämförbart oavsett capex-historik)
- Proxy för kassaflöde (före capex och rörelsekapital)

**Justerat EBITDA:**
Använd normaliserad/justerad EBITDA:
- Exklusive engångsposter
- Exklusive omstruktureringskostnader
- Inkludera/exkludera IFRS 16 konsistent

**Svenska branschmultiplar (2024 typiska värden):**

| Sektor | EV/EBITDA NTM |
|--------|---------------|
| Industri (Assa, Atlas) | 12-16x |
| Fastighet | 15-25x |
| Bank | N/A (använd P/E, P/B) |
| Retail | 6-10x |
| Tech/SaaS | 15-30x |
| Serieförvärvare | 12-18x |

**Fördelar:**
- Enkel att beräkna
- Ignorerar skillnader i avskrivningspolicies
- Funkar för de flesta bolagstyper

**Nackdelar:**
- Ignorerar capex-behov (problem för capex-tunga bolag)
- EBITDA ≠ kassaflöde
- Påverkas av IFRS 16 (leasingbehandling)

**Tumregel för värdering:**
< 8x: Billigt eller problem
8-12x: Normalt moget bolag
12-18x: Kvalitet eller tillväxt
> 18x: Hög tillväxt eller bubbla

**Kontrollera alltid:**
- Jämför med historisk egen multipel
- Jämför med relevanta peers
- Förstå varför avvikelser existerar""",
        "tags": ["EV/EBITDA", "multipel", "värdering", "branschmultiplar"],
        "related_metrics": ["EBITDA", "Enterprise Value", "Bruttomarginal"],
        "source": "CFA/Goldman Sachs"
    },
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "EV/EBITA vs EV/EBITDA: När använda vilken",
        "content": """EBITA-multipeln är viktig för serieförvärvare och bolag med stora PPA-avskrivningar.

**Definition:**
- EBITDA = EBIT + Avskrivningar (alla)
- EBITA = EBIT + Avskrivningar på förvärvade immateriella (PPA-avskrivningar)

**EV/EBITA används för:**
1. **Serieförvärvare** (Lifco, Indutrade, Addtech, Lagercrantz)
   - Stora PPA-avskrivningar från förvärv
   - EBITA visar underliggande lönsamhet

2. **Bolag med stor förvärvad goodwill/immateriella**
   - Avskrivningar är "non-cash" och ej operativa

**EV/EBITDA används för:**
1. **De flesta bolag** utan stor förvärvshistorik
2. **Jämförelse** mellan ägda vs leasade tillgångar
3. **Capex-proxy** - EBITDA före investeringar

**Svenska serieförvärvare - typiska multiplar:**

| Bolag | EV/EBITDA | EV/EBITA |
|-------|-----------|----------|
| Lifco | 20x | 18x |
| Indutrade | 18x | 16x |
| Addtech | 16x | 14x |
| Lagercrantz | 15x | 13x |

**Beräkning av PPA-avskrivning:**
Från årsredovisning, not om immateriella tillgångar:
- Avskrivning kundrelationer
- Avskrivning varumärken
- Avskrivning övriga förvärvade immateriella

EBITA = EBIT + Σ(PPA-avskrivningar)

**Tumregel:**
Om förvärvsrelaterade immateriella > 20% av totala tillgångar → Använd EBITA
Om organiskt bolag utan förvärv → EBITDA räcker""",
        "tags": ["EV/EBITA", "EV/EBITDA", "serieförvärvare", "PPA", "immateriella"],
        "related_metrics": ["EBITA", "EBITDA", "PPA-avskrivningar"],
        "source": "Svenska analytikerstandarder"
    },
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "P/E-tal: Trailing, Forward och Shiller CAPE",
        "content": """P/E (Price/Earnings) är den klassiska aktiemultipeln.

**Grundformel:**
P/E = Aktiekurs / Vinst per aktie (EPS)

**Varianter:**

**1. P/E Trailing (LTM):**
- Baserad på senaste 12 månaders vinst
- Faktisk data, ingen prognos
- Problem: Kan inkludera engångsposter

**2. P/E Forward (NTM eller FY):**
- Baserad på prognosticerad vinst
- Standard bland professionella
- Problem: Prognosrisk

**3. Shiller CAPE (Cyclically Adjusted P/E):**
- 10 års genomsnittlig real vinst
- Justerar för konjunkturcykler
- Bra för marknadsvärdering, mindre för enskilda bolag

**Svenska P/E-nivåer (2024):**

| Kategori | P/E NTM |
|----------|---------|
| OMXS30 | 15-17x |
| Tillväxtbolag | 25-40x |
| Stabila industri | 15-20x |
| Banker | 8-12x |
| Cykliska (lågkonjunktur) | 20-30x |
| Cykliska (högkonjunktur) | 8-12x |

**Fördelar med P/E:**
- Intuitivt: "Antal år att tjäna igen investeringen"
- Jämförbart över sektorer (med försiktighet)
- Historisk data lättillgänglig

**Nackdelar:**
- Påverkas av skuldsättning (ej kapitalstruktur-neutralt)
- Meningslöst för förlustbolag
- Vinst kan manipuleras (earnings management)
- Olika redovisningsprinciper

**PEG-ratio (tillväxtjusterad P/E):**
PEG = P/E / Vinsttillväxt (%)
- PEG < 1: Potentiellt undervärderad
- PEG = 1: Fair
- PEG > 2: Potentiellt övervärderad

Exempel: P/E 25x, tillväxt 20% → PEG = 1.25""",
        "tags": ["P/E", "PEG", "Shiller CAPE", "EPS", "trailing", "forward"],
        "related_metrics": ["EPS", "Aktiekurs", "Vinsttillväxt"],
        "source": "CFA/Standard & Poor's"
    },
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "Konstruktion av peer group för svenska bolag",
        "content": """En välkonstruerad peer group är avgörande för relativvärdering.

**Urvalskriterier (prioritetsordning):**

1. **Affärsmodell/Verksamhet:**
   - Samma produkter/tjänster
   - Liknande kundgrupper
   - Jämförbar marknadsposition

2. **Geografi:**
   - Nordiska peers primärt
   - Europeiska peers sekundärt
   - Globala peers för unika bolag

3. **Storlek (börsvärde):**
   - ±3x för jämförbarhet
   - Small-cap vs large-cap premium

4. **Tillväxt och lönsamhet:**
   - Liknande tillväxttakt (±5pp)
   - Jämförbar marginalstruktur

5. **Kapitalintensitet:**
   - Liknande ROIC/ROE
   - Jämförbar capex/sales

**Typiska svenska peer groups:**

**Industrikonglomerat:**
Atlas Copco, Sandvik, Alfa Laval, SKF

**Serieförvärvare:**
Lifco, Indutrade, Addtech, Lagercrantz, Sdiptech

**Fastighet:**
Castellum, Fabege, Wihlborgs, Balder, Sagax

**Fintech:**
Klarna, Qliro, Collector (nu Collector Bank)

**Tech/Mjukvara:**
Sinch, Storytel, Fortnox, Lime Technologies

**Antal peers:**
- Minimum: 3-4 bolag
- Optimalt: 6-10 bolag
- Maximum: 15 (annars för heterogent)

**Hantering av outliers:**
- Ta bort extremvärden (>2 standardavvikelser)
- Använd median snarare än genomsnitt
- Förstå VARFÖR outliers avviker

**Dokumentera alltid:**
- Urvalskriterier
- Exkluderade bolag och varför
- Datakällor och datum""",
        "tags": ["peer group", "relativvärdering", "comparables", "branschjämförelse"],
        "related_metrics": ["EV/EBITDA", "P/E", "Tillväxt", "Marginal"],
        "source": "Goldman Sachs/Morgan Stanley"
    },
    {
        "domain": "värdering",
        "category": "multiplar",
        "title": "EV/Sales och dess användning",
        "content": """EV/Sales (eller P/S för aktiepris/försäljning) används när vinst saknas eller är volatil.

**Formel:**
EV/Sales = Enterprise Value / Nettoomsättning

**När använda EV/Sales:**

1. **Förlustbolag:**
   - Tidiga tillväxtbolag utan vinst
   - Turnaround-situationer

2. **Högtillväxtbolag:**
   - Marknaden prissätter framtida intäkter
   - Tech, biotech, SaaS

3. **Cykliska bottnar:**
   - När EBITDA är tillfälligt depresserad
   - Undvik att köpa "billig" P/E i lågkonjunktur

**Svenska exempel:**

| Bolagstyp | EV/Sales |
|-----------|----------|
| SaaS (Fortnox) | 10-20x |
| Industri | 1-3x |
| Retail | 0.3-1x |
| Biotech (pre-revenue) | N/A |

**Koppling till marginal:**
EV/Sales = EV/EBITDA × EBITDA-marginal

Exempel:
- EV/EBITDA = 12x
- EBITDA-marginal = 20%
- EV/Sales = 12 × 0.20 = 2.4x

**Marginalexpansion-analys:**
Om bolaget kan förbättra marginal:
- Nuvarande EV/Sales: 3x
- Nuvarande marginal: 10% → EV/EBITDA = 30x (dyrt!)
- Om marginal går till 20% → Implicit EV/EBITDA = 15x (rimligare)

**Varningar:**
- Höga EV/Sales kräver marginalexpansion
- Jämför bara inom samma sektor
- Kontrollera kapitalintensitet (två bolag med samma omsättning kan ha mycket olika värde beroende på capex-behov)

**Rule of 40 (SaaS):**
Tillväxt (%) + EBITDA-marginal (%) > 40
→ Motiverar högre EV/Sales""",
        "tags": ["EV/Sales", "P/S", "förlustbolag", "tillväxt", "marginal"],
        "related_metrics": ["Nettoomsättning", "EBITDA-marginal", "Tillväxt"],
        "source": "CFA/SaaS Capital"
    },

    # -----------------------------------------
    # SEKTORSPECIFIK VÄRDERING
    # -----------------------------------------
    {
        "domain": "värdering",
        "category": "sektor_SaaS",
        "title": "SaaS-värdering: ARR-multiplar och Rule of 40",
        "content": """Software-as-a-Service (SaaS) värderas med specialmultiplar baserade på återkommande intäkter.

**Nyckelbegrepp:**

**ARR (Annual Recurring Revenue):**
- Årlig återkommande intäkt
- Normaliserad MRR × 12
- Exkluderar engångsintäkter

**MRR (Monthly Recurring Revenue):**
- Månatlig återkommande intäkt
- Grunden för ARR

**NRR (Net Revenue Retention):**
- Intäkter från befintliga kunder YoY
- Inkluderar churn, expansion, nedgradering
- >100% = expansion överstiger churn

**Svenska SaaS-multiplar (2024):**

| Kvalitet | EV/ARR |
|----------|--------|
| Premium (>30% tillväxt, >20% FCF) | 12-20x |
| Bra (20-30% tillväxt) | 8-12x |
| Moget (<20% tillväxt) | 5-8x |

**Rule of 40:**
Tillväxt (%) + FCF-marginal (%) > 40

Exempel:
- 25% tillväxt + 20% FCF-marginal = 45 → Bra
- 40% tillväxt + 5% FCF-marginal = 45 → Bra (tillväxtfokus)
- 10% tillväxt + 15% FCF-marginal = 25 → Under par

**LTV/CAC (Unit Economics):**
- LTV (Lifetime Value): Kundens totala värde
- CAC (Customer Acquisition Cost): Kostnad att värva kund
- Målvärde: LTV/CAC > 3x

**Fortnox-exempel (svenska SaaS):**
- ARR: ca 2,000 MSEK (2024)
- EV: ca 30,000 MSEK
- EV/ARR: ~15x
- Tillväxt: ~25%
- Rule of 40: ca 50 (bra)

**Magic Number:**
= (ARR Q4 - ARR Q1) × 4 / S&M-kostnad Q1-Q4
>0.75 = Effektiv kundanskaffning""",
        "tags": ["SaaS", "ARR", "Rule of 40", "NRR", "LTV/CAC"],
        "related_metrics": ["ARR", "MRR", "Churn", "CAC"],
        "source": "Bessemer Venture Partners"
    },
    {
        "domain": "värdering",
        "category": "sektor_bank",
        "title": "Bankvärdering: P/B, ROE-regression och P/TBV",
        "content": """Banker värderas annorlunda än industribolag pga regulatorisk miljö och balansräkningsfokus.

**Varför inte EV/EBITDA för banker:**
- Räntenetto är inte "EBITDA"
- Skulder är rörelserelaterade (inlåning), ej finansiering
- Kapitalstruktur styrs av regelverk

**Primära multiplar:**

**P/B (Price/Book):**
- Aktiekurs / Bokfört eget kapital per aktie
- Standard för banker
- P/B = 1.0x → Prissatt till bokfört värde

**P/TBV (Price/Tangible Book Value):**
- Exkluderar immateriella tillgångar
- Renare mått på "hårt" kapital
- P/TBV = Börsvärde / (Eget kapital - Goodwill - Immateriella)

**Svenska storbanker (2024 typiska värden):**

| Bank | P/B | P/TBV | ROE |
|------|-----|-------|-----|
| Handelsbanken | 1.1x | 1.2x | 11% |
| SEB | 1.2x | 1.4x | 13% |
| Nordea | 1.0x | 1.1x | 12% |
| Swedbank | 1.1x | 1.3x | 14% |

**ROE-regression:**
Banker med högre ROE motiverar högre P/B.

Teoretiskt: P/B = (ROE - g) / (CoE - g)

Om ROE = CoE → P/B = 1.0x
Om ROE > CoE → P/B > 1.0x

**Regression i praktiken:**
Plotta P/B mot ROE för peers → Hitta mönster
En bank med ROE 15% och P/B 0.9x kan vara undervärderad om regression säger 1.1x

**Dividend yield:**
Viktig för bankaktier pga stabila utdelningar
Svenska banker: Ofta 4-7% direktavkastning

**CET1-kapital:**
- Core Equity Tier 1 ratio
- Regulatoriskt krav: >4.5% + buffertar
- Svenska banker: Ofta >16%""",
        "tags": ["bank", "P/B", "P/TBV", "ROE", "CET1"],
        "related_metrics": ["ROE", "Eget kapital", "CET1"],
        "source": "ECB/Riksbanken"
    },
    {
        "domain": "värdering",
        "category": "sektor_fastighet",
        "title": "Fastighetsbolagsvärdering: NAV, yield och P/NAV",
        "content": """Fastighetsbolag värderas primärt utifrån tillgångsvärden (NAV) och direktavkastning.

**Substansvärde (NAV - Net Asset Value):**
NAV = Verkligt värde fastigheter - Skulder - Uppskjuten skatt

NAV per aktie = NAV / Antal aktier

**EPRA NAV-varianter:**

| Mått | Definition |
|------|------------|
| EPRA NRV | NAV justerat för verklig skuld och skatt |
| EPRA NTA | Tangible assets, exkl. goodwill |
| EPRA NDV | Disposal value, vid försäljning |

**P/NAV (Premium/Rabatt):**
P/NAV = Aktiekurs / NAV per aktie

- P/NAV > 1.0: Premium (marknaden tror på värdeökning)
- P/NAV < 1.0: Rabatt (skepsis eller likviditetsbrist)

**Svenska fastighetsbolag (2024):**

| Bolag | P/NAV | Segment |
|-------|-------|---------|
| Castellum | 0.7x | Kontor/logistik |
| Balder | 0.6x | Diversifierat |
| Sagax | 0.9x | Industri/logistik |
| Wihlborgs | 0.6x | Kontor Öresund |

**Direktavkastning (Yield):**
Yield = Driftnetto / Fastighetsvärde

Svenska yields (2024):
- Prime kontor Stockholm: 4.0-4.5%
- Logistik: 4.5-5.5%
- Bostäder: 3.0-3.5%
- Handel: 5.5-7.0%

**Implicit yield-värdering:**
Om bolaget har driftnetto 500 MSEK och implicit yield är 5%:
Fastighetsvärde = 500 / 0.05 = 10,000 MSEK

**Förvaltningsresultat:**
- Driftnetto - Centraladministration - Räntor
- Bolagets kassaflöde före värdeförändringar
- Ofta primärt resultatmått i branschen

**LTV (Loan-to-Value):**
- Skulder / Fastighetsvärde
- Svenska bolag: Ofta 40-55%
- Bankcovenants: Max 60-65%""",
        "tags": ["fastighet", "NAV", "P/NAV", "yield", "EPRA", "LTV"],
        "related_metrics": ["NAV", "Driftnetto", "Förvaltningsresultat", "LTV"],
        "source": "EPRA/Svenska Fastighetsanalytiker"
    },
    {
        "domain": "värdering",
        "category": "sektor_investmentbolag",
        "title": "Svenska investmentbolag: Substansrabatt och värdering",
        "content": """Svenska investmentbolag värderas utifrån substansvärde med typisk rabatt.

**Substansvärde (NAV) för investmentbolag:**

**Noterade innehav:**
= Antal aktier × Börskurs

**Onoterade innehav:**
= Senaste transaktion, värdering eller bokfört värde
- Ofta svårt att värdera
- Patricia Industries (Investor), EQT-fonder

**Total NAV:**
= Noterade innehav + Onoterade + Kassa - Skulder

**Svenska investmentbolag (2024):**

| Bolag | Typ | Typisk rabatt |
|-------|-----|---------------|
| Investor | Diversifierat | 5-15% |
| Industrivärden | Koncentrerat | 10-20% |
| Lundbergs | Fastighet + industri | 15-25% |
| Latour | Industri | 10-20% |
| Kinnevik | Tillväxt/tech | 20-40% |

**Varför substansrabatt?**

1. **Holdingbolagsstruktur:**
   - Dubbelbeskattning (bolag + ägare)
   - Administrativa kostnader

2. **Likviditet:**
   - Investmentbolagsaktien vs direktägande
   - Svårare att sälja stor post

3. **Diversifiering:**
   - Investerare vill välja själva
   - Konglomeratrabatt

4. **Kontrollpremie redan i NAV:**
   - Noterade innehav prissatta utan kontrollpremie
   - Investmentbolaget äger kontrollerande poster

**Värdering av onoterade:**
- Multipelvärdering mot peers
- Transaktionsvärden
- DCF
- Sum-of-the-parts (SOTP)

**Värderingsexempel (Investor):**

| Innehav | Värde (MSEK) |
|---------|-------------|
| Atlas Copco (22%) | 180,000 |
| ABB (7%) | 40,000 |
| Övriga noterade | 100,000 |
| Patricia Industries | 80,000 |
| Kassa - Skuld | 10,000 |
| **Total NAV** | **410,000** |
| Börsvärde | 360,000 |
| **Rabatt** | **12%** |

**Triggers för rabattförändring:**
- Ändrad utdelningspolicy
- Förvärv/avyttringar
- Strukturella förändringar
- Makroklimat (risk-on/risk-off)""",
        "tags": ["investmentbolag", "substansrabatt", "NAV", "holding", "Patricia", "noterat/onoterat"],
        "related_metrics": ["NAV", "Substansrabatt", "Totalavkastning"],
        "source": "Svenska investmentbolagsstandard"
    },
    {
        "domain": "värdering",
        "category": "sektor_investmentbolag",
        "title": "Investmentbolag: SOTP-värdering och innehavsanalys",
        "content": """Sum-of-the-Parts (SOTP) är standardmetod för att värdera investmentbolag.

**SOTP-struktur:**

1. **Värdera varje innehav separat**
2. **Summera till brutto NAV**
3. **Dra av skulder och kostnader**
4. **Applicera rimlig rabatt**

**Värdering av noterade innehav:**
- Marknadsvärde (aktiekurs × antal aktier)
- Eventuellt kontrollpremie för större poster (5-20%)

**Värdering av onoterade innehav:**

**Metod 1: Peer-multiplar**
- EV/EBITDA jämfört med noterade peers
- Ofta 10-20% illikviditetsrabatt

**Metod 2: Transaktionsmultiplar**
- Senaste förvärv/försäljningar i sektorn
- PE-bolags transaktioner som referens

**Metod 3: DCF**
- För mogna onoterade med stabil kassaflöde

**Metod 4: Bokfört värde**
- Konservativt, ofta golv

**Investorexempel - Patricia Industries:**

| Bolag | Värderingsmetod | Värde (MSEK) |
|-------|-----------------|--------------|
| Mölnlycke | EV/EBITDA 15x | 45,000 |
| Permobil | EV/EBITDA 12x | 15,000 |
| Laborie | EV/EBITDA 14x | 8,000 |
| Övriga | Bokfört | 12,000 |
| **Totalt Patricia** | | **80,000** |

**Kostnadsavdrag:**
- Förvaltningskostnader: 0.2-0.5% av NAV
- Diskontera evigt: Kostnad / Avkastningskrav

Exempel: 500 MSEK kostnad / 8% = 6,250 MSEK avdrag

**Skattejustering:**
- Latent skatt på övervärden (onoterade)
- Realisationsvinstskatt vid försäljning
- Ofta 5-10% avdrag

**Target-rabatt:**
- Historisk genomsnitt för bolaget
- Justerad för aktuella förhållanden
- Bull case: Rabatten krymper
- Bear case: Rabatten ökar

**Kurspotential:**
= (NAV × (1 - Target-rabatt)) / Aktiekurs - 1

Exempel:
NAV 500 SEK, Target-rabatt 10%, Aktiekurs 400 SEK
Target = 500 × 0.90 = 450 SEK
Potential = 450/400 - 1 = +12.5%""",
        "tags": ["SOTP", "Sum-of-the-Parts", "investmentbolag", "onoterat", "Patricia"],
        "related_metrics": ["NAV", "Substansrabatt", "EV/EBITDA"],
        "source": "Svenska investmentbolagsstandard"
    },
    {
        "domain": "värdering",
        "category": "sektor_cyklisk",
        "title": "Värdering av cykliska bolag: Normalisering och peak/trough",
        "content": """Cykliska bolag kräver särskild värderingsmetodik pga resultatvolatilitet.

**Svenska cykliska sektorer:**
- Stål (SSAB)
- Gruv (Boliden, LKAB)
- Skog/Papper (SCA, Holmen)
- Verkstad (delvis)
- Bygg (NCC, Peab, Skanska)

**Problemet med vanliga multiplar:**
- P/E i lågkonjunktur: Artificiellt högt (låg vinst)
- P/E i högkonjunktur: Artificiellt lågt (hög vinst)
→ "Köp dyrt, sälj billigt" om du följer P/E blint

**Metod 1: Mid-cycle earnings**
- Estimera "normalt" resultat mitt i cykeln
- Genomsnitt över 5-10 år (full cykel)
- Justera för strukturella förändringar

Normaliserad P/E = Kurs / Mid-cycle EPS

**Metod 2: Peak/Trough-värdering**
- Värdera på lågpunktsresultat (trough)
- Värdera på högpunktsresultat (peak)
- Triangulera med var i cykeln vi är

**Metod 3: Replacement cost / Asset value**
- Vad kostar det att bygga ny kapacitet?
- EV/ton kapacitet (stål, papper)
- Relevant vid extrema värderingar

**SSAB-exempel:**

| Cykelposition | EBITDA | EV/EBITDA | Kommentar |
|---------------|--------|-----------|-----------|
| Peak (2022) | 25,000 | 3x | "Billigt" - men peak |
| Mid-cycle | 12,000 | 6x | Normaliserat |
| Trough | 5,000 | 15x | "Dyrt" - men botten |

**DCF för cykliska:**
- Modellera explicit cykel (7-10 år)
- Låt terminalen reflektera mid-cycle
- Känslig för cykelposition

**Triggers att bevaka:**
- Kapacitetsutnyttjande
- Lagernivåer i värdekedjan
- Priser på insatsvaror
- Orderingång/book-to-bill

**Tumregel:**
"Köp när P/E är högt (botten), sälj när P/E är lågt (topp)""",
        "tags": ["cyklisk", "normalisering", "mid-cycle", "peak", "trough", "SSAB"],
        "related_metrics": ["EBITDA", "P/E", "Kapacitetsutnyttjande"],
        "source": "CFA/Cyklisk industri"
    },

    # -----------------------------------------
    # TRIANGULERING OCH SANITY CHECKS
    # -----------------------------------------
    {
        "domain": "värdering",
        "category": "triangulering",
        "title": "Triangulering av värdering: Kombinera metoder",
        "content": """Ingen enskild värderingsmetod är perfekt. Triangulering ökar tillförlitligheten.

**Principen:**
Använd minst 2-3 oberoende metoder och jämför resultaten.

**Triangulerings-setup:**

| Metod | Värde/aktie | Vikt |
|-------|-------------|------|
| DCF | 150 SEK | 40% |
| EV/EBITDA (peers) | 140 SEK | 30% |
| P/E (peers) | 130 SEK | 20% |
| Transaktionsmultiplar | 160 SEK | 10% |
| **Viktat snitt** | **144 SEK** | 100% |

**Metodval per bolagstyp:**

**Moget industribolag:**
1. DCF (primär)
2. EV/EBITDA peers
3. P/E peers

**Tillväxtbolag:**
1. EV/Sales eller EV/ARR
2. DCF med scenarier
3. Transaktionsjämförelser

**Fastighetsbolag:**
1. P/NAV
2. Yield-värdering
3. DCF på kassaflöde

**Investmentbolag:**
1. SOTP-NAV
2. Historisk rabatt
3. Sum av del-värderingar

**Bank:**
1. P/B och ROE-regression
2. P/TBV
3. Dividend discount model

**När metoderna divergerar:**
- DCF > Peers: Marknaden undervärderar, eller DCF-antaganden för optimistiska
- DCF < Peers: Marknaden övervärderar, eller DCF-antaganden för konservativa

**Sanity checks:**
1. Implicit tillväxt i multipel vs realistiskt?
2. Implicit avkastning på kapital?
3. Jämförelse med transaktioner i sektorn?
4. Historisk värdering av bolaget?

**Dokumentation:**
Ange alltid:
- Metoder använda
- Viktning och motivering
- Key assumptions
- Känslighetsintervall""",
        "tags": ["triangulering", "värdering", "metodkombination", "sanity check"],
        "related_metrics": ["DCF", "EV/EBITDA", "P/E", "NAV"],
        "source": "Best practice/CFA"
    },
    {
        "domain": "värdering",
        "category": "triangulering",
        "title": "Värdering: Vanliga fallgropar och hur undvika dem",
        "content": """Systematiska fel vid värdering kan leda till stora misstag.

**Fallgrop 1: Cirkelreferens i WACC**
- Problem: WACC beror på E/V, som beror på värderingen
- Lösning: Iterera tills konvergens, eller använd målkapitalstruktur

**Fallgrop 2: Inkonsekvent inflationsantagande**
- Problem: Nominell WACC med reala kassaflöden (eller tvärtom)
- Lösning: Var konsekvent - nominellt genomgående är standard

**Fallgrop 3: Dubbel-counting av tillväxt**
- Problem: Hög tillväxt i explicit period OCH hög terminal growth
- Lösning: Terminal growth max = inflation + real BNP-tillväxt

**Fallgrop 4: Glömmer rörelsekapital**
- Problem: Tillväxt kräver mer rörelsekapital
- Lösning: Modellera NWC som % av omsättning

**Fallgrop 5: Jämför fel multiplar**
- Problem: EV/EBITDA för bolag A vs P/E för bolag B
- Lösning: Alltid samma multipel inom peer group

**Fallgrop 6: Ignorerar kapitalintensitet**
- Problem: Två bolag med samma EBITDA men olika capex-behov
- Lösning: Använd EV/EBIT eller EV/FCF för capex-tunga

**Fallgrop 7: Backwards-looking i cykliska**
- Problem: Trailing multiplar på topp/botten av cykel
- Lösning: Normaliserade multiplar, mid-cycle analys

**Fallgrop 8: Fel behandling av leasing**
- Problem: Inkonsekvent EBITDA vs EV
- Lösning: Om EBITDA pre-IFRS16 → EV exkl. leasing

**Fallgrop 9: Syntetisk optioner och utspädning**
- Problem: Glömmer optionsprogram
- Lösning: Använd utspätt aktieantal, treasury stock method

**Fallgrop 10: Överdrivet precis värdering**
- Problem: "Värdet är 147.35 SEK"
- Lösning: Ange intervall, t.ex. "140-155 SEK"

**Checklista före slutsats:**
□ Är alla antaganden dokumenterade?
□ Är WACC rimlig jämfört med peers?
□ Ger implicit multipel mening?
□ Vad händer vid +/- 10% på key drivers?
□ Är terminalvärde < 75% av totalt?""",
        "tags": ["fallgropar", "värdering", "misstag", "checklista"],
        "related_metrics": ["WACC", "EBITDA", "Capex", "NWC"],
        "source": "CFA/McKinsey/Praktisk erfarenhet"
    },
]


def main():
    """Huvudfunktion för att populera databasen."""
    print("=" * 60)
    print("POPULERAR KNOWLEDGE-DATABASEN MED VÄRDERINGSMETODIK")
    print("=" * 60)

    success_count = 0
    error_count = 0

    for i, item in enumerate(VALUATION_KNOWLEDGE, 1):
        print(f"\n[{i}/{len(VALUATION_KNOWLEDGE)}] {item['title'][:50]}...")

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

        # Paus för att undvika rate limiting
        time.sleep(0.5)

    print("\n" + "=" * 60)
    print(f"KLART! Lyckade: {success_count}, Fel: {error_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
