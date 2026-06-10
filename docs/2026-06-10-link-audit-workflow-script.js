export const meta = {
  name: 'tender-coverage-and-links-audit',
  description: 'Аудит deep-links всех источников + поиск некраулящихся публичных каналов площадок + research способов поиска тендеров UZ',
  phases: [
    { title: 'Map', detail: 'карта источников и DB census' },
    { title: 'LinkAudit', detail: 'проверка deep-links по кластерам' },
    { title: 'PlatformProbe', detail: 'некраулящиеся публичные каналы площадок' },
    { title: 'Research', detail: 'внешние способы поиска тендеров + подача КП' },
    { title: 'Verify', detail: 'adversarial проверка находок' },
  ],
}

const REPO = '/Users/doniersalahutdinov/tmp-parsing-seo'
const DB_RECIPE = `Доступ к БД (read-only!): source ~/.zshrc, затем:
curl -s -X POST "https://api.supabase.com/v1/projects/oaoehczbycrabkprazts/database/query" -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" -d '{"query":"<SQL>"}'
Таблица tenders: external_id, title, organization, price, source (display name), source_url, status, alert_seq (NOT NULL = алерт отправлен), collected_at, search_text, relevance_score. ТОЛЬКО SELECT.`

const KNOWN_FACTS = `Уже установленные факты (используй, не передоказывай):
1. api.xt-xarid.uz/rpc и api.hayotbirja.uz/rpc — JSON-RPC, метод "ref" {op:"read", ref:"<имя>", filters:{}, limit, offset, fields:[...]}. Анонимно работают ref_reduction_object_public, ref_tender_public, ref_online_shop_public.
2. ref_online_shop_public (xt-xarid) отдаёт объявления э-магазина: поле name ПУСТОЕ, имя товара в product_name; фильтр {is_national:false, product_id:["17.23.13.191_00001"], is_gos_shop:true} работает. Объявления Winch 7628192/7627284 (Блокнот) там видны, в нашей БД отсутствуют.
3. Источник hayotbirja-shop (ref_online_shop_public) enabled в sources.yaml, но в БД 0 строк — вероятно из-за title:"name" (пустой).
4. SPA xt-xarid зовёт urpc метод get_proc {proc_id} — анонимно отдаёт полную карточку (type:ad, status:publicated, fields с описанием печати).
5. Шаблоны deep-links сейчас: xt-xarid.uz/procedure/{id}/core, hayotbirja.uz/procedure/{id}/core, new-xarid.uzex.uz/home/shop/detail/{id}?elektron=true (shop/auction), new-xarid.uzex.uz/home/purchase/proposal-request/detail/{id} (предквалификации).`

const SAFETY = 'Только чтение: curl анонимных публичных API (read), Read/Grep файлов. Никаких записей в БД, никаких POST мутаций, никаких git push, никакого деструктива. В вопросах сети будь вежлив: не более ~30 запросов на хост, паузы.'

// ===== Schemas =====
const REGISTRY_SCHEMA = {
  type: 'object', required: ['sources'],
  properties: {
    sources: { type: 'array', items: { type: 'object', required: ['id','name','enabled','adapter','link_template','platform'], properties: {
      id: {type:'string'}, name: {type:'string'}, enabled: {type:'boolean'}, adapter: {type:'string'},
      link_template: {type:'string', description:'source_url_template или как notifier строит ссылку; "SEARCH_FALLBACK" если ссылки нет/поиск по названию; "UNKNOWN" если не нашёл'},
      platform: {type:'string', description:'хост площадки, напр. xt-xarid.uz, hayotbirja.uz, new-xarid.uzex.uz, e-birja, cooperation.uz, telegram, html-misc'},
      broken_spa: {type:'boolean'}, notes: {type:'string'} } } },
    notifier_link_logic: { type:'string', description:'краткое описание как notifier.py строит ссылку алерта: какие поля, какие fallback' }
  }
}
const CENSUS_SCHEMA = {
  type: 'object', required: ['census'],
  properties: { census: { type: 'array', items: { type:'object', required:['source','total','alerted','last_seen','samples'], properties: {
    source: {type:'string'}, total:{type:'number'}, alerted:{type:'number'}, last_seen:{type:'string'},
    samples: { type:'array', items:{ type:'object', properties:{ external_id:{type:'string'}, title:{type:'string'}, source_url:{type:'string'}, alerted:{type:'boolean'} } } } } } },
    dead_enabled_sources: { type:'array', items:{type:'string'}, description:'enabled в yaml но 0 строк в БД или last_seen > 14д назад' } }
}
const LINK_AUDIT_SCHEMA = {
  type: 'object', required: ['cluster','verdicts'],
  properties: { cluster: {type:'string'}, verdicts: { type:'array', items: { type:'object', required:['source','sample_url','verdict','evidence','fix_proposal'], properties: {
    source:{type:'string'}, sample_url:{type:'string'},
    verdict:{type:'string', enum:['DIRECT_OK','WRONG_PAGE','SEARCH_FALLBACK','AUTH_REQUIRED','DEAD','NO_URL','UNVERIFIABLE']},
    evidence:{type:'string', description:'конкретика: HTTP код, что вернул API, какие поля подтверждают что это ТОТ лот'},
    fix_proposal:{type:'string', description:'конкретный фикс: новый шаблон URL / API endpoint для проверки / что менять в коде'},
    confidence:{type:'string', enum:['high','medium','low']} } } } }
}
const PROBE_SCHEMA = {
  type: 'object', required: ['platform','channels'],
  properties: { platform: {type:'string'}, channels: { type:'array', items: { type:'object', required:['name','access','crawled_now','data_kind','evidence'], properties: {
    name:{type:'string'}, access:{type:'string', enum:['anonymous','registration','eimzo','paid','unknown']},
    crawled_now:{type:'boolean'}, data_kind:{type:'string', description:'что за данные: спрос покупателя / объявления поставщиков / контракты / результаты / планы'},
    evidence:{type:'string', description:'точный воспроизводимый curl + первые строки ответа'},
    print_relevance:{type:'string'}, est_volume:{type:'string'}, deep_link_template:{type:'string'} } } },
    product_ids_print: { type:'array', items:{type:'string'}, description:'найденные product_id/категории полиграфии в каталоге площадки (если применимо)' },
    notes: {type:'string'} }
}
const RESEARCH_SCHEMA = {
  type: 'object', required: ['findings'],
  properties: { findings: { type:'array', items: { type:'object', required:['method','what_it_gives','access','impact','evidence_url'], properties: {
    method:{type:'string'}, what_it_gives:{type:'string'}, access:{type:'string'},
    effort:{type:'string'}, impact:{type:'string', enum:['high','medium','low']}, evidence_url:{type:'string'} } } } }
}
const VERIFY_SCHEMA = {
  type: 'object', required: ['holds','corrected','evidence'],
  properties: { holds: {type:'boolean'}, corrected: {type:'string'}, evidence: {type:'string'} }
}

// ===== Phase 1: Map (parallel pair, barrier needed: clusters built from both) =====
phase('Map')
const [registry, census] = await parallel([
  () => agent(`Ты исследователь кодовой базы краулера тендеров. Репо: ${REPO}.
Задача: построить ПОЛНЫЙ реестр источников и шаблонов ссылок.
1. Прочитай ${REPO}/crawler/config/sources.yaml ЦЕЛИКОМ (117 источников) — для каждого: id, name, enabled, adapter, source_url_template (если есть), платформа (хост).
2. Прочитай ${REPO}/crawler/core/notifier.py (и связанные) — как строится ссылка в Telegram-алерте: из source_url? шаблоны? что при отсутствии URL ("найти по названию")? Найди список _BROKEN_SPA (вероятно crawler/scripts/snap.py или notifier) — какие источники помечены как сломанные SPA.
3. Для каждого источника определи link_template: реальный шаблон URL, либо SEARCH_FALLBACK, либо UNKNOWN.
${SAFETY}
Верни структурированный реестр.`, { label: 'map:sources-registry', phase: 'Map', schema: REGISTRY_SCHEMA }),
  () => agent(`Ты аналитик БД тендерного краулера. ${DB_RECIPE}
Задача: перепись источников в БД.
1. SELECT source, count(*) total, count(alert_seq) alerted, max(collected_at)::date last_seen FROM tenders GROUP BY source ORDER BY total DESC — все источники.
2. Для КАЖДОГО источника с alerted > 0: выбери 3 последних alerted строки (alert_seq IS NOT NULL): external_id, title (обрежь 80 симв), source_url. Для источников с alerted=0 но total>100: 2 последних строки.
3. Пометь dead_enabled_sources: в БД нет строк вовсе или last_seen старше 2026-05-27. Известно: "Hayotbirja э-магазин" — 0 строк.
Делай запросы батчами (можно одним SQL с window functions). ${SAFETY}`, { label: 'map:db-census', phase: 'Map', schema: CENSUS_SCHEMA }),
])

if (!registry || !census) throw new Error('Map phase failed')
log(`Реестр: ${registry.sources.length} источников; БД census: ${census.census.length} источников, мёртвых enabled: ${(census.dead_enabled_sources||[]).length}`)

// ===== Build link-audit clusters from registry+census (genuinely needs both → barrier above is correct) =====
const byPlatform = {}
for (const s of registry.sources.filter(s => s.enabled)) {
  const key = s.platform || 'misc'
  byPlatform[key] = byPlatform[key] || []
  byPlatform[key].push(s)
}
const censusBySource = {}
for (const c of census.census) censusBySource[c.source] = c
const clusters = Object.entries(byPlatform).map(([platform, sources]) => ({
  platform,
  sources: sources.map(s => ({
    ...s,
    db: censusBySource[s.name] || null,
  })),
}))
log(`Кластеров для link-audit: ${clusters.length} (${clusters.map(c=>c.platform).join(', ')})`)

// ===== Phase 2+3: pipeline link audit per cluster; платформенные пробы параллельно =====
const PLATFORM_PROBES = [
  { key: 'xt-xarid.uz', hint: `JSON-RPC api.xt-xarid.uz/rpc + urpc (get_proc). Сейчас краулим: ref_reduction_object_public, ref_tender_public. НЕ краулим: ref_online_shop_public (объявления!), и вероятно есть refs для selection/request_proposals/master_agreement (SPA грузит ref_status_selection, ref_status_request_proposals, ref_status_master_agreement, ref_status_ad). Скачай JS-бандлы https://xt-xarid.uz (main*.js и чанки), grep по "ref_[a-z_]*public" и "method:" — выпиши ВСЕ публичные refs, прокури каждый curl'ом анонимно (limit:2), классифицируй данные. Найди каталог товаров (ref для product) и выпиши product_id полиграфии: блокноты(17.23.13.191*), бумага, печатная продукция, этикетки, коробки/гофра, бланки, журналы/книги учёта, конверты, календари, пакеты.` },
  { key: 'hayotbirja.uz', hint: `Тот же бэкенд что xt-xarid (api.hayotbirja.uz/rpc). Сейчас краулим: reduction, tender, selection, shop (shop сломан: title из пустого name). Скачай JS-бандлы https://hayotbirja.uz, grep "ref_[a-z_]*public", прокури все refs анонимно. Особо: ref_online_shop_public — какие поля заполнены (product_name!), какие фильтры по категориям работают, объёмы. Проверь есть ли публичный ref контрактов/результатов (кто выиграл, цены).` },
  { key: 'new-xarid.uzex.uz (UZEX)', hint: `Сейчас краулим: uzex-auctions, uzex-prequest (api: PREQUEST_API_URL/api/Public/GetLot и др. /api/Public/*). Изучи SPA https://new-xarid.uzex.uz: скачай главный JS и чанки, grep "api/Public" и "API_URL" — выпиши ВСЕ публичные endpoints (GetLots, GetLotsInTrade, offers, контракты/результаты, прямые закупки, конкурсы). Прокури curl'ом. Особо интересно: э-магазин/shop endpoints, Результаты торгов (победители и цены), и можно ли по лоту получить статус торгов real-time.` },
  { key: 'cooperation.uz', hint: `Сейчас краулим: cooperation-plans-filtered + контракты через stat-new.cooperation.uz/gateway/api-stat/auction-contracts (публичный). Лоты e-каталога требуют обходов. Изучи https://cooperation.uz и stat-new.cooperation.uz: какие ещё gateway/api-stat/* endpoints публичны (попробуй варианты: auction-lots, auctions, products, contracts по фильтрам), есть ли способ видеть АКТИВНЫЕ торги анонимно. grep JS-бандлы на "gateway" и "api".` },
  { key: 'e-birja / xarid.ebirja.uz', hint: `Сейчас краулим много ebirja-* источников (auction, eshop, natshop, ext-products, ext-contracts, reverse-listing, announcements, auth-*). Часть через E-IMZO. Изучи какие данные публичны БЕЗ авторизации: скачай JS https://xarid.ebirja.uz и https://e-birja.uz, выпиши API endpoints, прокури. Особо: обратные/встречные аукционы и объявления поставщиков, результаты с ценами победителей.` },
  { key: 'etender.uzex.uz + xarid.uzex.uz', hint: `Сейчас краулим etender (+discussion), xarid-competitions, xarid-direct (5669+4717 строк, 0-2 алерта — подозрительно мало!). Проверь: (1) что за данные мы собираем и почему почти не алертится (возможно title без позиций лота → keyword мимо), (2) какие API у etender.uzex.uz и xarid.uzex.uz публичны (JS-бандлы → endpoints → curl), (3) есть ли позиции лота/спецификации в API ответе которые мы НЕ сохраняем в search_text.` },
]

const STRICT_AUDIT_PROMPT = (cluster) => `Ты аудитор deep-links тендерного краулера. Кластер: ${cluster.platform}.
Источники кластера (с шаблонами ссылок и сэмплами из БД): ${JSON.stringify(cluster.sources).slice(0, 12000)}

Для КАЖДОГО enabled источника проверь deep-link эмпирически:
- Возьми sample_url из сэмплов БД (или построй из link_template + external_id).
- HTML-источники: curl -sL и проверь что страница содержит title лота (или хотя бы релевантный контент, не 404/редирект на главную).
- SPA-источники: curl страницы бесполезен (вернёт пустой шаблон) — проверяй через ПУБЛИЧНЫЙ API площадки: xt-xarid/hayotbirja: POST https://api.<host>/urpc {"id":1,"jsonrpc":"2.0","method":"get_proc","params":{"proc_id":"<id>"}} — если result.proc_id совпал и status понятен = DIRECT_OK. UZEX new-xarid: GET https://new-xarid.uzex.uz/...api/Public/GetLot?id=<id> (или эндпоинт из шаблона). Telegram-источники: ссылка t.me/<channel>/<msg> — проверь формат, классифицируй UNVERIFIABLE если нельзя проверить curl'ом.
- Если link_template = SEARCH_FALLBACK или UNKNOWN → verdict SEARCH_FALLBACK/NO_URL, и ПРЕДЛОЖИ как построить прямую ссылку (поищи в API ответе поля id/url; попробуй очевидные URL-паттерны площадки с реальным id из БД).
- ${SAFETY}
Верடи вердикт по каждому источнику с доказательствами (HTTP-код, фрагмент ответа подтверждающий совпадение лота).`

const [auditResults, probeResults, researchResults] = await parallel([
  // link audit: pipeline по кластерам
  () => parallel(clusters.map(c => () =>
    agent(STRICT_AUDIT_PROMPT(c), { label: `audit:${c.platform}`, phase: 'LinkAudit', schema: LINK_AUDIT_SCHEMA })
  )),
  // platform probes
  () => parallel(PLATFORM_PROBES.map(p => () =>
    agent(`Ты реверс-инженер публичных API госзакупочных площадок Узбекистана. Платформа: ${p.key}.
${KNOWN_FACTS}
Задание: ${p.hint}
Метод: (1) скачай JS-бандлы платформы (curl главной страницы → выпиши <script src>, скачай их в /tmp/, при необходимости — динамические чанки по именам из главного бандла), (2) grep по именам refs/endpoints, (3) прокури КАЖДЫЙ кандидат curl'ом анонимно с limit 1-2, (4) классифицируй: что за данные, доступ, объём, релевантность полиграфии. Для каждого канала дай ТОЧНЫЙ воспроизводимый curl. Цель — найти каналы которые мы НЕ краулим но которые видны анонимно: спрос покупателей, объявления, результаты/контракты с ценами победителей.
${SAFETY}`, { label: `probe:${p.key}`, phase: 'PlatformProbe', schema: PROBE_SCHEMA })
  )),
  // external research
  () => parallel([
    () => agent(`Web-research: ВСЕ способы находить тендеры на полиграфию/упаковку в Узбекистане в 2026, помимо прямого краула госплощадок (xarid/uzex/cooperation/ebirja/hayotbirja/xt-xarid — это уже краулим). Ищи: (1) агрегаторы (tenderzone.uz, bicotender, bnect, zakupki.prom.uz, tender.uz и новые) — что дают бесплатно vs платно, есть ли API/RSS; (2) Telegram-каналы и боты с тендерами UZ (актуальные 2026); (3) openbudget.uz / data.gov.uz — планы закупок (видеть спрос ДО публикации лота); (4) международка (UNDP/UNICEF/WB/ADB) — RSS/API; (5) корпоративные порталы закупок крупных компаний UZ с разделами тендеров. Для каждого: что даёт, доступ (free/рег/платно), оценка impact для типографии (бумажно-картонная продукция, блокноты, бланки, этикетки, стенды). Давай evidence_url на каждый метод.`, { label: 'research:discovery-methods', phase: 'Research', schema: RESEARCH_SCHEMA }),
    () => agent(`Web-research + анализ: флоу ПОДАЧИ заявки/КП на госзакупочных площадках Узбекистана — xt-xarid.uz, hayotbirja.uz, new-xarid.uzex.uz (UZEX), cooperation.uz, xarid.ebirja.uz. Для каждой: (1) как поставщик подаёт предложение на обратный аукцион/отбор/тендер (шаги, нужен ли E-IMZO на каждый шаг), (2) можно ли дать менеджеру ПРЯМУЮ ссылку, которая после логина ведёт сразу к форме подачи (deep-link до bid-формы), (3) есть ли у площадки API для подачи (даже полуофициальный — мобильные приложения Hayot Birja имеют API!), (4) время жизни торгов (за сколько часов/дней надо успеть). Цель: сократить путь "алерт в Telegram → поданное КП" до минимума кликов. Учти: у Winch уже автоматизирован E-IMZO логин на cooperation.uz (challenge→sign→login). Давай evidence_url.`, { label: 'research:bid-submission', phase: 'Research', schema: RESEARCH_SCHEMA }),
  ]),
])

const audits = (auditResults || []).filter(Boolean)
const probes = (probeResults || []).filter(Boolean)
const research = (researchResults || []).filter(Boolean)
log(`Audit кластеров: ${audits.length}; платформ прозондировано: ${probes.length}; research блоков: ${research.length}`)

// ===== Phase 4: adversarial verify — только high-impact claims =====
phase('Verify')
const brokenClaims = audits.flatMap(a => a.verdicts.filter(v => ['WRONG_PAGE','DEAD','SEARCH_FALLBACK','NO_URL'].includes(v.verdict)).map(v => ({type:'link', cluster:a.cluster, ...v})))
const newChannels = probes.flatMap(p => (p.channels||[]).filter(ch => !ch.crawled_now && ch.access === 'anonymous').map(ch => ({type:'channel', platform:p.platform, ...ch})))
log(`Проверяю adversarially: ${brokenClaims.length} сломанных ссылок + ${newChannels.length} новых каналов`)

const verifiedLinks = await parallel(brokenClaims.map(c => () =>
  agent(`Ты скептик-верификатор. Утверждение: источник "${c.source}" (кластер ${c.cluster}) имеет вердикт ${c.verdict}. Evidence: ${c.evidence}. Fix: ${c.fix_proposal}. Sample URL: ${c.sample_url}.
Попробуй ОПРОВЕРГНУТЬ: воспроизведи проверку curl'ом сам (страница/API), проверь работает ли предложенный fix на реальном id. Если fix_proposal содержит новый URL-шаблон — проверь его на 2 разных id. ${SAFETY}
holds=true если вердикт подтверждён. В corrected — уточнённый вердикт/фикс если нашёл лучше.`, { label: `verify:link:${c.source}`.slice(0,60), phase: 'Verify', schema: VERIFY_SCHEMA })
    .then(v => ({...c, verify: v}))
))
const verifiedChannels = await parallel(newChannels.map(ch => () =>
  agent(`Ты скептик-верификатор. Утверждение: на платформе ${ch.platform} есть НЕкраулящийся анонимный канал "${ch.name}" (${ch.data_kind}). Evidence curl: ${ch.evidence}.
Воспроизведи curl сам. Проверь: (1) реально анонимно (без cookies/токенов)? (2) данные свежие (есть записи 2026 года)? (3) есть ли там полиграфия — поищи по фильтрам/полям записи со словами блокнот/бумага/печать/этикетка/бланк/коробка. holds=true если канал реален и полезен. ${SAFETY}`, { label: `verify:ch:${ch.name}`.slice(0,60), phase: 'Verify', schema: VERIFY_SCHEMA })
    .then(v => ({...ch, verify: v}))
))

const confirmedLinks = verifiedLinks.filter(Boolean).filter(x => x.verify?.holds)
const confirmedChannels = verifiedChannels.filter(Boolean).filter(x => x.verify?.holds)
const refutedLinks = verifiedLinks.filter(Boolean).filter(x => !x.verify?.holds)

return {
  registry_size: registry.sources.length,
  notifier_link_logic: registry.notifier_link_logic,
  dead_enabled_sources: census.dead_enabled_sources,
  link_audit: { all: audits, confirmed_broken: confirmedLinks, refuted: refutedLinks },
  new_channels: { confirmed: confirmedChannels, all_probes: probes },
  research,
}