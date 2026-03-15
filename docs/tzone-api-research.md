# TZone.uz API Research

**Дата:** 2026-03-15
**Сайт:** https://tzone.uz (Uzbekistan instance of TenderZone by Saby/SBIS)

## Архитектура

TZone.uz построен на платформе **Saby/SBIS** (ранее Tensor). Фронтенд - кастомный фреймворк Wasaby (собственный React-like), бэкенд - SBIS JSON-RPC.

- **Product ID:** `tenderzone-uz`
- **xDomain:** `tzone-uz`
- **Авторизация:** SSO через `sso.saby.uz` (trade.tzone.uz -> sso.saby.uz)
- **Build:** 26.1227-4

## API Endpoints

### Base URLs

| Endpoint | URL | Описание |
|----------|-----|----------|
| Main Service | `POST https://tzone.uz/service/?srv=1` | Основной RPC-сервис (публичный) |
| Tender Service | `POST https://tzone.uz/tender/service/?srv=1` | Требует авторизации |
| Tender SQL | `POST https://tzone.uz/tendersql/service/?srv=1` | Не существует (404) |
| SSO | `https://sso.saby.uz/a-auth/` | Авторизация |
| Trade | `https://trade.tzone.uz` | Личный кабинет (redirect -> SSO) |

### Протокол вызова (SBIS JSON-RPC)

```
POST /service/?srv=1
Content-Type: application/json; charset=utf-8;type=rpc
X-Requested-With: XMLHttpRequest

{
  "jsonrpc": "2.0",
  "protocol": 4,
  "method": "Contract.Method",
  "params": {
    "ДопПоля": [],              // ОБЯЗАТЕЛЬНО (пустой массив)
    "Фильтр": { SBIS Record },  // Фильтрация
    "Сортировка": null | { SBIS RecordSet },
    "Навигация": { SBIS Record } // Пагинация
  },
  "id": 1
}
```

**SBIS Record формат:**
```json
{
  "_type": "record",
  "d": [value1, value2, ...],  // data array (порядок = порядок в "s")
  "s": [                       // schema
    {"n": "field_name", "t": "Строка"},
    {"n": "array_field", "t": {"n": "Массив", "t": "Число целое"}}
  ],
  "f": 0                       // format flags
}
```

**Навигация (пагинация):**
```json
{
  "_type": "record",
  "d": [0, 15, true],  // [страница, размер, естьЕще]
  "s": [
    {"n": "Страница", "t": "Число целое"},
    {"n": "РазмерСтраницы", "t": "Число целое"},
    {"n": "ЕстьЕще", "t": "Логическое"}
  ],
  "f": 0
}
```

Response: `result.n = true/false` (есть ли следующая страница)

## Найденные методы (публичные, без авторизации)

### 1. Tender.GetList -- ПОИСК ТЕНДЕРОВ

```
Method: Tender.GetList
Endpoint: /service/?srv=1
Auth: Public (но часть полей замаскирована XXXXXX)
```

**Фильтр (обязательные поля):**

| Поле | Тип | Описание | Пример |
|------|-----|----------|--------|
| stateid_arr | Массив<Число целое> | Этап тендера | [2] = Прием заявок |
| tenderType | Число целое | Тип | 1 = тендеры |
| fts_string | Строка | Полнотекстовый поиск | "упаковка" |
| delivery_place_region_code | Массив<Строка> | Регион поставки | ["860"] = Узбекистан |
| region_code_filter | Массив<Строка> | Регион фильтр | ["860"] |
| articleId | Массив<Число целое> | ID категории | [133291176] |

**Опциональные поля фильтра:**
- `is_promo` (Логическое) - промо-режим
- `country_interface` (Строка) - код страны интерфейса "860"
- `selectedIds` (Массив<Число целое>) - получить конкретные тендеры по ID

**Рабочий пример:**
```bash
curl -s -X POST 'https://tzone.uz/service/?srv=1' \
  -H 'Content-Type: application/json; charset=utf-8;type=rpc' \
  -d '{
    "jsonrpc":"2.0","protocol":4,
    "method":"Tender.GetList",
    "params":{
      "ДопПоля":[],
      "Фильтр":{
        "_type":"record",
        "d":[[2],1,"упаковка",[],[],[]],
        "s":[
          {"n":"stateid_arr","t":{"n":"Массив","t":"Число целое"}},
          {"n":"tenderType","t":"Число целое"},
          {"n":"fts_string","t":"Строка"},
          {"n":"delivery_place_region_code","t":{"n":"Массив","t":"Строка"}},
          {"n":"region_code_filter","t":{"n":"Массив","t":"Строка"}},
          {"n":"articleId","t":{"n":"Массив","t":"Число целое"}}
        ],"f":0
      },
      "Сортировка":null,
      "Навигация":{
        "_type":"record",
        "d":[0,15,true],
        "s":[
          {"n":"Страница","t":"Число целое"},
          {"n":"РазмерСтраницы","t":"Число целое"},
          {"n":"ЕстьЕще","t":"Логическое"}
        ],"f":0
      }
    },"id":1
  }'
```

**Поля ответа (67 полей):**

| # | Поле | Тип | Public | Описание |
|---|------|-----|--------|----------|
| 0 | id | int | Yes | ID тендера |
| 1 | publishdate | datetime | Yes | Дата публикации |
| 2 | actual_sort_date | datetime | Yes | Дата сортировки |
| 3 | creationdate | datetime | Yes | Дата создания |
| 4 | amount | money | Yes | Сумма (может быть null) |
| 5 | lotname | string | Yes | Название лота |
| 6 | tendername | string | Yes | Название тендера |
| 7 | endofferdate | datetime | Yes | Дедлайн подачи заявок |
| 8 | regionbrief | string | Yes | Регион |
| 9 | parentregionid | int | Yes | ID родительского региона |
| 10 | proctypeid | int | Yes | ID типа процедуры |
| 11 | proctype | string | Yes | Тип процедуры |
| 12 | tender_category | string | Yes | Категория ("tender") |
| 13 | proctype_brief | string | Yes | Краткое название типа |
| 14 | proctype_name | string | Yes | Полное название типа |
| 18 | currencybrief | string | Yes | Валюта (RUB, UZS, KZT) |
| 19 | stateid | int | Yes | ID статуса |
| 20 | stateaggid | int | Yes | Агрегированный статус |
| 23 | organizername | string | Yes | Название организатора |
| 24 | organizerfullname | string | Yes | Полное название |
| 36 | region_code | string | Yes | Код региона ("860") |
| 42 | url | string | Yes | URL карточки тендера |
| 44 | till_string | string | Yes | "Осталось 3 дня" |
| 45 | is_late | bool | Yes | Просрочен ли |
| 46+ | tendernumber, tpbrief, tp_id... | string | **MASKED** | Замаскированы (XXXX) |

### 2. TenderRegion.List -- РЕГИОНЫ

```
Method: TenderRegion.List
Auth: Public
```

**Фильтр:**
| Поле | Тип | Описание |
|------|-----|----------|
| country_code | Строка | ISO код страны ("860"=UZ, "643"=RU, "398"=KZ) |
| layers | Массив<Строка> | ["Regions", "RegionDistricts", "Cities"] |

**Ответ:** код региона, название, уровень, GeoObject, ISO3166-1Numeric, и т.д.

### 3. Category.getCategory -- КАТЕГОРИИ

```
Method: Category.getCategory
Auth: Public
```

**Фильтр:**
| Поле | Тип | Описание |
|------|-----|----------|
| country_interface | Строка | "860" = Узбекистан |

**Ответ:** ID, название категории, флаги.

Основные категории (UZ): Безопасность, Бизнес, Бумага/упаковка, Ветеринария, Деревообработка, IT, Машиностроение, Медицина, Металлы, Наука, Недвижимость, Офис, Перевозки, Полиграфия, Продукты, Связь, Сельское хозяйство, Спорт, Стройматериалы, Строительство, Сырье, ТНП, Топливо/Энергетика, Транспорт, и др.

### 4. TradingPlatform.GetList -- ТОРГОВЫЕ ПЛОЩАДКИ

```
Method: TradingPlatform.GetList
Auth: Public
```

**Фильтр:** пустой (все площадки) или с country_code

**Ответ (438 площадок):**

| Поле | Описание |
|------|----------|
| tp | ID |
| tradingplatformid | Platform ID |
| name | Название |
| brief | Краткое название |
| url | URL площадки |
| meter | Количество тендеров |
| type | Тип (group/individual) |
| type@ | True = группа |
| child_platforms | Массив ID дочерних площадок |
| country | Код страны (RU, UZ, KZ, BY, KG) |

### 5. Tender.GetStateAgg -- СТАТУСЫ ТЕНДЕРОВ

```
Method: Tender.GetStateAgg
Auth: Public
```

**Результат:**
| ID | Статус |
|----|--------|
| 2 | Прием заявок |
| 3 | Рассмотрение заявок |
| 4 | Завершен |
| 5 | Отменен |
| 41 | С победителем |
| 42 | Не состоялись |

### 6. TenderFilters.get_section_list -- СЕКЦИИ ФИЛЬТРА

```
Method: TenderFilters.get_section_list
Auth: Public
```

**Результат:** Малые закупки (2), Кроме малых закупок (-1)

## Методы требующие авторизации (из JS-кода)

| Метод | Контракт | Описание |
|-------|----------|----------|
| Tender.get_user_options | Tender | Пользовательские настройки |
| Tender.set_participate_in_tender | Tender | Участие в тендере |
| Tender.get_last_users | Tender | Последние пользователи |
| Tender.update_last_users | Tender | Обновить список пользователей |
| TenderMassOperations.massOperations | TenderMassOperations | Массовые операции |
| TenderMassOperations.mass_change_interesting | TenderMassOperations | Отметить интересные |
| TenderMassOperations.mass_delete_favourites | TenderMassOperations | Удалить из избранного |
| TenderMassOperations.mass_add_favourites | TenderMassOperations | Добавить в избранное |
| TenderMassOperations.mass_move_favourites | TenderMassOperations | Переместить в папку |
| TenderMassOperations.mass_read_tenders | TenderMassOperations | Отметить прочитанными |
| TenderMassOperations.mass_prepare_save_data | TenderMassOperations | Подготовка данных |
| WeOrganize.GetStates | WeOrganize | Статусы "мы организуем" |

## Торговые площадки (438 индивидуальных)

### По странам:
| Страна | Кол-во площадок |
|--------|----------------|
| Россия (RU) | 399 |
| Узбекистан (UZ) | 21 |
| Казахстан (KZ) | 14 |
| Беларусь (BY) | 3 |
| Кыргызстан (KG) | 1 |

### Группы площадок:
| Группа | Площадки | Тендеры |
|--------|----------|---------|
| 44-ФЗ Госзакупки | 12 | 8,560,124 |
| 223-ФЗ Госкорпорации | 87 | 2,764,662 |
| Коммерческие закупки | 291 | 15,618,675 |
| 615-ПП Капремонт | 9 | 157,953 |
| Международные | 39 | 11,825,544 |

### Топ-10 площадок по тендерам:
1. Госзакупки Казахстана (goszakup.gov.kz) - 5,705,436
2. ГИАС Беларусь (gias.by) - 2,554,547
3. УЗТСБ (uzex.uz) - 2,045,541
4. РТС-тендер (zmo.rts-tender.ru) - 1,863,900
5. Сбербанк-АСТ (sberbank-ast.ru) - 1,797,996
6. Росэлторг (etp.roseltorg.ru) - 1,740,683
7. Портал поставщиков Москвы (zakupki.mos.ru) - 1,500,039
8. Березка ЕАТ (agregatoreat.ru) - 1,452,172
9. РТС-тендер 223 (rts-tender.ru) - 1,334,095
10. Мосрег электронный магазин (market.mosreg.ru) - 1,195,715

## URL паттерны (HTML-страницы)

| URL | Описание |
|-----|----------|
| /ru/list?keywords={query} | Поиск тендеров |
| /ru/page/tender-card/{id} | Карточка тендера |
| /ru/categories/{slug} | Категория |
| /ru/regions/{name} | Регион |
| /#tariffs | Тарифы |
| /#contacts | Контакты |

## Замаскированные данные (требуют подписки)

Следующие поля в ответе Tender.GetList замаскированы символами "X" без авторизации:
- `tendernumber` - номер тендера
- `tpbrief` - название торговой площадки
- `tptype` - тип площадки
- `tp_id` - ID на площадке
- `winprice` - цена победителя
- `wincurrencybrief` - валюта победителя
- `winnername` - имя победителя
- `tradingplatformsppid` - SPPID площадки
- `tradingplatform_spp_uuid` - UUID площадки
- `tradingplatformurl` - URL площадки
- `tp_sppid_logo` / `tp_logo_url` - логотип площадки

## Контакты

- Телефон: +998 55 516 52 25
- Email: newtenderzoneuz@gmail.com
- Telegram бот: https://t.me/newtenderzone_bot

## Ограничения

1. Без авторизации: номер тендера, площадка, победитель - замаскированы
2. Максимальный pageSize не ограничен (тестировалось 100)
3. Нет rate limiting обнаружено
4. Telegram бот - только для уведомлений, не API
5. `/tender/service/` требует авторизации через SSO
6. Encoding: обязательно `charset=utf-8` в Content-Type для кириллических параметров
