# Anti-patterns: API calls and deployment

## SBIS JSON-RPC (TenderZone)

```python
# BAD: missing Навигация param — method returns 404
body = {"method": "TradingPlatform.GetList", "params": {"Фильтр": {...}}}

# GOOD: always include Навигация for list methods
body = {
    "method": "TradingPlatform.GetList",
    "params": {
        "ДопПоля": [],
        "Фильтр": {...},
        "Сортировка": None,
        "Навигация": {
            "_type": "record",
            "d": [0, 500, True],
            "s": [
                {"n": "Страница", "t": "Число целое"},
                {"n": "РазмерСтраницы", "t": "Число целое"},
                {"n": "ЕстьЕще", "t": "Логическое"},
            ],
            "f": 0,
        },
    },
}
```

```python
# BAD: missing charset in Content-Type — Cyrillic params break
headers = {"Content-Type": "application/json"}

# GOOD: charset=utf-8 mandatory for SBIS RPC
headers = {"Content-Type": "application/json; charset=utf-8;type=rpc"}
```

## VPS deploy

```bash
# BAD: git pull when VPS has local changes — aborts
ssh vps "cd /opt/parsing-seo && git pull"

# GOOD: stash first, then pull, then pop
ssh vps "cd /opt/parsing-seo && git stash && git pull && git stash pop"
```

## Telegram messages

```python
# BAD: raw user content in Markdown message — breaks formatting
text = "*Report*\n%s" % user_org_name  # org may contain * _ `

# GOOD: escape Markdown special chars
safe = user_org_name.replace("*", "").replace("_", "").replace("`", "")
text = "*Report*\n%s" % safe
```
