"""
Remote MCP Server med SSE transport.

Exponerar samma verktyg som den lokala MCP-servern, men via HTTP/SSE
så att användare kan ansluta via Claude Desktop → Add Remote Server.

Endpoint: /mcp/sse
"""

import json
import os
import re
from typing import Any, AsyncGenerator
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

# Ladda miljövariabler
env_path = Path(__file__).parent.parent / "rapport_extraktor" / ".env"
load_dotenv(env_path)

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
            raise ValueError("SUPABASE_URL och SUPABASE_KEY måste vara satta")
        _client = create_client(url, key)
    return _client


# =============================================================================
# DATABASFUNKTIONER (kopierade från mcp_server/server.py)
# =============================================================================

def db_list_companies() -> list[dict]:
    """Lista alla bolag med antal perioder."""
    client = get_client()
    companies = client.table("companies").select("id, name, slug").order("name").execute()

    result = []
    for c in companies.data:
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

    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return []

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

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
    """Hämta finansiell data."""
    client = get_client()

    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    if period:
        match = re.search(r'Q(\d)\s*(\d{4})', period)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
            period_row = client.table("periods").select(
                "id, quarter, year, valuta, language, source_file, pdf_hash"
            ).eq("company_id", company_id).eq("quarter", quarter).eq("year", year).execute()
        else:
            return {"error": f"Ogiltigt periodformat: {period}. Använd t.ex. 'Q3 2024'"}
    else:
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
        "language": language,
        "source": {
            "file": p.get("source_file"),
            "pdf_hash": p.get("pdf_hash"),
            "language": language
        },
        "tables": {}
    }

    query = client.table("report_tables").select("*").eq("period_id", period_id)
    if statement_type:
        query = query.eq("table_type", statement_type)

    tables = query.order("page_number").execute()

    if tables.data:
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
        query = client.table("financial_data").select("*").eq("period_id", period_id)
        if statement_type:
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
    """Hämta nyckeltal (KPIs)."""
    client = get_client()

    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    if period:
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
    """Hämta textsektioner."""
    client = get_client()

    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    if period:
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
                "input_type": "query"
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()["data"][0]["embedding"]
    except Exception:
        pass
    return None


def db_search_sections(query: str, company_slug: str | None = None, use_hybrid: bool = True) -> list[dict]:
    """Sök i textsektioner."""
    client = get_client()

    if use_hybrid:
        try:
            query_embedding = get_query_embedding(query)
            if query_embedding:
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
                            "search_type": "hybrid"
                        } for r in result.data]
                except Exception:
                    pass

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
                        "search_type": "semantic"
                    } for r in result.data]
        except Exception:
            pass

    # Textsökning (fallback)
    if company_slug:
        company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
        if not company.data:
            company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

        if not company.data:
            return []

        company_id = company.data[0]["id"]
        periods = client.table("periods").select("id").eq("company_id", company_id).execute()
        period_ids = [p["id"] for p in periods.data]

        if not period_ids:
            return []

        sections = client.table("sections").select(
            "title, section_type, content, period_id, page_number"
        ).in_("period_id", period_ids).ilike("content", f"%{query}%").limit(10).execute()
    else:
        sections = client.table("sections").select(
            "title, section_type, content, period_id, page_number"
        ).ilike("content", f"%{query}%").limit(10).execute()

    results = []
    for s in sections.data:
        period = client.table("periods").select(
            "quarter, year, company_id, source_file"
        ).eq("id", s["period_id"]).execute()

        if period.data:
            p = period.data[0]
            company = client.table("companies").select("name").eq("id", p["company_id"]).execute()
            company_name = company.data[0]["name"] if company.data else "Okänt"

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
                "search_type": "text"
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


def db_get_charts(company_slug: str, period: str | None = None) -> dict:
    """Hämta grafer/diagram för en period."""
    client = get_client()

    company = client.table("companies").select("id, name").eq("slug", company_slug).execute()
    if not company.data:
        company = client.table("companies").select("id, name").ilike("name", f"%{company_slug}%").execute()

    if not company.data:
        return {"error": f"Bolag '{company_slug}' hittades inte"}

    company_id = company.data[0]["id"]
    company_name = company.data[0]["name"]

    if period:
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

    try:
        charts = client.table("charts").select("*").eq("period_id", period_id).order("page_number").execute()

        formatted_charts = []
        for c in charts.data:
            formatted_charts.append({
                "title": c["title"],
                "type": c["chart_type"],
                "page": c["page_number"],
                "x_axis": c.get("x_axis"),
                "y_axis": c.get("y_axis"),
                "estimated": c["estimated"],
                "data_points": c["data_points"]
            })

        return {
            "company": company_name,
            "period": period_str,
            "source": {
                "file": p.get("source_file"),
                "pdf_hash": p.get("pdf_hash")
            },
            "charts": formatted_charts
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
            "note": "Inga grafer tillgängliga"
        }


# =============================================================================
# MCP TOOL DEFINITIONS
# =============================================================================

MCP_TOOLS = [
    {
        "name": "list_companies",
        "description": "Lista alla bolag i databasen med antal tillgängliga perioder",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_periods",
        "description": "Visa alla tillgängliga perioder (kvartal) för ett bolag",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {
                    "type": "string",
                    "description": "Bolagets namn eller slug (t.ex. 'vitrolife' eller 'Vitrolife AB')"
                }
            },
            "required": ["company"]
        }
    },
    {
        "name": "get_financials",
        "description": "Hämta finansiell data (resultaträkning, balansräkning, kassaflöde) för ett bolag och period",
        "inputSchema": {
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
    },
    {
        "name": "get_kpis",
        "description": "Hämta nyckeltal (KPIs) som marginaler, tillväxt etc. för ett bolag",
        "inputSchema": {
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
    },
    {
        "name": "get_sections",
        "description": "Hämta textsektioner som VD-kommentar, marknadsöversikt etc. för ett bolag",
        "inputSchema": {
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
    },
    {
        "name": "search_sections",
        "description": "Sök i alla textsektioner efter specifika termer eller ämnen. Använder hybrid sökning (text + semantisk) som default för bästa resultat.",
        "inputSchema": {
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
    },
    {
        "name": "compare_periods",
        "description": "Jämför finansiell data mellan två perioder för samma bolag",
        "inputSchema": {
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
    },
    {
        "name": "get_charts",
        "description": "Hämta extraherade grafer och diagram med datapunkter för ett bolag",
        "inputSchema": {
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
    }
]


def call_tool(name: str, arguments: dict[str, Any]) -> dict:
    """Anropa ett verktyg och returnera resultat."""
    try:
        if name == "list_companies":
            return {"result": db_list_companies()}

        elif name == "get_periods":
            return {"result": db_get_periods(arguments["company"])}

        elif name == "get_financials":
            return {"result": db_get_financials(
                arguments["company"],
                arguments.get("period"),
                arguments.get("statement_type")
            )}

        elif name == "get_kpis":
            return {"result": db_get_kpis(
                arguments["company"],
                arguments.get("period")
            )}

        elif name == "get_sections":
            return {"result": db_get_sections(
                arguments["company"],
                arguments.get("period"),
                arguments.get("section_type")
            )}

        elif name == "search_sections":
            return {"result": db_search_sections(
                arguments["query"],
                arguments.get("company"),
                use_hybrid=arguments.get("use_hybrid", True)
            )}

        elif name == "compare_periods":
            return {"result": db_compare_periods(
                arguments["company"],
                arguments["period1"],
                arguments["period2"],
                arguments.get("statement_type", "income_statement")
            )}

        elif name == "get_charts":
            return {"result": db_get_charts(
                arguments["company"],
                arguments.get("period")
            )}

        else:
            return {"error": f"Okänt verktyg: {name}"}

    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# SSE MCP PROTOCOL HANDLER
# =============================================================================

class MCPSession:
    """Hanterar en MCP-session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.initialized = False

    def handle_message(self, message: dict) -> dict | None:
        """Hantera ett MCP-meddelande och returnera svar."""
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        if method == "initialize":
            self.initialized = True
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "rapport-extraktor",
                        "version": "1.0.0"
                    }
                }
            }

        elif method == "notifications/initialized":
            # Klient bekräftar initialisering - inget svar behövs
            return None

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": MCP_TOOLS
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            result = call_tool(tool_name, arguments)

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, indent=2)
                    }]
                }
            }

        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {}
            }

        else:
            # Okänd metod
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }


# Global session storage
_sessions: dict[str, MCPSession] = {}


def get_or_create_session(session_id: str) -> MCPSession:
    """Hämta eller skapa en session."""
    if session_id not in _sessions:
        _sessions[session_id] = MCPSession(session_id)
    return _sessions[session_id]


def format_sse_message(data: dict, event: str = "message") -> str:
    """Formatera data som SSE-meddelande."""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {json_data}\n\n"
