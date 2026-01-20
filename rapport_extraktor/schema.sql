-- Supabase/PostgreSQL schema för Rapport Extraktor
-- Kör detta i Supabase SQL Editor (supabase.com > SQL Editor)

-- Bolag
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Perioder/rapporter
CREATE TABLE periods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    year INTEGER NOT NULL,
    valuta TEXT,
    language TEXT DEFAULT 'sv',  -- Dokumentspråk: sv, no, en
    pdf_hash TEXT,
    source_file TEXT,
    extraction_meta JSONB,  -- Pipeline-info: retry_stats, validation, kostnad, etc.
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, quarter, year)
);

-- Om tabellen redan finns, lägg till nya kolumner:
-- ALTER TABLE periods ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'sv';
-- ALTER TABLE periods ADD COLUMN IF NOT EXISTS extraction_meta JSONB;

-- Finansiella rader
CREATE TABLE financial_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_id UUID REFERENCES periods(id) ON DELETE CASCADE,
    statement_type TEXT NOT NULL,
    row_order INTEGER NOT NULL,
    row_name TEXT NOT NULL,
    value NUMERIC,
    row_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index för snabba queries
CREATE INDEX idx_periods_company ON periods(company_id);
CREATE INDEX idx_periods_year_quarter ON periods(year, quarter);
CREATE INDEX idx_financial_period ON financial_data(period_id);
CREATE INDEX idx_financial_type ON financial_data(statement_type);

-- ============================================
-- NYA TABELLER FÖR FULL EXTRAKTION
-- ============================================

-- Aktivera pgvector för semantisk sökning (kräver att extension är installerad i Supabase)
-- I Supabase: Database > Extensions > Sök "vector" > Enable
CREATE EXTENSION IF NOT EXISTS vector;

-- Textsektioner (VD-ord, marknadsöversikt, verksamhet, etc.)
CREATE TABLE sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_id UUID REFERENCES periods(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    page_number INTEGER,
    section_type TEXT,  -- narrative, summary, highlights, other
    content TEXT NOT NULL,
    embedding vector(1536),  -- För semantisk sökning (OpenAI embedding dimension)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Flexibla tabeller (alla typer - finansiella, nyckeltal, segment, etc.)
CREATE TABLE report_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_id UUID REFERENCES periods(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    page_number INTEGER,
    table_type TEXT,  -- income_statement, balance_sheet, cash_flow, kpi, segment, other
    columns JSONB,    -- ["Kolumn1", "Kolumn2", ...]
    rows JSONB,       -- [{"label": "...", "values": [...], "type": "subtotal"}, ...]
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index för nya tabeller
CREATE INDEX idx_sections_period ON sections(period_id);
CREATE INDEX idx_sections_type ON sections(section_type);
CREATE INDEX idx_tables_period ON report_tables(period_id);
CREATE INDEX idx_tables_type ON report_tables(table_type);

-- Vektor-index för semantisk sökning (ivfflat kräver minst 100 rader för att fungera bra)
-- Använd HNSW för bättre prestanda om du har många sektioner
CREATE INDEX idx_sections_embedding ON sections USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Fulltext-sökning på sektioner (använder 'simple' för språkagnostisk sökning)
-- 'simple' fungerar för alla språk (sv, no, en) men utan stemming
-- Semantisk sökning (embeddings) är primär sökmetod - FTS är komplement
CREATE INDEX idx_sections_content_fts ON sections USING gin(to_tsvector('simple', content));

-- Om indexet redan finns med 'swedish', kör:
-- DROP INDEX IF EXISTS idx_sections_content_fts;
-- CREATE INDEX idx_sections_content_fts ON sections USING gin(to_tsvector('simple', content));

-- Grafer/diagram extraherade från rapporter
CREATE TABLE charts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_id UUID REFERENCES periods(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    page_number INTEGER,
    chart_type TEXT,  -- bar, line, pie, area, other
    x_axis TEXT,
    y_axis TEXT,
    estimated BOOLEAN DEFAULT true,  -- true = värden uppskattade visuellt, false = exakta värden
    data_points JSONB,  -- [{"label": "Q1 2024", "value": 850}, ...]
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_charts_period ON charts(period_id);
CREATE INDEX idx_charts_type ON charts(chart_type);

-- ============================================
-- SYNONYM-TABELL FÖR LABEL_EN NORMALISERING
-- ============================================

-- Synonym-tabell för att normalisera label_en vid jämförelser
CREATE TABLE label_synonyms (
    synonym TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);

CREATE INDEX idx_label_synonyms_canonical ON label_synonyms(canonical);

-- Funktion för att normalisera label_en
CREATE OR REPLACE FUNCTION normalize_label_en(label TEXT)
RETURNS TEXT AS $$
DECLARE
    result TEXT;
BEGIN
    SELECT canonical INTO result
    FROM label_synonyms
    WHERE synonym = LOWER(TRIM(label));

    IF result IS NULL THEN
        RETURN LOWER(TRIM(label));
    END IF;

    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Kör migrations/002_label_synonyms.sql för att populera synonym-tabellen

-- Row Level Security (valfritt - aktivera om du vill ha autentisering)
-- ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE periods ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE financial_data ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sections ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE report_tables ENABLE ROW LEVEL SECURITY;
