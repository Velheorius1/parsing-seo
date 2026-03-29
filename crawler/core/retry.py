"""Retry decorator with exponential backoff."""
import functools
import logging
import time

logger = logging.getLogger(__name__)


def retry(max_attempts=3, backoff_base=2, exceptions=(Exception,)):
    """Decorator that retries on specified exceptions with exponential backoff.

    Usage:
        @retry(max_attempts=3, backoff_base=2, exceptions=(httpx.HTTPStatusError,))
        def my_function():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        delay = backoff_base ** (attempt - 1)
                        logger.warning("[Retry] %s attempt %d/%d failed: %s. Retrying in %ds...",
                                       func.__name__, attempt, max_attempts, str(exc)[:80], delay)
                        time.sleep(delay)
                    else:
                        logger.error("[Retry] %s failed after %d attempts: %s",
                                     func.__name__, max_attempts, str(exc)[:120])
            raise last_exc
        return wrapper
    return decorator
