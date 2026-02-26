# Naming Conventions

## Python (crawler/)
- Files: snake_case.py
- Classes: PascalCase (e.g., ApiAdapter, BaseAdapter)
- Functions: snake_case
- Constants: UPPER_SNAKE_CASE
- Adapter classes: {Type}Adapter (ApiAdapter, HtmlAdapter, SpaAdapter, TelegramAdapter)

## TypeScript (src/)
- Files: camelCase.ts or kebab-case.ts (follow existing pattern)
- React components: PascalCase.tsx
- Types/interfaces: PascalCase
- Zustand stores: use{Name}Store

## Database (Supabase)
- Tables: snake_case (plural: tenders, keywords)
- Columns: snake_case
- Indexes: idx_{table}_{column}
- RLS policies: {table}_{operation} (e.g., tenders_select)

## Config (YAML)
- Source IDs: kebab-case (e.g., etender-discussion, xt-xarid)
- id_prefix: lowercase, hyphens ok (e.g., etender-disc, tg-uzt)
