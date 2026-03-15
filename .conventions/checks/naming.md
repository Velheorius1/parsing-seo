# Naming Conventions

## Python (crawler/)
- Files: snake_case.py
- Classes: PascalCase (e.g., ApiAdapter, BaseAdapter)
- Functions: snake_case
- Constants: UPPER_SNAKE_CASE
- Adapter classes: {Type}Adapter (ApiAdapter, HtmlAdapter, SpaAdapter, TelegramAdapter)

## TypeScript (src/)
- Files: camelCase.ts or kebab-case.ts (follow existing pattern)
- React components: PascalCase.tsx (e.g., TenderTable.tsx, DeadlineBadge, FavoriteButton)
- Types/interfaces: PascalCase (e.g., Tender, TenderFavorite, TenderSearchParams)
- Zustand stores: use{Name}Store (e.g., useTenderStore)
- Helper functions: camelCase (e.g., calcDaysLeft, formatPrice, formatTimeAgo)
- API routes: src/app/api/{resource}/route.ts (e.g., api/tenders/favorites/route.ts)
- Supabase query files: src/lib/supabase/{resource}.ts (e.g., tenders.ts, favorites.ts)
- DB row interfaces: {Resource}Row (snake_case fields), app types: PascalCase (camelCase fields)
- Mapping functions: rowTo{Type} (e.g., rowToTender, rowToFavorite)

## Database (Supabase)
- Tables: snake_case (plural: tenders, keywords)
- Columns: snake_case
- Indexes: idx_{table}_{column}
- RLS policies: {table}_{operation} (e.g., tenders_select)

## Config (YAML)
- Source IDs: kebab-case (e.g., etender-discussion, xt-xarid)
- id_prefix: lowercase, hyphens ok (e.g., etender-disc, tg-uzt)
