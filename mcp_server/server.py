#!/usr/bin/env python3
"""
MCP Server för Rapport Extraktor

Exponerar finansiell data från Supabase för Claude Desktop.
Helt fristående från rapport_extraktor – bara läser data.

Verktyg:
- list_companies: Lista alla bolag
- get_periods: Visa tillgängliga perioder för ett bolag
- get_financials: Hämta finansiell data (resultat, balans, kassaflöde)
- get_kpis: Hämta nyckeltal från report_tables
- get_sections: Hämta textsektioner (VD-kommentar, etc.)
- search_sections: Sök i textsektioner (med embedding-stöd)
- compare_periods: Jämför två perioder
- get_charts: Hämta grafer med axelinfo och datapunkter
"""

import json
import os
import sys
from typing import Any

import requests
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Prompt, PromptMessage, PromptArgument, GetPromptResult
from supabase import create_client, Client

# Ladda miljövariabler
load_dotenv()

# Voyage API för embeddings
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
VOYAGE_MODEL = "voyage-4"

# Supabase-klient
_client: Client | None = None


def get_client() -> Client:
    """Hämta eller skapa Supabase-klient."""
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL och SUPABASE_KEY måste vara satta i .env")
        _client = create_client(url, key)
    return _client


# =============================================================================
# DATABASFUNKTIONER
# =============================================================================

def db_list_companies() -> list[dict]:
    """Lista alla bolag med antal perioder."""
    client = get_client()
    
    # Hämta bolag
    companies = client.table("companies").select("id, name, slug").order("name").execute()
    
    result = []
    for c in companies.data:
        # Räkna perioder
        periods = client.table("periods").select("id").eq("company_id", c["id"]).execute()
        result.append({
            "name": c["name"],
            "slug": c["slug"],
            "periods_count": len(periods.data)
        })
    
    return result


def db_get_periods(company_slug: str) -> list[dict]:
    """Hämta alla perioder för ett bolag."""
    client = get_client()

    # Hitta bolag
    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        # Försök med namn istället
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return []

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    # Hämta perioder med source_file och pdf_hash
    periods = client.table("periods").select(
        "quarter, year, valuta, source_file, pdf_hash, created_at"
    ).eq("company_id", company_id).order("year").order("quarter").execute()

    return [{
        "company": company_name,
        "period": f"Q{p['quarter']} {p['year']}",
        "quarter": p["quarter"],
        "year": p["year"],
        "valuta": p["valuta"],
        "source_file": p.get("source_file"),
        "pdf_hash": p.get("pdf_hash"),
        "created_at": p["created_at"]
    } for p in periods.data]


def db_get_financials(company_slug: str, period: str | None = None, statement_type: str | None = None) -> dict:
    """
    Hämta finansiell data.
    
    Args:
        company_slug: Bolagets slug eller namn
        period: T.ex. "Q3 2024" (om None, hämta senaste)
        statement_type: income_statement, balance_sheet, cash_flow, eller None för alla
    """
    client = get_client()
    
    # Hitta bolag
    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()
    
    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}
    
    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]
    
    # Hitta period (inkluderar source_file, pdf_hash och language)
    if period:
        # Parsa "Q3 2024"
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select(
                "id, quarter, year, valuta, language, source_file, pdf_hash"
            ).eq("company_id", company_id).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}. Använd t.ex. 'Q3 2024'"}
    else:
        # Senaste period
        period_row = client.table("periods").select(
            "id, quarter, year, valuta, language, source_file, pdf_hash"
        ).eq("company_id", company_id).order("year", desc=True).order("quarter", desc=True).limit(1).execute()

    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}

    p = period_row.data[0]
    period_id = p["id"]
    period_str = f"Q{p['quarter']} {p['year']}"
    valuta = p.get("valuta", "TSEK")
    language = p.get("language", "sv")

    result = {
        "company": company_name,
        "period": period_str,
        "valuta": valuta,
        "language": language,  # sv, no, eller en
        "source": {
            "file": p.get("source_file"),
            "pdf_hash": p.get("pdf_hash"),
            "language": language
        },
        "tables": {}
    }
    
    # Kolla om det finns data i report_tables (nytt format)
    query = client.table("report_tables").select("*").eq("period_id", period_id)
    if statement_type:
        query = query.eq("table_type", statement_type)
    
    tables = query.order("page_number").execute()
    
    if tables.data:
        # Nytt format
        for t in tables.data:
            table_type = t["table_type"] or "other"
            if table_type not in result["tables"]:
                result["tables"][table_type] = []
            
            result["tables"][table_type].append({
                "title": t["title"],
                "columns": t["columns"],
                "rows": t["rows"]
            })
    else:
        # Legacy format - financial_data
        query = client.table("financial_data").select("*").eq("period_id", period_id)
        if statement_type:
            # Mappa till legacy-namn
            legacy_map = {
                "income_statement": "resultatrakning",
                "balance_sheet": "balansrakning",
                "cash_flow": "kassaflodesanalys"
            }
            legacy_type = legacy_map.get(statement_type, statement_type)
            query = query.eq("statement_type", legacy_type)
        
        fin_data = query.order("row_order").execute()
        
        for row in fin_data.data:
            st = row["statement_type"]
            if st not in result["tables"]:
                result["tables"][st] = []
            
            result["tables"][st].append({
                "row": row["row_name"],
                "value": row["value"],
                "type": row.get("row_type")
            })
    
    return result


def db_get_kpis(company_slug: str, period: str | None = None) -> dict:
    """Hämta nyckeltal (KPIs) från report_tables."""
    client = get_client()

    # Hitta bolag
    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    # Hitta period (inkluderar source_file och pdf_hash direkt)
    if period:
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select(
                "id, quarter, year, valuta, source_file, pdf_hash"
            ).eq("company_id", company_id).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}. Använd t.ex. 'Q3 2024'"}
    else:
        period_row = client.table("periods").select(
            "id, quarter, year, valuta, source_file, pdf_hash"
        ).eq("company_id", company_id).order("year", desc=True).order("quarter", desc=True).limit(1).execute()

    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}

    p = period_row.data[0]
    period_id = p["id"]
    period_str = f"Q{p['quarter']} {p['year']}"
    valuta = p.get("valuta", "TSEK")

    # Hämta KPI-tabeller från report_tables
    kpis = client.table("report_tables").select("*").eq(
        "period_id", period_id
    ).eq("table_type", "kpi").order("page_number").execute()

    return {
        "company": company_name,
        "period": period_str,
        "valuta": valuta,
        "source": {
            "file": p.get("source_file"),
            "pdf_hash": p.get("pdf_hash")
        },
        "kpi_tables": [{
            "title": k["title"],
            "page": k["page_number"],
            "columns": k["columns"],
            "rows": k["rows"]
        } for k in kpis.data]
    }


def db_get_sections(company_slug: str, period: str | None = None, section_type: str | None = None) -> dict:
    """Hämta textsektioner (VD-kommentar, etc.)."""
    client = get_client()
    
    # Hitta bolag
    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()
    
    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}
    
    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]
    
    # Hitta period (inkluderar source_file och pdf_hash direkt)
    if period:
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select(
                "id, quarter, year, source_file, pdf_hash"
            ).eq("company_id", company_id).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}"}
    else:
        period_row = client.table("periods").select(
            "id, quarter, year, source_file, pdf_hash"
        ).eq("company_id", company_id).order("year", desc=True).order("quarter", desc=True).limit(1).execute()

    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}

    p = period_row.data[0]
    period_id = p["id"]
    period_str = f"Q{p['quarter']} {p['year']}"

    # Hämta sektioner
    query = client.table("sections").select("title, section_type, page_number, content").eq("period_id", period_id)
    if section_type:
        query = query.eq("section_type", section_type)

    sections = query.order("page_number").execute()

    return {
        "company": company_name,
        "period": period_str,
        "source": {
            "file": p.get("source_file"),
            "pdf_hash": p.get("pdf_hash")
        },
        "sections": [{
            "title": s["title"],
            "type": s["section_type"],
            "page": s["page_number"],
            "content": s["content"][:2000] + "..." if len(s["content"]) > 2000 else s["content"]
        } for s in sections.data]
    }


def get_query_embedding(text: str) -> list[float] | None:
    """Hämta embedding för en sökfråga via Voyage AI."""
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
                "model": VOYAGE_MODEL,
                "input": [text],
                "input_type": "query"  # Viktigt: "query" för sökfrågor
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()["data"][0]["embedding"]
    except Exception:
        pass
    return None


def db_search_sections(query: str, company_slug: str | None = None, use_embedding: bool = False, use_hybrid: bool = True) -> list[dict]:
    """
    Sök i textsektioner.

    Args:
        query: Sökterm
        company_slug: Begränsa till ett bolag (valfritt)
        use_embedding: Använd semantisk sökning med embeddings
        use_hybrid: Använd hybrid sökning (text + semantisk) - default True
    """
    client = get_client()

    # Hybrid-sökning (kombinerar text + semantisk)
    if use_hybrid or use_embedding:
        try:
            query_embedding = get_query_embedding(query)
            if query_embedding:
                # Försök hybrid först
                if use_hybrid:
                    try:
                        result = client.rpc("hybrid_search_sections", {
                            "query_text": query,
                            "query_embedding": query_embedding,
                            "match_count": 10,
                            "company_filter": company_slug,
                            "text_weight": 0.3,
                            "semantic_weight": 0.7
                        }).execute()

                        if result.data:
                            return [{
                                "company": r["company_name"],
                                "period": f"Q{r['quarter']} {r['year']}",
                                "section": r["title"],
                                "type": r["section_type"],
                                "page": r.get("page_number"),
                                "excerpt": r["content"][:300] + "..." if len(r["content"]) > 300 else r["content"],
                                "score": round(r.get("combined_score", 0), 3),
                                "search_type": "hybrid",
                                "source": {
                                    "file": r.get("source_file"),
                                    "period": f"Q{r['quarter']} {r['year']}"
                                }
                            } for r in result.data]
                    except Exception:
                        pass  # Fallback till ren semantisk sökning

                # Fallback till ren semantisk sökning
                result = client.rpc("match_sections", {
                    "query_embedding": query_embedding,
                    "match_count": 10,
                    "company_filter": company_slug
                }).execute()

                if result.data:
                    return [{
                        "company": r["company_name"],
                        "period": f"Q{r['quarter']} {r['year']}",
                        "section": r["title"],
                        "type": r["section_type"],
                        "page": r.get("page_number"),
                        "excerpt": r["content"][:300] + "..." if len(r["content"]) > 300 else r["content"],
                        "score": round(r.get("similarity", 0), 3),
                        "search_type": "semantic",
                        "source": {
                            "file": r.get("source_file"),
                            "period": f"Q{r['quarter']} {r['year']}"
                        }
                    } for r in result.data]
        except Exception:
            # Fallback till textsökning
            pass

    # Textsökning (fallback)
    if company_slug:
        # Hitta bolag först
        company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
        if not company.data:
            company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

        if not company.data:
            return []

        company_id = company.data[0]["id"]

        # Hämta period-IDs för bolaget
        periods = client.table("periods").select("id").eq("company_id", company_id).execute()
        period_ids = [p["id"] for p in periods.data]

        if not period_ids:
            return []

        # Sök i sektioner för dessa perioder
        sections = client.table("sections").select(
            "title, section_type, content, period_id, page_number"
        ).in_("period_id", period_ids).ilike("content", f"%{query}%").limit(10).execute()
    else:
        # Sök i alla sektioner
        sections = client.table("sections").select(
            "title, section_type, content, period_id, page_number"
        ).ilike("content", f"%{query}%").limit(10).execute()

    results = []
    for s in sections.data:
        # Hämta period-info med source_file
        period = client.table("periods").select(
            "quarter, year, company_id, source_file"
        ).eq("id", s["period_id"]).execute()

        if period.data:
            p = period.data[0]
            company = client.table("companies").select("name").eq("id", p["company_id"]).execute()
            company_name = company.data[0]["name"] if company.data else "Okänt"

            # Hitta relevant textutdrag
            content = s["content"]
            query_lower = query.lower()
            content_lower = content.lower()
            pos = content_lower.find(query_lower)

            if pos >= 0:
                start = max(0, pos - 100)
                end = min(len(content), pos + len(query) + 100)
                excerpt = "..." + content[start:end] + "..."
            else:
                excerpt = content[:200] + "..."

            results.append({
                "company": company_name,
                "period": f"Q{p['quarter']} {p['year']}",
                "section": s["title"],
                "type": s["section_type"],
                "page": s.get("page_number"),
                "excerpt": excerpt,
                "search_type": "text",
                "source": {
                    "file": p.get("source_file"),
                    "period": f"Q{p['quarter']} {p['year']}"
                }
            })

    return results


def db_compare_periods(company_slug: str, period1: str, period2: str, statement_type: str = "income_statement") -> dict:
    """Jämför två perioder för samma bolag."""
    data1 = db_get_financials(company_slug, period1, statement_type)
    data2 = db_get_financials(company_slug, period2, statement_type)

    if "error" in data1:
        return data1
    if "error" in data2:
        return data2

    return {
        "company": data1["company"],
        "comparison": {
            period1: data1["tables"],
            period2: data2["tables"]
        }
    }


# Synonymer för label_en-matchning (STRIKT - endast definitivt ekvivalenta termer)
# Fallback om DB inte finns. Bättre att missa matchning än visa felaktig jämförelse.
LABEL_EN_SYNONYMS = {
    # ============ RESULTATRÄKNING ============
    # OBS: "revenue", "sales", "turnover" är INTE synonymer - kan betyda olika saker
    "net sales": ["net sales", "net revenue"],
    # Kostnader
    "cost of goods sold": ["cost of goods sold", "cogs", "cost of sales"],
    "personnel expenses": ["personnel expenses", "personnel costs", "employee expenses", "staff costs"],
    "other operating expenses": ["other operating expenses", "other operating costs"],
    "selling expenses": ["selling expenses"],
    "administrative expenses": ["administrative expenses", "admin expenses"],
    # Avskrivningar - OBS: "depreciation" och "amortization" ensamma är OLIKA
    "depreciation and amortization": ["depreciation and amortization", "d&a"],
    # Resultat - OBS: "EBIT" separerat från "operating profit" (kan skilja vid exceptionella poster)
    "gross profit": ["gross profit", "gross income"],
    "operating profit": ["operating profit", "operating income", "operating result"],
    "ebit": ["ebit"],
    "ebitda": ["ebitda"],
    "profit before tax": ["profit before tax", "earnings before tax", "ebt", "result before tax", "profit after financial items"],
    "net profit": ["net profit", "net income", "net result", "profit for the period", "net earnings", "result for the period"],
    # Finansiellt
    "net financial items": ["net financial items", "net financial result"],
    "financial income": ["financial income", "finance income"],
    "financial expenses": ["financial expenses", "finance costs"],
    # Skatt
    "tax": ["tax", "income tax", "tax expense"],

    # ============ BALANSRÄKNING - TILLGÅNGAR ============
    # OBS: "assets" ensamt, "fixed assets" är för vaga
    "total assets": ["total assets", "sum assets"],
    "non-current assets": ["non-current assets", "long-term assets"],
    "current assets": ["current assets", "short-term assets"],
    "intangible assets": ["intangible assets", "intangibles"],
    "goodwill": ["goodwill"],
    "property plant and equipment": ["property plant and equipment", "ppe", "tangible fixed assets"],
    "inventories": ["inventories", "inventory"],  # OBS: "stock" borttagen (tvetydigt)
    "trade receivables": ["trade receivables", "accounts receivable"],
    "cash and cash equivalents": ["cash and cash equivalents", "cash and equivalents", "cash and bank"],

    # ============ BALANSRÄKNING - EGET KAPITAL ============
    # OBS: "equity" ensamt, "net assets" är för vaga
    "total equity": ["total equity", "shareholders equity", "stockholders equity"],
    "share capital": ["share capital", "common stock", "issued capital"],
    "retained earnings": ["retained earnings", "accumulated profit", "accumulated earnings"],

    # ============ BALANSRÄKNING - SKULDER ============
    # OBS: "debt", "liabilities" ensamma är för vaga
    "total liabilities": ["total liabilities", "sum liabilities"],
    "non-current liabilities": ["non-current liabilities", "long-term liabilities"],
    "current liabilities": ["current liabilities", "short-term liabilities"],
    "trade payables": ["trade payables", "accounts payable"],
    "interest-bearing debt": ["interest-bearing debt", "interest-bearing liabilities", "borrowings"],
    "provisions": ["provisions"],  # OBS: "accruals" borttagen (annorlunda)
    "deferred tax liabilities": ["deferred tax liabilities", "deferred tax"],

    # ============ KASSAFLÖDE ============
    "cash flow from operations": ["cash flow from operations", "operating cash flow", "cash flow from operating activities"],
    "cash flow from investing": ["cash flow from investing", "cash flow from investing activities"],
    "cash flow from financing": ["cash flow from financing", "cash flow from financing activities"],
    "change in cash": ["change in cash", "net change in cash"],
    "capital expenditure": ["capital expenditure", "capex"],
    "dividends paid": ["dividends paid", "dividend payments"],
}


# Cache för DB-synonymer (laddas vid första anrop)
_db_synonyms_cache: dict | None = None


def _load_db_synonyms() -> dict:
    """Ladda synonymer från databasen (cachas)."""
    global _db_synonyms_cache
    if _db_synonyms_cache is not None:
        return _db_synonyms_cache

    try:
        client = get_client()
        result = client.table("label_synonyms").select("synonym, canonical").execute()
        if result.data:
            _db_synonyms_cache = {row["synonym"]: row["canonical"] for row in result.data}
            return _db_synonyms_cache
    except Exception:
        pass  # Fallback till Python-dict om DB-tabell saknas

    _db_synonyms_cache = {}
    return _db_synonyms_cache


def _normalize_label_en(label: str) -> str:
    """Normalisera label_en för jämförelse. Använder DB om tillgänglig, annars Python-dict."""
    label_lower = label.lower().strip()

    # Försök DB-lookup först (skalbart för 1000+ bolag)
    db_synonyms = _load_db_synonyms()
    if db_synonyms and label_lower in db_synonyms:
        return db_synonyms[label_lower]

    # Fallback till Python-dict
    for canonical, synonyms in LABEL_EN_SYNONYMS.items():
        if label_lower in synonyms:
            return canonical

    return label_lower


def db_compare_companies(
    company1_slug: str,
    company2_slug: str,
    period1: str | None = None,
    period2: str | None = None,
    statement_type: str = "income_statement"
) -> dict:
    """
    Jämför finansiell data mellan två olika bolag.
    Matchar rader via label_en för cross-language jämförelse.

    Args:
        company1_slug: Första bolagets slug/namn
        company2_slug: Andra bolagets slug/namn
        period1: Period för bolag 1 (default: senaste)
        period2: Period för bolag 2 (default: senaste)
        statement_type: Typ av rapport att jämföra
    """
    data1 = db_get_financials(company1_slug, period1, statement_type)
    data2 = db_get_financials(company2_slug, period2, statement_type)

    if "error" in data1:
        return data1
    if "error" in data2:
        return data2

    # Bygg jämförelse med label_en som matchningsnyckel
    comparison_rows = []

    # Samla alla rader från båda bolagen
    rows1 = []
    rows2 = []

    for table_type, tables in data1.get("tables", {}).items():
        for table in tables:
            for row in table.get("rows", []):
                rows1.append({
                    "label": row.get("label", ""),
                    "label_en": row.get("label_en", ""),
                    "normalized_label": _normalize_label_en(row.get("label_en", "")),
                    "values": row.get("values", []),
                    "table": table.get("title", "")
                })

    for table_type, tables in data2.get("tables", {}).items():
        for table in tables:
            for row in table.get("rows", []):
                rows2.append({
                    "label": row.get("label", ""),
                    "label_en": row.get("label_en", ""),
                    "normalized_label": _normalize_label_en(row.get("label_en", "")),
                    "values": row.get("values", []),
                    "table": table.get("title", "")
                })

    # Matcha rader via normaliserad label_en
    matched = set()
    unmatched_rows1 = []

    for r1 in rows1:
        normalized = r1.get("normalized_label", "")
        if not normalized:
            continue

        found_match = False
        for r2 in rows2:
            r2_normalized = r2.get("normalized_label", "")
            if normalized == r2_normalized and normalized not in matched:
                matched.add(normalized)
                found_match = True

                # Hämta första numeriska värdet (hoppa över null)
                val1_raw = next((v for v in r1.get("values", []) if v is not None), None)
                val2_raw = next((v for v in r2.get("values", []) if v is not None), None)

                # Konvertera till nummer om möjligt
                def to_number(val):
                    if val is None:
                        return None
                    if isinstance(val, (int, float)):
                        return val
                    if isinstance(val, str):
                        try:
                            # Försök konvertera sträng till tal
                            cleaned = val.strip().replace(" ", "").replace(",", ".")
                            return float(cleaned)
                        except (ValueError, AttributeError):
                            return None
                    return None

                val1 = to_number(val1_raw)
                val2 = to_number(val2_raw)

                # Beräkna difference och ratio endast om båda är numeriska
                diff = None
                ratio = None
                if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
                    diff = round(val1 - val2, 2)
                    if val2 != 0:
                        ratio = round(val1 / val2, 2)

                comparison_rows.append({
                    "label_en": r1.get("label_en", ""),
                    "matched_via": normalized if normalized != r1.get("label_en", "").lower().strip() else None,
                    "company1": {
                        "label": r1.get("label", ""),
                        "value": val1
                    },
                    "company2": {
                        "label": r2.get("label", ""),
                        "value": val2
                    },
                    "difference": diff,
                    "ratio": ratio
                })
                break

        if not found_match and r1.get("label_en"):
            unmatched_rows1.append(r1.get("label_en", ""))

    # Ta bort None från matched_via i output
    for row in comparison_rows:
        if row.get("matched_via") is None:
            del row["matched_via"]

    return {
        "comparison_type": "cross_company",
        "statement_type": statement_type,
        "company1": {
            "name": data1.get("company", ""),
            "period": data1.get("period", ""),
            "valuta": data1.get("valuta", ""),
            "language": data1.get("language", "sv")
        },
        "company2": {
            "name": data2.get("company", ""),
            "period": data2.get("period", ""),
            "valuta": data2.get("valuta", ""),
            "language": data2.get("language", "sv")
        },
        "matched_rows": comparison_rows,
        "match_count": len(comparison_rows),
        "unmatched_from_company1": unmatched_rows1[:10] if unmatched_rows1 else [],
        "note": "Matchning sker via label_en med synonym-normalisering"
    }


# =============================================================================
# KNOWLEDGE-FUNKTIONER (RAG)
# =============================================================================

def db_add_knowledge(
    domain: str,
    category: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    related_metrics: list[str] | None = None,
    source: str | None = None
) -> dict:
    """
    Lägg till kunskap i knowledge-databasen.

    Args:
        domain: Huvudområde (nyckeltal, redovisning, bransch, värdering, kvalitativ)
        category: Underkategori (t.ex. lönsamhet, IFRS, SaaS)
        title: Rubrik för kunskapsposten
        content: Innehållet (300-1500 tecken rekommenderat)
        tags: Nyckelord för sökning
        related_metrics: Koppling till finansiella mått
        source: Källa (FAR, CFA, intern, etc.)
    """
    client = get_client()

    # Validera domain
    valid_domains = ["nyckeltal", "redovisning", "bransch", "värdering", "kvalitativ"]
    if domain not in valid_domains:
        return {"error": f"Ogiltig domain: {domain}. Giltiga: {valid_domains}"}

    # Skapa embedding för content
    embedding = None
    try:
        text_for_embedding = f"{title}\n\n{content}"
        embedding = get_query_embedding(text_for_embedding)
    except Exception as e:
        # Fortsätt utan embedding om det misslyckas
        pass

    # Skapa post
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
            return {
                "success": True,
                "id": result.data[0]["id"],
                "title": title,
                "domain": domain,
                "category": category,
                "has_embedding": embedding is not None
            }
        else:
            return {"error": "Kunde inte spara kunskapspost"}
    except Exception as e:
        return {"error": f"Databasfel: {str(e)}"}


def db_search_knowledge(
    query: str,
    domain: str | None = None,
    category: str | None = None,
    limit: int = 5
) -> dict:
    """
    Sök i knowledge-databasen med semantisk sökning.

    Args:
        query: Sökfråga
        domain: Filtrera på domän (valfritt)
        category: Filtrera på kategori (valfritt)
        limit: Max antal resultat (default 5)
    """
    client = get_client()

    # Skapa embedding för sökfrågan
    query_embedding = get_query_embedding(query)
    fallback_reason = None

    if not VOYAGE_API_KEY:
        fallback_reason = "VOYAGE_API_KEY ej konfigurerad"
    elif not query_embedding:
        fallback_reason = "Kunde inte skapa embedding för sökfrågan"

    if query_embedding:
        # Semantisk sökning via RPC
        try:
            result = client.rpc("search_knowledge", {
                "query_embedding": query_embedding,
                "match_count": limit,
                "domain_filter": domain,
                "category_filter": category
            }).execute()

            if result.data:
                return {
                    "query": query,
                    "search_type": "semantic",
                    "results": [{
                        "title": r["title"],
                        "content": r["content"],
                        "domain": r["domain"],
                        "category": r["category"],
                        "tags": r["tags"],
                        "related_metrics": r["related_metrics"],
                        "similarity": round(r["similarity"], 3)
                    } for r in result.data]
                }
        except Exception as e:
            fallback_reason = f"RPC-fel: {str(e)}"

    # Fallback: enkel textsökning
    query_builder = client.table("knowledge").select("*")

    if domain:
        query_builder = query_builder.eq("domain", domain)
    if category:
        query_builder = query_builder.eq("category", category)

    # Sök i title och content - splitta på mellanslag för bättre matchning
    search_terms = query.replace("/", " ").split()
    if len(search_terms) > 1:
        # Sök på varje term separat och kombinera
        or_conditions = []
        for term in search_terms[:3]:  # Max 3 termer
            or_conditions.append(f"title.ilike.%{term}%")
            or_conditions.append(f"content.ilike.%{term}%")
        query_builder = query_builder.or_(",".join(or_conditions))
    else:
        query_builder = query_builder.or_(f"title.ilike.%{query}%,content.ilike.%{query}%")
    query_builder = query_builder.limit(limit)

    result = query_builder.execute()

    response = {
        "query": query,
        "search_type": "text",
        "results": [{
            "title": r["title"],
            "content": r["content"],
            "domain": r["domain"],
            "category": r["category"],
            "tags": r.get("tags", []),
            "related_metrics": r.get("related_metrics", [])
        } for r in result.data]
    }

    if fallback_reason:
        response["fallback_reason"] = fallback_reason

    return response


def db_list_knowledge(domain: str | None = None, category: str | None = None) -> dict:
    """Lista all kunskap, med valfri filtrering."""
    client = get_client()

    query = client.table("knowledge").select("id, domain, category, title, tags, created_at")

    if domain:
        query = query.eq("domain", domain)
    if category:
        query = query.eq("category", category)

    query = query.order("domain").order("category").order("title")
    result = query.execute()

    # Gruppera per domain/category
    grouped = {}
    for r in result.data:
        key = f"{r['domain']}/{r['category']}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({
            "id": r["id"],
            "title": r["title"],
            "tags": r.get("tags", [])
        })

    return {
        "total": len(result.data),
        "by_category": grouped
    }


def db_delete_knowledge(knowledge_id: str) -> dict:
    """Ta bort en kunskapspost."""
    client = get_client()

    try:
        # Kontrollera att posten finns
        existing = client.table("knowledge").select("id, title").eq("id", knowledge_id).execute()
        if not existing.data:
            return {"error": f"Kunskapspost med id '{knowledge_id}' hittades inte"}

        title = existing.data[0]["title"]

        # Ta bort
        client.table("knowledge").delete().eq("id", knowledge_id).execute()

        return {
            "success": True,
            "deleted_id": knowledge_id,
            "deleted_title": title
        }
    except Exception as e:
        return {"error": f"Kunde inte ta bort: {str(e)}"}


def db_update_knowledge(
    knowledge_id: str,
    title: str | None = None,
    content: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    related_metrics: list[str] | None = None,
    source: str | None = None
) -> dict:
    """Uppdatera en kunskapspost."""
    client = get_client()

    try:
        # Kontrollera att posten finns
        existing = client.table("knowledge").select("*").eq("id", knowledge_id).execute()
        if not existing.data:
            return {"error": f"Kunskapspost med id '{knowledge_id}' hittades inte"}

        # Bygg uppdatering
        updates = {}
        if title is not None:
            updates["title"] = title
        if content is not None:
            updates["content"] = content
        if category is not None:
            updates["category"] = category
        if tags is not None:
            updates["tags"] = tags
        if related_metrics is not None:
            updates["related_metrics"] = related_metrics
        if source is not None:
            updates["source"] = source

        if not updates:
            return {"error": "Inga fält att uppdatera"}

        # Uppdatera embedding om title eller content ändrats
        if title is not None or content is not None:
            new_title = title or existing.data[0]["title"]
            new_content = content or existing.data[0]["content"]
            try:
                embedding = get_query_embedding(f"{new_title}\n\n{new_content}")
                if embedding:
                    updates["embedding"] = embedding
            except Exception:
                pass

        updates["updated_at"] = "now()"

        # Utför uppdatering
        result = client.table("knowledge").update(updates).eq("id", knowledge_id).execute()

        if result.data:
            return {
                "success": True,
                "id": knowledge_id,
                "updated_fields": list(updates.keys())
            }
        else:
            return {"error": "Uppdatering misslyckades"}

    except Exception as e:
        return {"error": f"Kunde inte uppdatera: {str(e)}"}


def _load_image_as_base64(image_path: str) -> str | None:
    """Läs en bildfil och returnera som base64-sträng."""
    import base64
    from pathlib import Path

    if not image_path:
        return None

    path = Path(image_path)
    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            image_data = f.read()
        return f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
    except Exception:
        return None


def db_get_charts(company_slug: str, period: str | None = None, include_images: bool = True) -> dict:
    """
    Hämta grafer/diagram för en period.

    Args:
        company_slug: Bolagets slug eller namn
        period: T.ex. "Q3 2024" (om None, hämta senaste)
        include_images: Om True, inkludera base64-kodade bilder för visuell analys
    """
    client = get_client()

    # Hitta bolag
    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    # Hitta period (inkluderar source_file och pdf_hash direkt)
    if period:
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select(
                "id, quarter, year, source_file, pdf_hash"
            ).eq("company_id", company_id).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}"}
    else:
        period_row = client.table("periods").select(
            "id, quarter, year, source_file, pdf_hash"
        ).eq("company_id", company_id).order("year", desc=True).order("quarter", desc=True).limit(1).execute()

    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}

    p = period_row.data[0]
    period_id = p["id"]
    period_str = f"Q{p['quarter']} {p['year']}"

    # Hämta grafer
    try:
        charts = client.table("charts").select("*").eq("period_id", period_id).order("page_number").execute()

        formatted_charts = []
        for c in charts.data:
            chart_data = {
                "title": c["title"],
                "type": c["chart_type"],
                "page": c["page_number"],
                "x_axis": c.get("x_axis"),
                "y_axis": c.get("y_axis"),
                "estimated": c["estimated"],
                "data_points": c["data_points"],
                "image_path": c.get("image_path"),
            }

            # Läs in bilden som base64 om den finns och include_images är True
            if include_images and c.get("image_path"):
                image_base64 = _load_image_as_base64(c["image_path"])
                if image_base64:
                    chart_data["image_base64"] = image_base64

            formatted_charts.append(chart_data)

        return {
            "company": company_name,
            "period": period_str,
            "source": {
                "file": p.get("source_file"),
                "pdf_hash": p.get("pdf_hash")
            },
            "charts": formatted_charts,
            "note": "Grafer med image_base64 kan analyseras visuellt av Claude" if include_images else None
        }
    except Exception:
        return {
            "company": company_name,
            "period": period_str,
            "source": {
                "file": p.get("source_file"),
                "pdf_hash": p.get("pdf_hash")
            },
            "charts": [],
            "note": "Inga grafer tillgängliga (charts-tabellen saknas)"
        }


# =============================================================================
# MCP SERVER
# =============================================================================

# Skapa server
server = Server("rapport-extraktor")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Definiera tillgängliga verktyg."""
    return [
        Tool(
            name="list_companies",
            description="Lista alla bolag i databasen med antal tillgängliga perioder",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_periods",
            description="Visa alla tillgängliga perioder (kvartal) för ett bolag",
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Bolagets namn eller slug (t.ex. 'vitrolife' eller 'Vitrolife AB')"
                    }
                },
                "required": ["company"]
            }
        ),
        Tool(
            name="get_financials",
            description="Hämta finansiell data (resultaträkning, balansräkning, kassaflöde) för ett bolag och period",
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Bolagets namn eller slug"
                    },
                    "period": {
                        "type": "string",
                        "description": "Period, t.ex. 'Q3 2024'. Om utelämnad hämtas senaste."
                    },
                    "statement_type": {
                        "type": "string",
                        "enum": ["income_statement", "balance_sheet", "cash_flow"],
                        "description": "Typ av rapport. Om utelämnad hämtas alla."
                    }
                },
                "required": ["company"]
            }
        ),
        Tool(
            name="get_kpis",
            description="Hämta nyckeltal (KPIs) som marginaler, tillväxt etc. för ett bolag",
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Bolagets namn eller slug"
                    },
                    "period": {
                        "type": "string",
                        "description": "Period, t.ex. 'Q3 2024'. Om utelämnad hämtas senaste."
                    }
                },
                "required": ["company"]
            }
        ),
        Tool(
            name="get_sections",
            description="Hämta textsektioner som VD-kommentar, marknadsöversikt etc. för ett bolag",
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Bolagets namn eller slug"
                    },
                    "period": {
                        "type": "string",
                        "description": "Period, t.ex. 'Q3 2024'. Om utelämnad hämtas senaste."
                    },
                    "section_type": {
                        "type": "string",
                        "enum": ["narrative", "summary", "highlights", "notes", "other"],
                        "description": "Typ av sektion. Om utelämnad hämtas alla."
                    }
                },
                "required": ["company"]
            }
        ),
        Tool(
            name="search_sections",
            description="Sök i alla textsektioner efter specifika termer eller ämnen. Använder hybrid sökning (text + semantisk) som default för bästa resultat.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Sökterm, t.ex. 'tillväxt', 'leveransproblem', 'förvärv'"
                    },
                    "company": {
                        "type": "string",
                        "description": "Begränsa sökning till ett bolag (valfritt)"
                    },
                    "use_hybrid": {
                        "type": "boolean",
                        "description": "Använd hybrid sökning (text + semantisk). Default: true"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="compare_periods",
            description="Jämför finansiell data mellan två perioder för samma bolag",
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Bolagets namn eller slug"
                    },
                    "period1": {
                        "type": "string",
                        "description": "Första perioden, t.ex. 'Q3 2024'"
                    },
                    "period2": {
                        "type": "string",
                        "description": "Andra perioden, t.ex. 'Q3 2023'"
                    },
                    "statement_type": {
                        "type": "string",
                        "enum": ["income_statement", "balance_sheet", "cash_flow"],
                        "description": "Typ av rapport att jämföra. Default: income_statement"
                    }
                },
                "required": ["company", "period1", "period2"]
            }
        ),
        Tool(
            name="get_charts",
            description="Hämta extraherade grafer och diagram för ett bolag. Med include_images=true kan Claude se och analysera grafbilderna visuellt.",
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Bolagets namn eller slug"
                    },
                    "period": {
                        "type": "string",
                        "description": "Period, t.ex. 'Q3 2024'. Om utelämnad hämtas senaste."
                    },
                    "include_images": {
                        "type": "boolean",
                        "description": "Inkludera base64-kodade bilder för visuell analys. Default: true"
                    }
                },
                "required": ["company"]
            }
        ),
        Tool(
            name="compare_companies",
            description="Jämför finansiell data mellan två olika bolag. Fungerar även cross-language (t.ex. svenskt vs norskt bolag) via standardiserade engelska termer (label_en).",
            inputSchema={
                "type": "object",
                "properties": {
                    "company1": {
                        "type": "string",
                        "description": "Första bolagets namn eller slug"
                    },
                    "company2": {
                        "type": "string",
                        "description": "Andra bolagets namn eller slug"
                    },
                    "period1": {
                        "type": "string",
                        "description": "Period för bolag 1, t.ex. 'Q3 2024'. Om utelämnad hämtas senaste."
                    },
                    "period2": {
                        "type": "string",
                        "description": "Period för bolag 2, t.ex. 'Q3 2024'. Om utelämnad hämtas senaste."
                    },
                    "statement_type": {
                        "type": "string",
                        "enum": ["income_statement", "balance_sheet", "cash_flow"],
                        "description": "Typ av rapport att jämföra. Default: income_statement"
                    }
                },
                "required": ["company1", "company2"]
            }
        ),
        # Knowledge-verktyg
        Tool(
            name="add_knowledge",
            description="Lägg till kunskap i kunskapsdatabasen. Använd för att spara definitioner, formler, analysmetoder och best practices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": ["nyckeltal", "redovisning", "bransch", "värdering", "kvalitativ"],
                        "description": "Huvudområde för kunskapen"
                    },
                    "category": {
                        "type": "string",
                        "description": "Underkategori, t.ex. 'lönsamhet', 'IFRS', 'SaaS', 'röda_flaggor'"
                    },
                    "title": {
                        "type": "string",
                        "description": "Rubrik för kunskapsposten"
                    },
                    "content": {
                        "type": "string",
                        "description": "Innehållet (300-1500 tecken rekommenderat). Inkludera formler, definitioner, typiska värden, tolkningsguide."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Nyckelord för sökning"
                    },
                    "related_metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relaterade finansiella mått (t.ex. 'EBITDA', 'Nettoomsättning')"
                    },
                    "source": {
                        "type": "string",
                        "description": "Källa (t.ex. 'FAR', 'CFA', 'intern')"
                    }
                },
                "required": ["domain", "category", "title", "content"]
            }
        ),
        Tool(
            name="search_knowledge",
            description="Sök i kunskapsdatabasen efter definitioner, formler, analysmetoder och best practices. Använder semantisk sökning.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Sökfråga, t.ex. 'hur beräknas EBITDA-marginal' eller 'röda flaggor kassaflöde'"
                    },
                    "domain": {
                        "type": "string",
                        "enum": ["nyckeltal", "redovisning", "bransch", "värdering", "kvalitativ"],
                        "description": "Filtrera på domän (valfritt)"
                    },
                    "category": {
                        "type": "string",
                        "description": "Filtrera på kategori (valfritt)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max antal resultat (default 5)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="list_knowledge",
            description="Lista all kunskap i databasen, grupperat per domän och kategori.",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": ["nyckeltal", "redovisning", "bransch", "värdering", "kvalitativ"],
                        "description": "Filtrera på domän (valfritt)"
                    },
                    "category": {
                        "type": "string",
                        "description": "Filtrera på kategori (valfritt)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="delete_knowledge",
            description="Ta bort en kunskapspost från databasen.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "UUID för kunskapsposten att ta bort (från list_knowledge)"
                    }
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="update_knowledge",
            description="Uppdatera en befintlig kunskapspost. Embedding uppdateras automatiskt om title eller content ändras.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "UUID för kunskapsposten att uppdatera"
                    },
                    "title": {
                        "type": "string",
                        "description": "Ny rubrik (valfritt)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Nytt innehåll (valfritt)"
                    },
                    "category": {
                        "type": "string",
                        "description": "Ny kategori (valfritt)"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Nya taggar (valfritt)"
                    },
                    "related_metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Nya relaterade mått (valfritt)"
                    },
                    "source": {
                        "type": "string",
                        "description": "Ny källa (valfritt)"
                    }
                },
                "required": ["id"]
            }
        )
    ]


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """Definiera tillgängliga prompts."""
    return [
        Prompt(
            name="verktyg",
            description="Tillgängliga verktyg",
            arguments=[]
        ),
        Prompt(
            name="dataset",
            description="Tillgängliga dataset",
            arguments=[]
        ),
        Prompt(
            name="funktionalitet",
            description="Funktionalitet",
            arguments=[]
        )
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    """Hämta en prompt."""
    if name == "verktyg":
        return GetPromptResult(
            description="Tillgängliga verktyg",
            messages=[PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text="""Visa följande information direkt i chatten (inte som sammanfattning):

# Tillgängliga verktyg

## Finansiell data
| Verktyg | Beskrivning |
|---------|-------------|
| `list_companies` | Lista alla bolag i databasen |
| `get_periods` | Visa tillgängliga kvartal för ett bolag |
| `get_financials` | Hämta resultaträkning, balansräkning eller kassaflödesanalys |
| `get_kpis` | Hämta nyckeltal (marginaler, tillväxt, etc.) |
| `get_sections` | Hämta textsektioner (VD-ord, marknadsöversikt, etc.) |
| `get_charts` | Hämta extraherade grafer med datapunkter |
| `search_sections` | Sök i textsektioner (hybrid: text + semantisk AI-sökning) |
| `compare_periods` | Jämför två perioder för samma bolag |
| `compare_companies` | Jämför två bolag (stödjer cross-language) |

## Kunskapsdatabas (RAG)
| Verktyg | Beskrivning |
|---------|-------------|
| `search_knowledge` | Sök efter definitioner, formler och analysmetoder |
| `add_knowledge` | Lägg till ny kunskap (definitioner, formler, best practices) |
| `list_knowledge` | Lista all kunskap grupperat per domän/kategori |
| `update_knowledge` | Uppdatera befintlig kunskapspost |
| `delete_knowledge` | Ta bort kunskapspost |

## Användning

Ställ frågor på naturligt språk, t.ex.:
- "Visa Vitrolifes resultaträkning för Q3 2024"
- "Sök efter information om förvärv"
- "Hur beräknas EBITDA-marginal?"
- "Lägg till kunskap om Rule of 40"

All data inkluderar källhänvisningar (PDF-fil, period, sidnummer)."""
                )
            )]
        )

    elif name == "dataset":
        return GetPromptResult(
            description="Tillgängliga dataset",
            messages=[PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text="""Visa följande information direkt i chatten (inte som sammanfattning):

# Tillgängliga dataset

Databasen innehåller extraherad data från kvartalsrapporter.

## Datatyper per rapport

| Typ | Beskrivning |
|-----|-------------|
| **Finansiella tabeller** | Resultaträkning, balansräkning, kassaflödesanalys |
| **Nyckeltal (KPIs)** | Marginaler, tillväxt, lönsamhet |
| **Textsektioner** | VD-ord, marknadsöversikt, verksamhetsbeskrivning |
| **Övriga tabeller** | Segmentdata, produktfördelning, geografisk fördelning |
| **Grafer** | Extraherade diagram med uppskattade datapunkter |

## Metadata

Varje datapunkt inkluderar:
- Bolag och period (Q1-Q4 + år)
- Källfil (PDF-namn)
- Sidnummer (för sektioner och tabeller)
- Valuta (SEK, NOK, EUR, etc.)
- Språk (sv, no, en)

## Sökning

Textsektioner har semantiska embeddings (Voyage AI) för intelligent sökning som förstår synonymer och kontext."""
                )
            )]
        )

    elif name == "funktionalitet":
        return GetPromptResult(
            description="Funktionalitet",
            messages=[PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text="""Visa följande information direkt i chatten (inte som sammanfattning):

# Funktionalitet

## Vad kan jag hjälpa dig med?

### Analys
- Hämta finansiell data för specifika bolag och perioder
- Jämföra perioder (t.ex. Q3 2024 vs Q3 2023)
- Jämföra bolag (stödjer cross-language, t.ex. svenskt vs norskt bolag)
- Söka efter specifik information i rapporttexter

### Sökning
Hybrid sökning kombinerar:
- **Textsökning** (30%): Exakta ordmatchningar
- **Semantisk sökning** (70%): AI som förstår betydelse och kontext

Tips: Sök på beskrivande fraser som "lönsamhetsförbättring" eller "förvärvsstrategi"

### Exempel på frågor
- "Vilka bolag finns i databasen?"
- "Hur har Vitrolifes omsättning utvecklats senaste året?"
- "Vad säger VD:n om framtidsutsikterna?"
- "Sök efter information om hållbarhet"
- "Jämför marginaler mellan Q2 och Q3 2024"

### Källhänvisningar
All data inkluderar spårbarhet till ursprunglig PDF, period och sidnummer."""
                )
            )]
        )

    # Fallback för okänd prompt
    return GetPromptResult(
        description="Okänd prompt",
        messages=[]
    )


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Hantera verktygsanrop."""
    try:
        if name == "list_companies":
            result = db_list_companies()
        
        elif name == "get_periods":
            result = db_get_periods(arguments["company"])
        
        elif name == "get_financials":
            result = db_get_financials(
                arguments["company"],
                arguments.get("period"),
                arguments.get("statement_type")
            )
        
        elif name == "get_kpis":
            result = db_get_kpis(
                arguments["company"],
                arguments.get("period")
            )

        elif name == "get_sections":
            result = db_get_sections(
                arguments["company"],
                arguments.get("period"),
                arguments.get("section_type")
            )

        elif name == "search_sections":
            result = db_search_sections(
                arguments["query"],
                arguments.get("company"),
                use_hybrid=arguments.get("use_hybrid", True)
            )
        
        elif name == "compare_periods":
            result = db_compare_periods(
                arguments["company"],
                arguments["period1"],
                arguments["period2"],
                arguments.get("statement_type", "income_statement")
            )
        
        elif name == "get_charts":
            result = db_get_charts(
                arguments["company"],
                arguments.get("period"),
                include_images=arguments.get("include_images", True)
            )

        elif name == "compare_companies":
            result = db_compare_companies(
                arguments["company1"],
                arguments["company2"],
                arguments.get("period1"),
                arguments.get("period2"),
                arguments.get("statement_type", "income_statement")
            )

        # Knowledge-verktyg
        elif name == "add_knowledge":
            result = db_add_knowledge(
                domain=arguments["domain"],
                category=arguments["category"],
                title=arguments["title"],
                content=arguments["content"],
                tags=arguments.get("tags"),
                related_metrics=arguments.get("related_metrics"),
                source=arguments.get("source")
            )

        elif name == "search_knowledge":
            result = db_search_knowledge(
                query=arguments["query"],
                domain=arguments.get("domain"),
                category=arguments.get("category"),
                limit=arguments.get("limit", 5)
            )

        elif name == "list_knowledge":
            result = db_list_knowledge(
                domain=arguments.get("domain"),
                category=arguments.get("category")
            )

        elif name == "delete_knowledge":
            result = db_delete_knowledge(arguments["id"])

        elif name == "update_knowledge":
            result = db_update_knowledge(
                knowledge_id=arguments["id"],
                title=arguments.get("title"),
                content=arguments.get("content"),
                category=arguments.get("category"),
                tags=arguments.get("tags"),
                related_metrics=arguments.get("related_metrics"),
                source=arguments.get("source")
            )

        else:
            result = {"error": f"Okänt verktyg: {name}"}
        
        return [TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, indent=2)
        )]
    
    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)}, ensure_ascii=False)
        )]


async def main():
    """Starta MCP-servern."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
