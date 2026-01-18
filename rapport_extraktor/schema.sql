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
    pdf_hash TEXT,
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, quarter, year)
);

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

-- Row Level Security (valfritt - aktivera om du vill ha autentisering)
-- ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE periods ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE financial_data ENABLE ROW LEVEL SECURITY;
