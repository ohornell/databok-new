-- ============================================
-- MIGRATION 003: Robust Statistik och Atomisk Sparning
-- ============================================
--
-- Kör denna migration i Supabase SQL Editor:
-- 1. Öppna Supabase Dashboard > SQL Editor
-- 2. Klistra in och kör varje sektion separat (för enklare debugging)
--
-- VIKTIGT: Kör INTE om du redan har kört denna migration!
-- ============================================

-- ============================================
-- STEG 1: Nya kolumner på periods (denormalisering)
-- ============================================
-- Dessa kolumner lagrar aggregerad data för snabba stats-queries

ALTER TABLE periods ADD COLUMN IF NOT EXISTS
    extraction_status TEXT DEFAULT 'pending'
    CHECK (extraction_status IN ('pending', 'extracting', 'success', 'partial', 'failed'));

ALTER TABLE periods ADD COLUMN IF NOT EXISTS tables_count INTEGER DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS sections_count INTEGER DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS charts_count INTEGER DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS embeddings_count INTEGER DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS cost_sek NUMERIC(10,4) DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS extraction_time_seconds NUMERIC(10,2) DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS error_count INTEGER DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS warning_count INTEGER DEFAULT 0;
ALTER TABLE periods ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Index för snabb filtrering på status
CREATE INDEX IF NOT EXISTS idx_periods_company_status ON periods(company_id, extraction_status);
CREATE INDEX IF NOT EXISTS idx_periods_status ON periods(extraction_status);

-- ============================================
-- STEG 2: Ny tabell för explicita extraktionsfel
-- ============================================

CREATE TABLE IF NOT EXISTS extraction_errors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_id UUID REFERENCES periods(id) ON DELETE CASCADE,
    error_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'error', 'warning')),
    component TEXT,  -- 'tables', 'sections', 'charts', 'embeddings', 'database'
    details JSONB,
    resolved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extraction_errors_period ON extraction_errors(period_id);
CREATE INDEX IF NOT EXISTS idx_extraction_errors_unresolved ON extraction_errors(period_id) WHERE resolved = false;
CREATE INDEX IF NOT EXISTS idx_extraction_errors_severity ON extraction_errors(severity);

-- ============================================
-- STEG 3: Atomisk upsert-funktion
-- ============================================
-- Denna funktion säkerställer att parallella extraktioner av samma period
-- inte orsakar race conditions genom att använda advisory locks.

CREATE OR REPLACE FUNCTION upsert_period_atomic(
    p_company_id UUID,
    p_quarter INTEGER,
    p_year INTEGER,
    p_valuta TEXT,
    p_language TEXT,
    p_pdf_hash TEXT,
    p_source_file TEXT,
    p_extraction_meta JSONB,
    p_tables_count INTEGER,
    p_sections_count INTEGER,
    p_charts_count INTEGER,
    p_cost_sek NUMERIC,
    p_extraction_time_seconds NUMERIC
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_period_id UUID;
    v_lock_key BIGINT;
BEGIN
    -- Generera deterministisk lock-nyckel från company_id + quarter + year
    -- Detta garanterar att samma period alltid får samma lock
    v_lock_key := hashtext(p_company_id::text || p_quarter::text || p_year::text);

    -- Advisory lock - blockerar andra transaktioner som försöker uppdatera samma period
    -- Låset släpps automatiskt när transaktionen committas
    PERFORM pg_advisory_xact_lock(v_lock_key);

    -- Ta bort befintlig period (CASCADE tar bort relaterade rader)
    DELETE FROM periods
    WHERE company_id = p_company_id AND quarter = p_quarter AND year = p_year;

    -- Skapa ny period med denormaliserade värden
    INSERT INTO periods (
        company_id, quarter, year, valuta, language, pdf_hash, source_file,
        extraction_meta, extraction_status,
        tables_count, sections_count, charts_count,
        cost_sek, extraction_time_seconds,
        updated_at
    )
    VALUES (
        p_company_id, p_quarter, p_year, p_valuta, p_language, p_pdf_hash, p_source_file,
        p_extraction_meta, 'extracting',
        p_tables_count, p_sections_count, p_charts_count,
        p_cost_sek, p_extraction_time_seconds,
        NOW()
    )
    RETURNING id INTO v_period_id;

    RETURN v_period_id;
END;
$$;

-- ============================================
-- STEG 4: Snabb global statistik-funktion
-- ============================================
-- Ersätter 6000+ queries med 1 query för /stats endpoint

CREATE OR REPLACE FUNCTION get_global_stats()
RETURNS TABLE (
    company_id UUID,
    company_name TEXT,
    company_slug TEXT,
    reports_count BIGINT,
    tables_total BIGINT,
    sections_total BIGINT,
    charts_total BIGINT,
    embeddings_total BIGINT,
    cost_total NUMERIC,
    time_total NUMERIC,
    success_count BIGINT,
    partial_count BIGINT,
    failed_count BIGINT
)
LANGUAGE SQL
STABLE
AS $$
    SELECT
        c.id AS company_id,
        c.name AS company_name,
        c.slug AS company_slug,
        COUNT(p.id) AS reports_count,
        COALESCE(SUM(p.tables_count), 0) AS tables_total,
        COALESCE(SUM(p.sections_count), 0) AS sections_total,
        COALESCE(SUM(p.charts_count), 0) AS charts_total,
        COALESCE(SUM(p.embeddings_count), 0) AS embeddings_total,
        COALESCE(SUM(p.cost_sek), 0) AS cost_total,
        COALESCE(SUM(p.extraction_time_seconds), 0) AS time_total,
        COUNT(p.id) FILTER (WHERE p.extraction_status = 'success') AS success_count,
        COUNT(p.id) FILTER (WHERE p.extraction_status = 'partial') AS partial_count,
        COUNT(p.id) FILTER (WHERE p.extraction_status = 'failed') AS failed_count
    FROM companies c
    LEFT JOIN periods p ON c.id = p.company_id
    GROUP BY c.id, c.name, c.slug
    ORDER BY c.name;
$$;

-- ============================================
-- STEG 5: Snabb bolagsstatistik-funktion
-- ============================================
-- Ersätter 500+ queries med 1 query för /stats/{slug} endpoint

CREATE OR REPLACE FUNCTION get_company_stats(p_company_id UUID)
RETURNS TABLE (
    period_id UUID,
    quarter INTEGER,
    year INTEGER,
    extraction_status TEXT,
    tables_count INTEGER,
    sections_count INTEGER,
    charts_count INTEGER,
    embeddings_count INTEGER,
    cost_sek NUMERIC,
    extraction_time_seconds NUMERIC,
    error_count INTEGER,
    warning_count INTEGER,
    created_at TIMESTAMPTZ
)
LANGUAGE SQL
STABLE
AS $$
    SELECT
        p.id AS period_id,
        p.quarter,
        p.year,
        p.extraction_status,
        p.tables_count,
        p.sections_count,
        p.charts_count,
        p.embeddings_count,
        p.cost_sek,
        p.extraction_time_seconds,
        p.error_count,
        p.warning_count,
        p.created_at
    FROM periods p
    WHERE p.company_id = p_company_id
    ORDER BY p.year DESC, p.quarter DESC;
$$;

-- ============================================
-- STEG 6: Funktion för att uppdatera period-status
-- ============================================

CREATE OR REPLACE FUNCTION update_period_status(
    p_period_id UUID,
    p_status TEXT,
    p_error_count INTEGER DEFAULT 0,
    p_warning_count INTEGER DEFAULT 0,
    p_embeddings_count INTEGER DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE periods
    SET
        extraction_status = p_status,
        error_count = p_error_count,
        warning_count = p_warning_count,
        embeddings_count = COALESCE(p_embeddings_count, embeddings_count),
        updated_at = NOW()
    WHERE id = p_period_id;
END;
$$;

-- ============================================
-- STEG 7: Trigger för att automatiskt uppdatera embeddings_count
-- ============================================

CREATE OR REPLACE FUNCTION update_embeddings_count()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Uppdatera bara om embedding ändrades
    IF (TG_OP = 'UPDATE' AND OLD.embedding IS DISTINCT FROM NEW.embedding) OR TG_OP = 'INSERT' THEN
        UPDATE periods
        SET embeddings_count = (
            SELECT COUNT(*) FROM sections
            WHERE period_id = NEW.period_id AND embedding IS NOT NULL
        )
        WHERE id = NEW.period_id;
    END IF;
    RETURN NEW;
END;
$$;

-- Ta bort gammal trigger om den finns
DROP TRIGGER IF EXISTS trg_update_embeddings_count ON sections;

-- Skapa ny trigger
CREATE TRIGGER trg_update_embeddings_count
AFTER INSERT OR UPDATE OF embedding ON sections
FOR EACH ROW
EXECUTE FUNCTION update_embeddings_count();

-- ============================================
-- STEG 8: Datamigration - Populera nya kolumner
-- ============================================
-- Kör detta EFTER att alla funktioner är skapade

UPDATE periods p SET
    tables_count = (SELECT COUNT(*) FROM report_tables WHERE period_id = p.id),
    sections_count = (SELECT COUNT(*) FROM sections WHERE period_id = p.id),
    charts_count = (SELECT COUNT(*) FROM charts WHERE period_id = p.id),
    embeddings_count = (SELECT COUNT(*) FROM sections WHERE period_id = p.id AND embedding IS NOT NULL),
    cost_sek = COALESCE((p.extraction_meta->>'total_cost_sek')::NUMERIC, 0),
    extraction_time_seconds = COALESCE((p.extraction_meta->>'total_elapsed_seconds')::NUMERIC, 0),
    extraction_status = CASE
        WHEN p.extraction_meta IS NULL THEN 'success'
        WHEN (p.extraction_meta->'validation'->'tables'->>'is_valid')::BOOLEAN = false THEN 'partial'
        WHEN COALESCE((p.extraction_meta->'validation'->'tables'->>'error_count')::INTEGER, 0) > 0 THEN 'partial'
        ELSE 'success'
    END,
    error_count = COALESCE((p.extraction_meta->'validation'->'tables'->>'error_count')::INTEGER, 0),
    warning_count = COALESCE((p.extraction_meta->'validation'->'tables'->>'warning_count')::INTEGER, 0),
    updated_at = NOW()
WHERE p.extraction_status IS NULL OR p.extraction_status = 'pending';

-- ============================================
-- VERIFIERING
-- ============================================
-- Kör dessa queries för att verifiera att migrationen lyckades:

-- SELECT extraction_status, COUNT(*) FROM periods GROUP BY extraction_status;
-- SELECT * FROM get_global_stats();
-- SELECT * FROM get_company_stats((SELECT id FROM companies LIMIT 1));
