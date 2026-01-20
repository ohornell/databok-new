-- Migration: Lägg till synonym-tabell för skalbar label_en-matchning
-- Kör denna migration i Supabase SQL Editor
--
-- PRINCIP: Endast synonymer som är DEFINITIVT samma sak.
-- Bättre att missa en matchning än att visa felaktig jämförelse.

-- 1. Synonym-tabell för att normalisera label_en
CREATE TABLE IF NOT EXISTS label_synonyms (
    synonym TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);

-- Index för snabb sökning på canonical
CREATE INDEX IF NOT EXISTS idx_label_synonyms_canonical ON label_synonyms(canonical);

-- 2. Ta bort gamla synonymer och populera med striktare mappningar
DELETE FROM label_synonyms;

INSERT INTO label_synonyms (synonym, canonical) VALUES
-- ============ RESULTATRÄKNING - INTÄKTER ============
-- OBS: "revenue", "sales", "turnover" kan betyda olika saker - SEPARERADE
('net sales', 'net sales'),
('net revenue', 'net sales'),  -- Bara "net" varianter är ekvivalenta

-- ============ RESULTATRÄKNING - KOSTNADER ============
('cost of goods sold', 'cost of goods sold'),
('cogs', 'cost of goods sold'),
('cost of sales', 'cost of goods sold'),

('personnel expenses', 'personnel expenses'),
('personnel costs', 'personnel expenses'),
('employee expenses', 'personnel expenses'),
('staff costs', 'personnel expenses'),
-- OBS: "salaries and wages" kan vara en del av personnel expenses, inte samma sak

('other operating expenses', 'other operating expenses'),
('other operating costs', 'other operating expenses'),
-- OBS: "other expenses" kan inkludera icke-operativa kostnader

('selling expenses', 'selling expenses'),
('sales and marketing expenses', 'selling expenses'),
('selling and distribution expenses', 'selling expenses'),
-- OBS: "distribution costs" kan vara separat post

('administrative expenses', 'administrative expenses'),
('admin expenses', 'administrative expenses'),
('general and administrative expenses', 'administrative expenses'),
('g&a expenses', 'administrative expenses'),

-- ============ RESULTATRÄKNING - AVSKRIVNINGAR ============
('depreciation and amortization', 'depreciation and amortization'),
('d&a', 'depreciation and amortization'),
-- OBS: "depreciation" ensamt och "amortization" ensamt är OLIKA saker

-- ============ RESULTATRÄKNING - RESULTAT ============
('gross profit', 'gross profit'),
('gross income', 'gross profit'),
-- OBS: "gross margin" är ofta i procent, inte samma sak

('operating profit', 'operating profit'),
('operating income', 'operating profit'),
('operating result', 'operating profit'),
-- OBS: "EBIT" kan skilja sig om det finns exceptionella poster

('ebit', 'ebit'),
('earnings before interest and tax', 'ebit'),

('ebitda', 'ebitda'),
('earnings before interest, tax, depreciation and amortization', 'ebitda'),

('profit before tax', 'profit before tax'),
('earnings before tax', 'profit before tax'),
('result before tax', 'profit before tax'),
('profit after financial items', 'profit before tax'),
('ebt', 'profit before tax'),

('net profit', 'net profit'),
('net income', 'net profit'),
('net result', 'net profit'),
('profit for the period', 'net profit'),
('result for the period', 'net profit'),
('net earnings', 'net profit'),

-- ============ RESULTATRÄKNING - FINANSIELLT ============
('net financial items', 'net financial items'),
('net financial result', 'net financial items'),
('financial items net', 'net financial items'),

('financial income', 'financial income'),
('finance income', 'financial income'),
-- OBS: "interest income" kan vara en del av financial income

('financial expenses', 'financial expenses'),
('finance costs', 'financial expenses'),
('finance expenses', 'financial expenses'),
-- OBS: "interest expenses" kan vara en del av financial expenses

-- ============ RESULTATRÄKNING - SKATT ============
('tax', 'tax'),
('income tax', 'tax'),
('income tax expense', 'tax'),
('tax expense', 'tax'),
('tax on profit', 'tax'),

-- ============ BALANSRÄKNING - TILLGÅNGAR ============
('total assets', 'total assets'),
('sum assets', 'total assets'),

('non-current assets', 'non-current assets'),
('non current assets', 'non-current assets'),
('long-term assets', 'non-current assets'),
-- OBS: "fixed assets" kan betyda bara materiella anläggningstillgångar

('current assets', 'current assets'),
('short-term assets', 'current assets'),

('intangible assets', 'intangible assets'),
('intangibles', 'intangible assets'),
-- OBS: "goodwill and intangibles" är en annan post

('goodwill', 'goodwill'),

('property plant and equipment', 'property plant and equipment'),
('property, plant and equipment', 'property plant and equipment'),
('ppe', 'property plant and equipment'),
('tangible fixed assets', 'property plant and equipment'),
-- OBS: "tangible assets" kan inkludera mer än PPE

('inventories', 'inventories'),
('inventory', 'inventories'),
-- OBS: "stock" är tvetydigt (kan betyda aktier)

('trade receivables', 'trade receivables'),
('accounts receivable', 'trade receivables'),
-- OBS: "receivables" kan inkludera andra fordringar

('cash and cash equivalents', 'cash and cash equivalents'),
('cash and equivalents', 'cash and cash equivalents'),
('cash and bank', 'cash and cash equivalents'),
-- OBS: "cash" ensamt och "liquid assets" kan vara bredare

-- ============ BALANSRÄKNING - EGET KAPITAL ============
('total equity', 'total equity'),
('shareholders equity', 'total equity'),
('shareholders'' equity', 'total equity'),
('stockholders equity', 'total equity'),
-- OBS: "equity" ensamt kan vara tvetydigt, "net assets" är annorlunda

('share capital', 'share capital'),
('common stock', 'share capital'),
('issued capital', 'share capital'),

('retained earnings', 'retained earnings'),
('accumulated earnings', 'retained earnings'),
('accumulated profit', 'retained earnings'),
-- OBS: "reserves" kan inkludera andra reserver

-- ============ BALANSRÄKNING - SKULDER ============
('total liabilities', 'total liabilities'),
('sum liabilities', 'total liabilities'),
-- OBS: "total debt" är ofta bara räntebärande skulder

('non-current liabilities', 'non-current liabilities'),
('non current liabilities', 'non-current liabilities'),
('long-term liabilities', 'non-current liabilities'),
-- OBS: "long-term debt" är ofta bara räntebärande

('current liabilities', 'current liabilities'),
('short-term liabilities', 'current liabilities'),
-- OBS: "short-term debt" är ofta bara räntebärande

('trade payables', 'trade payables'),
('accounts payable', 'trade payables'),
-- OBS: "payables" kan inkludera andra skulder

('interest-bearing debt', 'interest-bearing debt'),
('interest-bearing liabilities', 'interest-bearing debt'),
('borrowings', 'interest-bearing debt'),
('loans and borrowings', 'interest-bearing debt'),
-- OBS: "debt", "loans", "bank loans" är för vaga

('provisions', 'provisions'),
-- OBS: "accruals" är annorlunda (upplupna kostnader)

('deferred tax liabilities', 'deferred tax liabilities'),
('deferred tax', 'deferred tax liabilities'),

-- ============ KASSAFLÖDE ============
('cash flow from operations', 'cash flow from operations'),
('cash flow from operating activities', 'cash flow from operations'),
('operating cash flow', 'cash flow from operations'),
('net cash from operating activities', 'cash flow from operations'),

('cash flow from investing', 'cash flow from investing'),
('cash flow from investing activities', 'cash flow from investing'),
('investing cash flow', 'cash flow from investing'),
('net cash from investing activities', 'cash flow from investing'),

('cash flow from financing', 'cash flow from financing'),
('cash flow from financing activities', 'cash flow from financing'),
('financing cash flow', 'cash flow from financing'),
('net cash from financing activities', 'cash flow from financing'),

('change in cash', 'change in cash'),
('net change in cash', 'change in cash'),
('net increase in cash', 'change in cash'),
('cash flow for the period', 'change in cash'),

('capital expenditure', 'capital expenditure'),
('capex', 'capital expenditure'),
('purchases of property plant and equipment', 'capital expenditure'),
-- OBS: "investments in fixed assets" kan inkludera mer

('dividends paid', 'dividends paid'),
('dividend payments', 'dividends paid')
-- OBS: "dividends" ensamt kan vara erhållna utdelningar

ON CONFLICT (synonym) DO UPDATE SET canonical = EXCLUDED.canonical;

-- 3. Skapa en funktion för att normalisera label_en
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

-- 4. Kommentar
COMMENT ON TABLE label_synonyms IS 'Strikt synonym-mappning för label_en. Endast definitivt ekvivalenta termer.';
COMMENT ON FUNCTION normalize_label_en IS 'Normaliserar label_en till kanonisk form för jämförelse';
