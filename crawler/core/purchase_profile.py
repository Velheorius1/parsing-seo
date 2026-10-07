"""Судья предмета закупки: полиграфия / мерч / не наше (для журнала закупок).

Профиль заказчиков задан Данияром 07.10.2026: ТОЛЬКО полиграфия и сувенирка /
мерч с нанесением. Стенды, таблички, упаковка, канцелярия — не наше. Профиль
алертов (notifier._RELEVANCE_PROMPT) этим не меняется — это отдельный вопрос
«что покупали», а не «что нам слать».

Почему не слова. Живые предметы журнала (07.10) ловят словарь на каждом шагу:
«Kitob tumani» — район, а не книга; «chop etish qurilmalari va kartrij» —
принтеры; «kundalik tozalash» — ежедневная уборка, а не ежедневник; световой
логотип Mobiuz — вывеска; «Президентский подарок» — набор школьной канцелярии.
Поэтому правила только ОТБИРАЮТ кандидатов (широкий список корней), а решает AI
по определениям. Реклама в корнях нарочно: «Merchendayzing mahsulotlarini ishlab
chiqarish» (1,76 млрд) и «изготовление рекламно-информационных материалов»
(4,4 млрд) без неё уходили в «нет корня» мимо AI (сверка 07.10). Без единого корня строка — «не наше» без AI; полноту этого
шага меряет эталон (purchase_golden).

Порядок: ручная метка > код классификатора полиграфии > раздел прямой закупки >
кандидат → AI > «нет корня».
"""
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

PROMPT_VERSION = 'p4-2026-10-07'
PROFILES = ('poly', 'merch', 'none')

# Коды классификатора (ОКПД, как в карточках ebirja), однозначно полиграфия:
# 17.23.12 конверты/открытки, 17.23.13 журналы учёта, бланки, тетради, ежедневники;
# 18.1 печатные услуги; 58.11 книги, 58.13 газеты, 58.14 журналы, 58.19 прочая
# печатная продукция (календари, плакаты, открытки). 17.23.14 (офисная бумага) — нет.
POLY_CODE_PREFIXES = ('17.23.12', '17.23.13', '18.1', '58.11', '58.13', '58.14', '58.19')
# Коды, где мерч возможен, но только с нанесением: ручки, кружки, футболки и
# прочая одежда, пакеты, сумки, зонты, флешки. Без логотипа это канцелярия /
# одежда — решает AI по тексту.
MERCH_CODE_PREFIXES = ('32.99.1', '32.99.2', '23.41', '14.1', '22.22.1', '13.92.2', '15.12', '26.20.21')

_DIRECT_PRINT_DIVISION = re.compile(
    r'издат|печат|бумаг|одежд|текстил|реклам|изделия готовые прочие|'
    r'минеральные неметаллические|резин|пластмасс|кож', re.I)

# Корни-кандидаты (рус., узб. латиница и кириллица). Широко нарочно: лишний
# кандидат стоит долю цента AI, пропущенный — заказчика в топе.
_STEM_RE = re.compile(
    r'полиграф|типограф|печат|тираж|издан|издат|бланк|книг|учебник|брошюр|буклет|листовк|флаер|'
    r'календар|блокнот|ежедневник|тетрад|конверт|визитк|открытк|грамот|диплом|сертификат|журнал|'
    r'газет|плакат|афиш|наклейк|стикер|этикет|папк|сувенир|подар|логотип|символик|нанесени|брендир|'
    r'футболк|кепк|бейсболк|кружк|ручк|значк|флаг|баннер|пакет|альбом|удостоверени|бюллетен|вымпел|'
    r'термос|зонт|реклам|мерч|промо|раздаточн|пособи|методичк|'
    r'chop|bosma|nashr|matbaa|tipograf|poligraf|kitob|darslik|jurnal|gazeta|blank|broshyur|buklet|'
    r'varaq|taqvim|kalendar|bloknot|daftar|konvert|vizitka|otkritka|diplom|sertifikat|yorliq|'
    r'guvohnoma|sovg|esdalik|suvenir|logotip|ramz|futbolka|krujka|kepka|ruchka|nishon|bayroq|'
    r'stiker|banner|paket|albom|faxriy|tashakkurnoma|reklam|merch|promo|tarqatma|risola|'
    r"qo.llanma|"
    r'чоп|босма|нашр|матбаа|китоб|дарслик|тақвим|дафтар|совға|эсдалик|ёрлиқ|гувоҳнома|фахрий|'
    r'ташаккурнома|нишон|байроқ|рисола|қўлланма|тарқатма',
    re.I)


def _norm(value):
    # type: (Any) -> str
    return '' if value is None else ' '.join(str(value).replace('\x00', '').split())


def _codes(row):
    # type: (Dict[str, Any]) -> List[str]
    return [c for c in (row.get('subject_codes') or []) if c]


def rule_verdict(row):
    # type: (Dict[str, Any]) -> Tuple[Optional[str], str]
    """(profile, источник) по правилам; profile None + 'ai' — решать AI,
    None + 'pending' — данных ещё нет (ждём карточку/детали)."""
    codes = _codes(row)
    if any(code.startswith(POLY_CODE_PREFIXES) for code in codes):
        return 'poly', 'code'
    text = _norm(row.get('subject'))
    feed = row.get('feed') or ''
    if not text:
        if feed == 'direct':
            if _DIRECT_PRINT_DIVISION.search(row.get('category') or ''):
                return None, 'pending'      # печатный раздел — ждём позиции договора
            return 'none', 'category'
        return None, 'pending'
    if _STEM_RE.search(text) or any(code.startswith(MERCH_CODE_PREFIXES) for code in codes):
        return None, 'ai'
    return 'none', 'no_stem'


_PROMPT = """Ты классифицируешь закупки организаций Узбекистана для типографии Winch \
(полиграфия + сувенирная продукция с нанесением логотипа). Для каждой закупки выбери:

poly — заказ полиграфии: печать, издание, тираж книг и учебников (кодексов, \
энциклопедий, сборников), выпусков журналов и газет, журналов учёта и бланков (в том \
числе медицинских; закупка готовых бланков и журналов учёта тоже poly), брошюр, \
буклетов, листовок, раздаточных материалов, календарей, блокнотов, ежедневников, \
тетрадей, конвертов, визиток, открыток, грамот, дипломов, плакатов; «полиграфическая \
продукция», «типографские / полиграфические услуги».

merch — сувенирная и подарочная продукция: «сувенирка», сувениры, корпоративные \
подарки, подарочные наборы, подарки к праздникам и мероприятиям — merch всегда, \
логотип не обязателен; одежда, кружки, ручки, пакеты, сумки, значки, флешки, часы — \
merch только С НАНЕСЕНИЕМ логотипа или символики (брендированные).

none — всё остальное, в том числе:
- покупка готовых изданных книг для библиотек, школ, фондов («китоблар сотиб олиш», \
«по списку», «бадиий адабиёт») без печати тиража;
- подписка на газеты и журналы, доставка изданий подписчикам;
- принтеры, картриджи, МФУ, ремонт печатной техники; печатные платы; печати и штампы;
- стенды, таблички, вывески, световые и объёмные логотипы, УФ-печать на композите, \
баннеры, наружная и широкоформатная реклама, реклама в СМИ, на экранах и в интернете, \
мишени («нишон» как мишень);
- упаковка, коробки, этикетки для товаров;
- канцтовары без нанесения (ручки, бумага А4, папки) и наборы школьных принадлежностей, \
в том числе «Prezident sovgʻasi» / «Президентский подарок» — это учебные \
принадлежности, а не сувенир;
- одежда, форма и поло без упоминания логотипа;
- поощрения бытовой техникой и товарами (телевизоры, духовки, термопоты) по \
программам «Ёшлар дафтари» / «Аёллар дафтари»;
- художественно-декоративные и интерьерные изделия, орнамент, макеты для фасадов;
- сертификация ISO и аудит («сертификат» как документ);
- строительство, ремонт, оценка зданий (даже если здание — типография);
- топонимы («Kitob tumani» — район, не книга); «kundalik» в значении «ежедневный».

Закупки (номер. предмет | коды классификатора | заказчик):
{items}

Ответь СТРОГО JSON: {{"items": [{{"i": <номер>, "p": "poly|merch|none", "r": "<до 60 симв>"}}]}} \
— по одному элементу на каждый номер."""


def family_key(subject):
    # type: (Any) -> str
    """Ключ «одной и той же закупки»: без цифр и регистра. Лоты «1-Lot» и «2-Lot»
    «Prezident sovgʻasi» AI оценил по-разному (07.10: 126 млрд ушли в «наше»
    из-за части лотов) — одна семья предметов получает одно решение."""
    return re.sub(r'\d+', '#', _norm(subject).casefold())


def build_prompt(items):
    # type: (List[Dict[str, Any]]) -> str
    lines = []
    for index, item in enumerate(items, 1):
        parts = [_norm(item.get('subject'))[:300] or '—']
        codes = _codes(item)
        if codes:
            parts.append('коды: ' + ', '.join(codes[:4]))
        if item.get('buyer_name'):
            parts.append(_norm(item.get('buyer_name'))[:80])
        lines.append('%d. %s' % (index, ' | '.join(parts)))
    return _PROMPT.format(items='\n'.join(lines))


def parse_answer(text, count):
    # type: (str, int) -> Dict[int, Tuple[str, str]]
    """Ответ AI → {номер: (profile, причина)}; мусорные элементы пропускаются —
    такая закупка останется неразмеченной и уйдёт в следующий прогон."""
    from crawler.core.notifier import _extract_json_object, _strip_think_tags
    payload = _extract_json_object(_strip_think_tags(text or '')) or {}
    out = {}  # type: Dict[int, Tuple[str, str]]
    for item in payload.get('items') or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get('i'))
        except (TypeError, ValueError):
            continue
        profile = str(item.get('p') or '').strip().lower()
        if 1 <= index <= count and profile in PROFILES:
            out[index] = (profile, _norm(item.get('r'))[:120])
    return out


def openrouter_call(prompt, model=None, timeout=90):
    # type: (str, Optional[str], int) -> str
    """Один вызов OpenRouter: та же модель и настройки, что у гейта алертов
    (reasoning выключен, JSON-ответ, temperature 0)."""
    import httpx
    from crawler.config.settings import settings
    response = httpx.post(
        'https://openrouter.ai/api/v1/chat/completions',
        headers={'Authorization': 'Bearer %s' % settings.openrouter_api_key},
        json={
            'model': model or settings.ai_relevance_model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 3000,
            'temperature': 0,
            "reasoning": {"enabled": False},  # формат, который сторожит test_reasoning_disabled
            'response_format': {'type': 'json_object'},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content'] or ''


def classify_batch(items, call=None):
    # type: (List[Dict[str, Any]], Optional[Callable[[str], str]]) -> Dict[int, Tuple[str, str]]
    """Пачка кандидатов → {индекс в items (с 0): (profile, причина)}."""
    if not items:
        return {}
    answer = (call or openrouter_call)(build_prompt(items))
    return {index - 1: verdict for index, verdict in parse_answer(answer, len(items)).items()}


def batches(items, size):
    # type: (List[Any], int) -> Iterable[List[Any]]
    for start in range(0, len(items), size):
        yield items[start:start + size]


def segment(name, buyer_type=None):
    # type: (Any, Any) -> str
    """Сегмент заказчика по названию (рус./узб.); ручная правка — в реестре."""
    text = _norm(name).casefold().replace('ʻ', "'").replace('‘', "'").replace('’', "'").replace('`', "'")
    for label, pattern in _SEGMENTS:
        if pattern.search(text):
            return label
    if 'budget' in _norm(buyer_type).casefold():
        return 'бюджетная организация'
    return 'другое'


_SEGMENTS = (
    ('страховая', re.compile(r"sug'urta|sugurta|сугурта|суғурта|страхов|insurance")),
    ('банк', re.compile(r"bank|банк|\batb\b|\bатб\b")),
    # Узбекская латиница пишет «ҳ» и как h, и как x: «XARBIY QISM», «xokimligi».
    ('силовые', re.compile(r"harbiy|xarbiy|ҳарбий|харбий|военн|mudofaa|мудофаа|ichki ishlar|ички ишлар|"
                           r"milliy gvardiya|миллий гвардия|gvardiya|xavfsizlik|хавфсизлик|chegara|"
                           r"prokuratura|прокурат|favqulodda|фавқулодда|jazoni ijro|жазони ижро|"
                           r"koloniya|колония|qo'riqlash|қўриқлаш|караул|bojxona|божхона|таможен")),
    ('хокимият', re.compile(r"[hx]okimligi|ҳокимлиги|хокимлиги|[hx]okimiyat|ҳокимият|хокимият")),
    ('министерство/агентство', re.compile(r"vazirligi|вазирлиги|министерств|agentligi|агентлиги|агентств|"
                                          r"qo'mitasi|қўмитаси|комитет|inspeksiya|инспекци|palatasi|палатаси|"
                                          r"markaziy bank|центральный банк|soliq|солиқ|налог")),
    ('образование', re.compile(r"universitet|университет|institut|институт|maktab|мактаб|школ|litsey|лицей|"
                               r"kollej|колледж|akademiya|академи|ta'lim|таълим|bog'cha|боғча|texnikum|"
                               r"техникум|o'quv|ўқув")),
    ('медицина', re.compile(r"shifoxona|шифохона|больниц|tibbiyot|тиббиёт|медицин|klinika|клиник|"
                            r"poliklinika|поликлиник|sanatoriy|санатор|dispanser|диспансер")),
    ('госкомпания/АО', re.compile(r"\baj\b|\bаж\b|aksiyadorlik|акционерн|\bао\b|\bjsc\b|unitar|унитар|"
                                  r"neftegaz|neftgaz|нефтегаз|transgaz|трансгаз|kon-metallurgiya|"
                                  r"горно-металлург|uzauto|temir yo'l|темир йўл|железн|elektr|электр|"
                                  r"energo|энерго|issiqlik|иссиқлик|suv ta'minoti|сув таъминоти|airways")),
    ('частная компания', re.compile(r"\bmchj\b|\bмчж\b|\bооо\b|\bxk\b|\bхк\b|\bqk\b|\bчп\b|\bип\b|"
                                    r"mas'uliyati cheklangan|масъулияти чекланган")),
)


def decision_log_line(subject_hash, subject, profile, reason, model):
    # type: (Optional[str], Any, str, str, str) -> str
    return json.dumps({'hash': subject_hash, 'subject': _norm(subject)[:300], 'profile': profile,
                       'reason': reason, 'model': model, 'prompt': PROMPT_VERSION}, ensure_ascii=False)
