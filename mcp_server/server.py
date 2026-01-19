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
from mcp.types import Tool, TextContent
from supabase import create_client, Client

# Ladda miljövariabler
load_dotenv()

# Voyage API för embeddings
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "pa-S5CQuQswu6Vhm3uf3L1TFBCmjP36RLaTkpxpzb4gfCZ")
VOYAGE_MODEL = "voyage-3"

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
    
    # Hitta period
    if period:
        # Parsa "Q3 2024"
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select("id, quarter, year, valuta").eq(
                "company_id", company_id
            ).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}. Använd t.ex. 'Q3 2024'"}
    else:
        # Senaste period
        period_row = client.table("periods").select("id, quarter, year, valuta").eq(
            "company_id", company_id
        ).order("year", desc=True).order("quarter", desc=True).limit(1).execute()
    
    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}
    
    period_id = period_row.data[0]["id"]
    period_str = f"Q{period_row.data[0]['quarter']} {period_row.data[0]['year']}"
    valuta = period_row.data[0].get("valuta", "TSEK")
    
    result = {
        "company": company_name,
        "period": period_str,
        "valuta": valuta,
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

    # Hitta period
    if period:
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select("id, quarter, year, valuta").eq(
                "company_id", company_id
            ).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}. Använd t.ex. 'Q3 2024'"}
    else:
        period_row = client.table("periods").select("id, quarter, year, valuta").eq(
            "company_id", company_id
        ).order("year", desc=True).order("quarter", desc=True).limit(1).execute()

    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}

    period_id = period_row.data[0]["id"]
    period_str = f"Q{period_row.data[0]['quarter']} {period_row.data[0]['year']}"
    valuta = period_row.data[0].get("valuta", "TSEK")

    # Hämta KPI-tabeller från report_tables
    kpis = client.table("report_tables").select("*").eq(
        "period_id", period_id
    ).eq("table_type", "kpi").order("page_number").execute()

    return {
        "company": company_name,
        "period": period_str,
        "valuta": valuta,
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
    
    # Hitta period
    if period:
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select("id, quarter, year").eq(
                "company_id", company_id
            ).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}"}
    else:
        period_row = client.table("periods").select("id, quarter, year").eq(
            "company_id", company_id
        ).order("year", desc=True).order("quarter", desc=True).limit(1).execute()
    
    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}
    
    period_id = period_row.data[0]["id"]
    period_str = f"Q{period_row.data[0]['quarter']} {period_row.data[0]['year']}"
    
    # Hämta sektioner
    query = client.table("sections").select("title, section_type, page_number, content").eq("period_id", period_id)
    if section_type:
        query = query.eq("section_type", section_type)
    
    sections = query.order("page_number").execute()
    
    return {
        "company": company_name,
        "period": period_str,
        "sections": [{
            "title": s["title"],
            "type": s["section_type"],
            "page": s["page_number"],
            "content": s["content"][:2000] + "..." if len(s["content"]) > 2000 else s["content"]
        } for s in sections.data]
    }


def get_query_embedding(text: str) -> list[float] | None:
    """Hämta embedding för en sökfråga via Voyage AI."""
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


def db_search_sections(query: str, company_slug: str | None = None, use_embedding: bool = False) -> list[dict]:
    """
    Sök i textsektioner.

    Args:
        query: Sökterm
        company_slug: Begränsa till ett bolag (valfritt)
        use_embedding: Använd semantisk sökning med embeddings (kräver att embeddings finns)
    """
    client = get_client()

    # Embedding-sökning (om aktiverat och embeddings finns)
    if use_embedding:
        try:
            # Generera embedding för sökfrågan
            query_embedding = get_query_embedding(query)
            if query_embedding:
                # Anropa Supabase RPC-funktion för embedding-sökning
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
                        "excerpt": r["content"][:300] + "..." if len(r["content"]) > 300 else r["content"],
                        "similarity": round(r.get("similarity", 0), 3)
                    } for r in result.data]
        except Exception:
            # Fallback till textsökning om embedding-sökning misslyckas
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
            "title, section_type, content, period_id"
        ).in_("period_id", period_ids).ilike("content", f"%{query}%").limit(10).execute()
    else:
        # Sök i alla sektioner
        sections = client.table("sections").select(
            "title, section_type, content, period_id"
        ).ilike("content", f"%{query}%").limit(10).execute()

    results = []
    for s in sections.data:
        # Hämta period-info
        period = client.table("periods").select(
            "quarter, year, company_id"
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
                "excerpt": excerpt
            })

    return results


def db_compare_periods(company_slug: str, period1: str, period2: str) -> dict:
    """Jämför två perioder för samma bolag."""
    data1 = db_get_financials(company_slug, period1, "income_statement")
    data2 = db_get_financials(company_slug, period2, "income_statement")
    
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


def db_get_charts(company_slug: str, period: str | None = None) -> dict:
    """Hämta grafer/diagram för en period."""
    client = get_client()
    
    # Hitta bolag
    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()
    
    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}
    
    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]
    
    # Hitta period
    if period:
        import re
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select("id, quarter, year").eq(
                "company_id", company_id
            ).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}"}
    else:
        period_row = client.table("periods").select("id, quarter, year").eq(
            "company_id", company_id
        ).order("year", desc=True).order("quarter", desc=True).limit(1).execute()
    
    if not period_row.data:
        return {"error": f"Ingen period hittad för {company_name}"}
    
    period_id = period_row.data[0]["id"]
    period_str = f"Q{period_row.data[0]['quarter']} {period_row.data[0]['year']}"
    
    # Hämta grafer
    try:
        charts = client.table("charts").select("*").eq("period_id", period_id).order("page_number").execute()

        return {
            "company": company_name,
            "period": period_str,
            "charts": [{
                "title": c["title"],
                "type": c["chart_type"],
                "page": c["page_number"],
                "x_axis": c.get("x_axis"),
                "y_axis": c.get("y_axis"),
                "estimated": c["estimated"],
                "data_points": c["data_points"]
            } for c in charts.data]
        }
    except Exception:
        return {
            "company": company_name,
            "period": period_str,
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
            description="Sök i alla textsektioner efter specifika termer eller ämnen. Stöder semantisk sökning med embeddings.",
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
                    "use_embedding": {
                        "type": "boolean",
                        "description": "Använd semantisk sökning med AI-embeddings (default: false)"
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
                    }
                },
                "required": ["company", "period1", "period2"]
            }
        ),
        Tool(
            name="get_charts",
            description="Hämta extraherade grafer och diagram med datapunkter för ett bolag",
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
        )
    ]


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
                arguments.get("use_embedding", False)
            )
        
        elif name == "compare_periods":
            result = db_compare_periods(
                arguments["company"],
                arguments["period1"],
                arguments["period2"]
            )
        
        elif name == "get_charts":
            result = db_get_charts(
                arguments["company"],
                arguments.get("period")
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
