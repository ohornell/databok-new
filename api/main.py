"""
FastAPI backend för rapport-extraktion.

Endpoints:
  POST /extract                     - Ladda upp PDF och starta extraktion
  GET  /status/{id}                 - Kolla status på ett jobb (inkl. detaljerade loggar)
  GET  /download/{id}               - Ladda ner Excel-fil
  GET  /jobs                        - Lista alla jobb
  GET  /companies                   - Lista alla bolag
  GET  /companies/{slug}/periods    - Lista perioder för ett bolag
  POST /companies/{slug}/excel      - Generera Excel från databas
  GET  /companies/{slug}/periods/{period}/data - Hämta all data för en period

Kör lokalt: uvicorn api.main:app --reload
"""

import asyncio
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# MCP Remote
from api.mcp_remote import (
    get_or_create_session,
    format_sse_message,
    MCP_TOOLS,
    call_tool as mcp_call_tool,
)

# Lägg till rapport_extraktor i path
sys.path.insert(0, str(Path(__file__).parent.parent / "rapport_extraktor"))

from dotenv import load_dotenv
# Ladda .env från rapport_extraktor-mappen
env_path = Path(__file__).parent.parent / "rapport_extraktor" / ".env"
load_dotenv(env_path)

from pipeline import extract_pdf_multi_pass
from excel_builder import build_databook
from supabase_client import (
    get_or_create_company,
    list_companies as db_list_companies,
    get_company_by_slug,
    load_all_periods,
    load_period,
    get_client,
)
from anthropic import AsyncAnthropic

# ============================================
# APP CONFIG
# ============================================

app = FastAPI(
    title="Rapport Extraktor API",
    description="""
## PDF-extraktion och databoksgenering för finansiella rapporter

### Flöde för enskild fil
1. **POST /extract** - Ladda upp PDF och starta extraktion (returnerar job_id)
2. **GET /status/{job_id}** - Polla status tills `status == "done"`
3. **GET /download/{job_id}** - Ladda ner genererad Excel-fil

### Flöde för batch (flera filer)
1. **POST /extract/batch** - Ladda upp flera PDF:er (returnerar batch_id + job_ids)
2. **GET /extract/batch/{batch_id}** - Polla status för alla filer i batchen
3. **GET /download/{job_id}** - Ladda ner Excel per fil

### Befintlig data
- **GET /companies** - Lista alla bolag i databasen
- **GET /companies/{slug}/periods** - Lista tillgängliga perioder för ett bolag
- **GET /companies/{slug}/periods/{period}/data** - Hämta all data för en period
- **POST /companies/{slug}/excel** - Generera Excel från befintlig databas-data

### Status-värden (per fil)
- `pending` - Väntar på att starta
- `processing` - Bearbetar PDF
- `pass_1` - Strukturidentifiering (25%)
- `pass_2_3` - Tabellextraktion (50%)
- `validating` - Validerar data (80%)
- `done` - Klar (100%)
- `failed` - Misslyckades (se error-fält)

### Batch-status
- `pending` - Inga filer har startats
- `processing` - Filer bearbetas
- `done` - Alla filer klara
- `partial_failure` - Vissa filer misslyckades
""",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS för Lovable
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Sätt till din Lovable-domän i produktion
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# JOB STORAGE (in-memory, byt till Redis i prod)
# ============================================

jobs: dict[str, dict] = {}
batches: dict[str, dict] = {}

# ============================================
# STORAGE HELPERS (lokal eller Supabase)
# ============================================

USE_CLOUD_STORAGE = os.environ.get("USE_CLOUD_STORAGE", "").lower() == "true"
STORAGE_BUCKET = "pdfs"  # Supabase Storage bucket


async def save_pdf_file(content: bytes, filename: str, job_id: str) -> str:
    """
    Spara PDF-fil lokalt eller i Supabase Storage.
    Returnerar sökvägen till filen.
    """
    if USE_CLOUD_STORAGE:
        # Supabase Storage
        client = get_client()
        storage_path = f"uploads/{job_id}/{filename}"

        # Ladda upp till Supabase Storage
        client.storage.from_(STORAGE_BUCKET).upload(
            storage_path,
            content,
            file_options={"content-type": "application/pdf"}
        )

        # Ladda ner till temp-fil för extraktion
        temp_dir = tempfile.mkdtemp()
        local_path = os.path.join(temp_dir, filename)

        # Hämta filen tillbaka
        data = client.storage.from_(STORAGE_BUCKET).download(storage_path)
        with open(local_path, "wb") as f:
            f.write(data)

        return local_path
    else:
        # Lokal lagring
        temp_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(temp_dir, filename)
        with open(pdf_path, "wb") as f:
            f.write(content)
        return pdf_path


async def save_excel_file(local_path: str, job_id: str, filename: str) -> str:
    """
    Spara Excel-fil lokalt eller i Supabase Storage.
    Returnerar sökvägen/URL till filen.
    """
    if USE_CLOUD_STORAGE:
        client = get_client()
        storage_path = f"results/{job_id}/{filename}"

        with open(local_path, "rb") as f:
            content = f.read()

        client.storage.from_(STORAGE_BUCKET).upload(
            storage_path,
            content,
            file_options={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        )

        # Returnera signerad URL (giltig 1 timme)
        url = client.storage.from_(STORAGE_BUCKET).create_signed_url(storage_path, 3600)
        return url.get("signedURL", local_path)
    else:
        return local_path


# ============================================
# PYDANTIC MODELS
# ============================================

class PassInfo(BaseModel):
    """Info om ett extraktions-pass."""
    pass_number: int
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    cost_sek: float


class RetryStats(BaseModel):
    """Statistik för validering och retry."""
    retry_count: int
    tables_retried: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    cost_sek: float


class ValidationInfo(BaseModel):
    """Valideringsresultat."""
    is_valid: bool
    error_count: int
    warning_count: int
    errors: list[dict] = []


class PipelineInfo(BaseModel):
    """Detaljerad pipeline-info."""
    passes: list[PassInfo] = []
    retry_stats: Optional[RetryStats] = None
    validation_tables: Optional[ValidationInfo] = None
    validation_sections: Optional[ValidationInfo] = None
    total_cost_sek: float
    total_elapsed_seconds: float
    total_input_tokens: int
    total_output_tokens: int


class JobStatus(BaseModel):
    """Status för ett extraktionsjobb."""
    job_id: str
    status: str  # pending, processing, pass_1, pass_2_3, validating, done, failed
    progress: int  # 0-100
    company: str
    filename: str
    created_at: str
    cost_sek: Optional[float] = None
    error: Optional[str] = None
    # Detaljerad info (endast när done)
    pipeline_info: Optional[PipelineInfo] = None
    tables_count: Optional[int] = None
    sections_count: Optional[int] = None
    charts_count: Optional[int] = None


class ExtractResponse(BaseModel):
    job_id: str
    message: str


class BatchFileStatus(BaseModel):
    """Status för en fil i en batch."""
    job_id: str
    filename: str
    status: str
    progress: int
    cost_sek: Optional[float] = None
    error: Optional[str] = None


class BatchStatus(BaseModel):
    """Status för en batch-extraktion."""
    batch_id: str
    company: str
    total_files: int
    completed_files: int
    failed_files: int
    status: str  # pending, processing, done, partial_failure
    files: list[BatchFileStatus]
    total_cost_sek: float
    created_at: str


class BatchResponse(BaseModel):
    batch_id: str
    message: str
    job_ids: list[str]


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str


class PeriodResponse(BaseModel):
    quarter: int
    year: int
    period_label: str
    valuta: Optional[str]
    language: Optional[str]
    tables_count: int
    sections_count: int
    has_extraction_meta: bool


# ============================================
# BACKGROUND TASK
# ============================================

async def run_extraction(job_id: str, pdf_path: str, company_name: str, filename: str):
    """Kör extraktion i bakgrunden."""
    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 10

        # Hämta eller skapa bolag
        company = get_or_create_company(company_name)
        company_id = company["id"]

        # Skapa Anthropic-klient
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY saknas")

        client = AsyncAnthropic(api_key=api_key, timeout=300)
        semaphore = asyncio.Semaphore(10)

        # Progress callback
        def on_progress(path: str, status: str, info: dict | None):
            progress_map = {
                "extracting": 15,
                "pass_1": 25,
                "pass_2_3": 50,
                "validating": 80,
                "done": 100,
            }
            jobs[job_id]["status"] = status
            jobs[job_id]["progress"] = progress_map.get(status, jobs[job_id]["progress"])
            if info and "cost_sek" in info:
                jobs[job_id]["cost_sek"] = info["cost_sek"]

        # Kör extraktion
        result = await extract_pdf_multi_pass(
            pdf_path=pdf_path,
            client=client,
            semaphore=semaphore,
            company_id=company_id,
            progress_callback=on_progress,
            use_cache=True
        )

        # Skapa Excel
        excel_path = pdf_path.replace(".pdf", ".xlsx")
        build_databook([result], excel_path)

        # Hämta pipeline-info
        pipeline_info = result.get("_pipeline_info", {})

        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["excel_path"] = excel_path
        jobs[job_id]["cost_sek"] = pipeline_info.get("total_cost_sek", 0)

        # Spara detaljerad info
        jobs[job_id]["result"] = result
        jobs[job_id]["pipeline_info"] = pipeline_info
        jobs[job_id]["tables_count"] = len(result.get("tables", []))
        jobs[job_id]["sections_count"] = len(result.get("sections", []))
        jobs[job_id]["charts_count"] = len(result.get("charts", []))

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        import traceback
        jobs[job_id]["traceback"] = traceback.format_exc()
        print(f"[ERROR] Job {job_id}: {e}")


# ============================================
# EXTRACTION ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """
    Health check för API:t.

    Returnerar status "ok" om API:t körs korrekt.
    Används för att verifiera att tjänsten är igång.
    """
    return {"status": "ok", "service": "rapport-extraktor-api", "version": "1.0.0"}


@app.post("/extract", response_model=ExtractResponse)
async def extract_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    company: str = Form(...)
):
    """
    Ladda upp PDF och starta extraktion.

    - **file**: PDF-fil att extrahera
    - **company**: Bolagsnamn

    Returnerar job_id som kan användas för att kolla status.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Endast PDF-filer stöds")

    # Skapa jobb-ID
    job_id = str(uuid.uuid4())[:8]

    # Läs fil och spara (lokalt eller i Supabase)
    content = await file.read()
    pdf_path = await save_pdf_file(content, file.filename, job_id)

    # Skapa jobb
    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "company": company,
        "filename": file.filename,
        "pdf_path": pdf_path,
        "created_at": datetime.now().isoformat(),
        "cost_sek": None,
        "error": None,
        "excel_path": None,
        "result": None,
        "pipeline_info": None,
        "tables_count": None,
        "sections_count": None,
        "charts_count": None,
    }

    # Starta extraktion i bakgrunden
    background_tasks.add_task(run_extraction, job_id, pdf_path, company, file.filename)

    return ExtractResponse(
        job_id=job_id,
        message=f"Extraktion startad för {file.filename}"
    )


@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    """
    Kolla status på ett extraktionsjobb.

    När status är "done" inkluderas detaljerad pipeline-info:
    - passes: Info om varje pass (tokens, kostnad, tid)
    - retry_stats: Validering och retry-statistik
    - validation: Valideringsresultat för tabeller och sektioner

    Status:
    - pending: Väntar
    - processing: Bearbetar
    - pass_1: Strukturidentifiering
    - pass_2_3: Tabellextraktion
    - validating: Validerar
    - done: Klar
    - failed: Misslyckades
    """
    if job_id not in jobs:
        raise HTTPException(404, f"Jobb {job_id} hittades inte")

    job = jobs[job_id]

    # Bygg pipeline_info om jobbet är klart
    pipeline_info_response = None
    if job["status"] == "done" and job.get("pipeline_info"):
        pi = job["pipeline_info"]

        passes = []
        for p in pi.get("passes", []):
            passes.append(PassInfo(
                pass_number=p["pass"],
                model=p["model"],
                input_tokens=p["input_tokens"],
                output_tokens=p["output_tokens"],
                elapsed_seconds=p["elapsed_seconds"],
                cost_sek=p["cost_sek"]
            ))

        retry_stats = None
        if pi.get("retry_stats"):
            rs = pi["retry_stats"]
            retry_stats = RetryStats(
                retry_count=rs["retry_count"],
                tables_retried=rs["tables_retried"],
                input_tokens=rs["input_tokens"],
                output_tokens=rs["output_tokens"],
                elapsed_seconds=rs["elapsed_seconds"],
                cost_sek=rs["cost_sek"]
            )

        validation_tables = None
        validation_sections = None
        if pi.get("validation"):
            v = pi["validation"]
            if v.get("tables"):
                vt = v["tables"]
                validation_tables = ValidationInfo(
                    is_valid=vt["is_valid"],
                    error_count=vt["error_count"],
                    warning_count=vt["warning_count"],
                    errors=vt.get("errors", [])
                )
            if v.get("sections"):
                vs = v["sections"]
                validation_sections = ValidationInfo(
                    is_valid=vs["is_valid"],
                    error_count=vs["error_count"],
                    warning_count=vs["warning_count"],
                    errors=vs.get("warnings", [])
                )

        # Beräkna totaler
        total_input = sum(p.input_tokens for p in passes)
        total_output = sum(p.output_tokens for p in passes)
        if retry_stats:
            total_input += retry_stats.input_tokens
            total_output += retry_stats.output_tokens

        pipeline_info_response = PipelineInfo(
            passes=passes,
            retry_stats=retry_stats,
            validation_tables=validation_tables,
            validation_sections=validation_sections,
            total_cost_sek=pi.get("total_cost_sek", 0),
            total_elapsed_seconds=pi.get("total_elapsed_seconds", 0),
            total_input_tokens=total_input,
            total_output_tokens=total_output
        )

    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        progress=job["progress"],
        company=job["company"],
        filename=job["filename"],
        created_at=job["created_at"],
        cost_sek=job.get("cost_sek"),
        error=job.get("error"),
        pipeline_info=pipeline_info_response,
        tables_count=job.get("tables_count"),
        sections_count=job.get("sections_count"),
        charts_count=job.get("charts_count"),
    )


@app.get("/download/{job_id}")
async def download_excel(job_id: str):
    """
    Ladda ner genererad Excel-fil.

    Returnerar en Excel-fil (.xlsx) med extraherad finansiell data.
    Jobbet måste ha status "done" för att kunna ladda ner filen.

    Filnamnet blir: `{bolag}_{pdf-namn}_databok.xlsx`
    """
    if job_id not in jobs:
        raise HTTPException(404, f"Jobb {job_id} hittades inte")

    job = jobs[job_id]

    if job["status"] != "done":
        raise HTTPException(400, f"Jobb är inte klart (status: {job['status']})")

    excel_path = job.get("excel_path")
    if not excel_path or not os.path.exists(excel_path):
        raise HTTPException(500, "Excel-fil kunde inte hittas")

    # Skapa filnamn för download
    download_name = f"{job['company']}_{job['filename'].replace('.pdf', '')}_databok.xlsx"

    return FileResponse(
        excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name
    )


@app.get("/jobs")
async def list_jobs():
    """
    Lista alla extraktionsjobb.

    Returnerar alla jobb med grundläggande status-info.
    Använd `/status/{job_id}` för detaljerad info om ett specifikt jobb.
    """
    return {
        "jobs": [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "progress": j["progress"],
                "company": j["company"],
                "filename": j["filename"],
                "created_at": j["created_at"],
                "cost_sek": j.get("cost_sek"),
                "tables_count": j.get("tables_count"),
                "sections_count": j.get("sections_count"),
            }
            for j in jobs.values()
        ]
    }


# ============================================
# BATCH ENDPOINTS
# ============================================

@app.post("/extract/batch", response_model=BatchResponse)
async def extract_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    company: str = Form(...)
):
    """
    Ladda upp flera PDF-filer och starta batch-extraktion.

    - **files**: Lista med PDF-filer att extrahera
    - **company**: Bolagsnamn (samma för alla filer)

    Returnerar batch_id och job_ids för varje fil.
    Använd `/extract/batch/{batch_id}` för att kolla status på alla filer.
    """
    if not files:
        raise HTTPException(400, "Inga filer uppladdade")

    # Validera alla filer först
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"Endast PDF-filer stöds: {file.filename}")

    # Skapa batch
    batch_id = str(uuid.uuid4())[:8]
    job_ids = []

    batches[batch_id] = {
        "batch_id": batch_id,
        "company": company,
        "job_ids": [],
        "created_at": datetime.now().isoformat(),
    }

    # Skapa jobb för varje fil
    for file in files:
        job_id = str(uuid.uuid4())[:8]
        job_ids.append(job_id)
        batches[batch_id]["job_ids"].append(job_id)

        # Läs filinnehåll
        content = await file.read()

        # Spara PDF (lokalt eller i Supabase)
        pdf_path = await save_pdf_file(content, file.filename, job_id)

        # Skapa jobb
        jobs[job_id] = {
            "job_id": job_id,
            "batch_id": batch_id,
            "status": "pending",
            "progress": 0,
            "company": company,
            "filename": file.filename,
            "pdf_path": pdf_path,
            "created_at": datetime.now().isoformat(),
            "cost_sek": None,
            "error": None,
            "excel_path": None,
            "result": None,
            "pipeline_info": None,
            "tables_count": None,
            "sections_count": None,
            "charts_count": None,
        }

        # Starta extraktion i bakgrunden
        background_tasks.add_task(run_extraction, job_id, pdf_path, company, file.filename)

    return BatchResponse(
        batch_id=batch_id,
        message=f"Batch-extraktion startad för {len(files)} filer",
        job_ids=job_ids
    )


@app.get("/extract/batch/{batch_id}", response_model=BatchStatus)
async def get_batch_status(batch_id: str):
    """
    Kolla status på en batch-extraktion.

    Returnerar:
    - **total_files**: Totalt antal filer
    - **completed_files**: Antal färdiga filer
    - **failed_files**: Antal misslyckade filer
    - **status**: Övergripande status (pending, processing, done, partial_failure)
    - **files**: Lista med status för varje fil
    - **total_cost_sek**: Total kostnad för alla filer
    """
    if batch_id not in batches:
        raise HTTPException(404, f"Batch {batch_id} hittades inte")

    batch = batches[batch_id]
    job_ids = batch["job_ids"]

    # Samla status för varje fil
    file_statuses = []
    completed = 0
    failed = 0
    total_cost = 0.0

    for job_id in job_ids:
        if job_id in jobs:
            job = jobs[job_id]
            file_statuses.append(BatchFileStatus(
                job_id=job_id,
                filename=job["filename"],
                status=job["status"],
                progress=job["progress"],
                cost_sek=job.get("cost_sek"),
                error=job.get("error")
            ))

            if job["status"] == "done":
                completed += 1
                if job.get("cost_sek"):
                    total_cost += job["cost_sek"]
            elif job["status"] == "failed":
                failed += 1

    # Beräkna övergripande status
    total_files = len(job_ids)
    if completed == total_files:
        overall_status = "done"
    elif failed == total_files:
        overall_status = "failed"
    elif completed + failed == total_files:
        overall_status = "partial_failure"
    elif completed > 0 or failed > 0:
        overall_status = "processing"
    else:
        overall_status = "pending"

    return BatchStatus(
        batch_id=batch_id,
        company=batch["company"],
        total_files=total_files,
        completed_files=completed,
        failed_files=failed,
        status=overall_status,
        files=file_statuses,
        total_cost_sek=total_cost,
        created_at=batch["created_at"]
    )


@app.get("/batches")
async def list_batches():
    """
    Lista alla batch-jobb.

    Returnerar en översikt av alla batches med antal filer och status.
    """
    result = []
    for batch_id, batch in batches.items():
        job_ids = batch["job_ids"]
        completed = sum(1 for jid in job_ids if jobs.get(jid, {}).get("status") == "done")
        failed = sum(1 for jid in job_ids if jobs.get(jid, {}).get("status") == "failed")

        result.append({
            "batch_id": batch_id,
            "company": batch["company"],
            "total_files": len(job_ids),
            "completed_files": completed,
            "failed_files": failed,
            "created_at": batch["created_at"]
        })

    return {"batches": result}


# ============================================
# COMPANY/PERIOD ENDPOINTS
# ============================================

@app.get("/companies", response_model=list[CompanyResponse])
async def list_companies():
    """
    Lista alla bolag i databasen.

    Returnerar en lista med bolag som har extraherad data.
    Varje bolag har ett unikt `slug` som används i andra endpoints.
    """
    companies = db_list_companies()
    return [
        CompanyResponse(
            id=c["id"],
            name=c["name"],
            slug=c["slug"]
        )
        for c in companies
    ]


@app.get("/companies/{slug}/periods", response_model=list[PeriodResponse])
async def get_company_periods(slug: str):
    """
    Lista alla perioder för ett bolag.

    Returnerar info om varje period inklusive antal tabeller och sektioner.
    """
    company = get_company_by_slug(slug)
    if not company:
        raise HTTPException(404, f"Bolag '{slug}' hittades inte")

    client = get_client()

    # Hämta perioder med counts
    periods = client.table("periods").select(
        "id, quarter, year, valuta, language, extraction_meta"
    ).eq("company_id", company["id"]).order("year").order("quarter").execute()

    result = []
    for p in periods.data:
        # Räkna tabeller och sektioner
        tables_count = client.table("report_tables").select(
            "id", count="exact"
        ).eq("period_id", p["id"]).execute()

        sections_count = client.table("sections").select(
            "id", count="exact"
        ).eq("period_id", p["id"]).execute()

        result.append(PeriodResponse(
            quarter=p["quarter"],
            year=p["year"],
            period_label=f"Q{p['quarter']} {p['year']}",
            valuta=p.get("valuta"),
            language=p.get("language"),
            tables_count=tables_count.count or 0,
            sections_count=sections_count.count or 0,
            has_extraction_meta=p.get("extraction_meta") is not None
        ))

    return result


@app.post("/companies/{slug}/excel")
async def generate_excel_from_db(
    slug: str,
    periods: Optional[list[str]] = Query(None, description="Perioder att inkludera, t.ex. ['Q1 2024', 'Q2 2024']")
):
    """
    Generera Excel-fil från befintlig data i databasen.

    Om inga perioder anges genereras Excel för alla perioder.
    """
    company = get_company_by_slug(slug)
    if not company:
        raise HTTPException(404, f"Bolag '{slug}' hittades inte")

    # Ladda alla perioder
    all_periods = load_all_periods(company["id"])

    if not all_periods:
        raise HTTPException(404, f"Inga perioder hittades för {company['name']}")

    # Filtrera om specifika perioder anges
    if periods:
        filtered = []
        for p in all_periods:
            period_label = p.get("metadata", {}).get("period", "")
            if period_label in periods:
                filtered.append(p)
        if not filtered:
            raise HTTPException(404, f"Inga av de angivna perioderna hittades: {periods}")
        all_periods = filtered

    # Skapa Excel i temp-mapp
    temp_dir = tempfile.mkdtemp()
    excel_path = os.path.join(temp_dir, f"{company['slug']}_databok.xlsx")

    build_databook(all_periods, excel_path)

    # Skapa filnamn för download
    if periods:
        periods_str = "_".join(p.replace(" ", "") for p in periods)
        download_name = f"{company['name']}_{periods_str}_databok.xlsx"
    else:
        download_name = f"{company['name']}_alla_perioder_databok.xlsx"

    return FileResponse(
        excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name
    )


@app.get("/companies/{slug}/periods/{period}/data")
async def get_period_data(slug: str, period: str):
    """
    Hämta all data för en specifik period.

    Exempel: /companies/vitrolife/periods/Q3%202024/data

    Returnerar:
    - metadata (bolag, period, valuta)
    - tables (alla tabeller)
    - sections (alla textsektioner)
    - charts (alla grafer)
    """
    company = get_company_by_slug(slug)
    if not company:
        raise HTTPException(404, f"Bolag '{slug}' hittades inte")

    # Parsa period (Q1 2024 -> quarter=1, year=2024)
    match = re.search(r'Q(\d)\s*(\d{4})', period)
    if not match:
        raise HTTPException(400, f"Ogiltigt periodformat: {period}. Använd t.ex. 'Q1 2024'")

    quarter = int(match.group(1))
    year = int(match.group(2))

    data = load_period(company["id"], quarter, year)
    if not data:
        raise HTTPException(404, f"Period {period} hittades inte för {company['name']}")

    return data


# ============================================
# MCP REMOTE SSE ENDPOINTS
# ============================================

import json
import asyncio
from typing import AsyncGenerator

# Session storage för MCP
mcp_sessions: dict[str, dict] = {}


async def sse_event_generator(session_id: str) -> AsyncGenerator[str, None]:
    """Generator för SSE-events."""
    session = get_or_create_session(session_id)
    mcp_sessions[session_id] = {"queue": asyncio.Queue(), "active": True}

    try:
        # Skicka endpoint för messages
        yield f"event: endpoint\ndata: /mcp/messages?session_id={session_id}\n\n"

        # Vänta på meddelanden från klienten och skicka svar
        while mcp_sessions[session_id]["active"]:
            try:
                # Vänta på meddelande från kön (med timeout)
                response = await asyncio.wait_for(
                    mcp_sessions[session_id]["queue"].get(),
                    timeout=30
                )
                if response:
                    yield f"event: message\ndata: {json.dumps(response, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                # Skicka keep-alive
                yield ": keepalive\n\n"
    finally:
        mcp_sessions.pop(session_id, None)


@app.get("/mcp/sse")
async def mcp_sse():
    """
    MCP Server-Sent Events endpoint.

    Användare ansluter via Claude Desktop:
    1. Settings → MCP → Add Remote Server
    2. URL: https://din-domän.com/mcp/sse

    Protokollet:
    1. Klienten ansluter till /mcp/sse
    2. Servern skickar `endpoint` event med URL för /mcp/messages
    3. Klienten skickar JSON-RPC meddelanden till /mcp/messages
    4. Servern svarar via SSE-strömmen
    """
    session_id = str(uuid.uuid4())

    return StreamingResponse(
        sse_event_generator(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/mcp/messages")
async def mcp_messages(request: Request, session_id: str):
    """
    Hantera MCP JSON-RPC meddelanden.

    Klienten skickar JSON-RPC requests hit, och svaren
    skickas tillbaka via SSE-strömmen.
    """
    if session_id not in mcp_sessions:
        raise HTTPException(400, "Ogiltig session")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Ogiltig JSON")

    session = get_or_create_session(session_id)
    response = session.handle_message(body)

    if response:
        # Lägg svar i kön för SSE
        await mcp_sessions[session_id]["queue"].put(response)

    return {"status": "ok"}


@app.get("/mcp/tools")
async def list_mcp_tools():
    """
    Lista tillgängliga MCP-verktyg.

    Returnerar alla verktyg som finns tillgängliga via MCP-protokollet.
    Användbart för debugging och dokumentation.
    """
    return {"tools": MCP_TOOLS}


@app.post("/mcp/call/{tool_name}")
async def call_mcp_tool(tool_name: str, request: Request):
    """
    Direkt anrop av MCP-verktyg via REST.

    Användbart för testing utan att behöva sätta upp MCP-klient.

    Exempel:
    ```
    POST /mcp/call/list_companies
    {}

    POST /mcp/call/get_financials
    {"company": "vitrolife", "period": "Q3 2024"}
    ```
    """
    try:
        arguments = await request.json()
    except Exception:
        arguments = {}

    result = mcp_call_tool(tool_name, arguments)
    return result
