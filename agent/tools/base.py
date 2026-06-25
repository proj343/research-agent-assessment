"""Base tool interface and shared utilities."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
import functools
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    content: str
    sources: list = field(default_factory=list)
    success: bool = True
    error: str = ""


def with_retry(max_attempts: int = 3, backoff: float = 1.5):
    """Retry decorator with exponential backoff; longer wait on 429 rate-limit errors."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        # Rate-limit responses need a longer pause
                        is_429 = "429" in str(e) or "Too Many Requests" in str(e)
                        wait = 10.0 if is_429 else backoff ** attempt
                        logger.warning(f"{func.__name__} attempt {attempt+1} failed: {e}. Retrying in {wait:.1f}s")
                        time.sleep(wait)
            raise last_exc
        return wrapper
    return decorator


class BaseTool(ABC):
    name: str
    description: str

    @abstractmethod
    def run(self, query: str) -> ToolResult:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
