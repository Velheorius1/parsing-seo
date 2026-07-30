"""Редакция секретов в логах — токен бота не должен попадать в файлы.

Дыра, которую это закрывает (найдено 30.07). httpx на уровне INFO печатает
полный URL запроса, а Telegram Bot API носит токен прямо в пути:

    HTTP Request: POST https://api.telegram.org/bot<ТОКЕН>/sendMessage "200 OK"

На момент правки токен лежал в 3 227 строках /var/log/parsing-seo-*.log —
файлов, которые читаются и грепаются в открытую, попадают в бэкапы и в вывод
команд. Ни один скрипт не делал ничего неправильного: так логирует httpx.

Почему фильтр на фабрике записей, а не `getLogger("httpx").setLevel(WARNING)`
и не фильтр на обработчике:

  * `basicConfig` раскидан по двум десяткам скриптов, и половина из них
    вызывает его ДО импорта crawler-модулей, половина после. Фильтр на
    обработчике корневого логгера в этой мешанине то ставится, то затирается —
    защита, зависящая от порядка импортов, не защита.
  * Глушить httpx целиком — терять полезное: какой запрос куда ушёл и с каким
    кодом. Резать надо секрет, а не сигнал.
  * Фабрика записей одна на процесс и работает до всех обработчиков, поэтому
    редактируется и сообщение, и аргументы (httpx кладёт URL именно в args).

Установка идемпотентна и вызывается из `crawler/__init__.py`, то есть
происходит при любом импорте любого модуля пакета.
"""
import logging
import re

# /bot123456789:AA... — цифры, двоеточие, тело. Совпадение только внутри пути
# после «/bot», поэтому обычный текст не трогается.
_TG_TOKEN = re.compile(r"(/bot)\d{6,}:[A-Za-z0-9_\-]{10,}")
# apikey=..., token=..., access_token=... в query-строках.
_QUERY_SECRET = re.compile(
    r"((?:api[_-]?key|token|access[_-]?token|secret|password)=)[^&\s\"']+",
    re.IGNORECASE,
)

_MARK = "_crawler_secret_redaction_installed"


def redact(text):
    # type: (str) -> str
    """Заменить секреты в строке. Идемпотентна: повторная редакция ничего не меняет."""
    text = _TG_TOKEN.sub(r"\1<TOKEN>", text)
    return _QUERY_SECRET.sub(r"\1<REDACTED>", text)


def _redact_any(value):
    """Отредактировать аргумент записи лога.

    Проверять только `isinstance(value, str)` НЕДОСТАТОЧНО, и это выяснилось на
    живом проде, а не в тестах: httpx кладёт в args не строку, а объект
    `httpx.URL`, поэтому первая версия фильтра пропускала токен целиком.
    Поэтому у не-строк смотрим строковое представление и подменяем ТОЛЬКО если
    в нём действительно нашёлся секрет — числа и прочее остаются собой, и
    форматирование `%d`/`%f` не ломается.
    """
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    try:
        text = str(value)
    except Exception:                             # pragma: no cover
        return value
    redacted = redact(text)
    return redacted if redacted != text else value


def install():
    # type: () -> bool
    """Поставить редакцию на фабрику LogRecord. True — поставили сейчас."""
    if getattr(logging, _MARK, False):
        return False
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = dict(
                    (k, _redact_any(v)) for k, v in record.args.items())
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_any(v) for v in record.args)
        return record

    logging.setLogRecordFactory(factory)
    setattr(logging, _MARK, True)
    return True
