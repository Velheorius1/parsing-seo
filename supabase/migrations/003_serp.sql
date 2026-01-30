-- Таблица результатов поисковой выдачи
CREATE TABLE serp_results (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  query TEXT NOT NULL,
  position INTEGER NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  description TEXT,
  domain TEXT,
  search_engine TEXT DEFAULT 'google',
  parsed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_serp_query ON serp_results(query);
CREATE INDEX idx_serp_parsed_at ON serp_results(parsed_at DESC);
