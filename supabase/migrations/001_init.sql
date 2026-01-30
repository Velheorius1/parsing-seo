-- Таблица ключевых слов
CREATE TABLE keywords (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  keyword TEXT NOT NULL,
  source TEXT NOT NULL,
  search_volume INTEGER,
  competition TEXT,
  base_query TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(keyword, source)
);

CREATE INDEX idx_keywords_keyword ON keywords(keyword);
CREATE INDEX idx_keywords_source ON keywords(source);
