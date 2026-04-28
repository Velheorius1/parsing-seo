import { createClient } from '@supabase/supabase-js';

// Браузерный клиент (используется на клиенте и сервере для публичных операций)
function createBrowserClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    return null;
  }

  return createClient(url, anonKey);
}

// Серверный клиент с повышенными правами (только для API routes)
function createServerClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !serviceKey) {
    return null;
  }

  return createClient(url, serviceKey);
}

// Экспортируем ленивые геттеры — клиент создаётся при первом обращении
let _browserClient: ReturnType<typeof createBrowserClient> | undefined;
let _serverClient: ReturnType<typeof createServerClient> | undefined;

export function getSupabase() {
  if (_browserClient === undefined) {
    _browserClient = createBrowserClient();
  }
  return _browserClient;
}

export function getSupabaseServer() {
  if (_serverClient === undefined) {
    _serverClient = createServerClient();
  }
  return _serverClient;
}

// Проверка: подключён ли Supabase
export function isSupabaseConfigured(): boolean {
  return !!(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  );
}
