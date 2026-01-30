-- Таблица спарсенных страниц
CREATE TABLE parsed_pages (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  url TEXT NOT NULL,
  domain TEXT NOT NULL,
  title TEXT,
  h1 TEXT,
  meta_description TEXT,
  meta_keywords TEXT[] DEFAULT '{}',
  parsed_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(url)
);

CREATE INDEX idx_parsed_pages_domain ON parsed_pages(domain);
CREATE INDEX idx_parsed_pages_parsed_at ON parsed_pages(parsed_at DESC);
