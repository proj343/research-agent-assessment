"""LLM abstraction — supports Groq (free cloud) and Ollama (local)."""

import logging
import os
import re
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseLLM(ABC):
    """Common interface for all LLM backends."""

    @abstractmethod
    def complete(
        self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 4096
    ) -> str:
        """Send an OpenAI-style message list and return the assistant reply as a string."""


class GroqLLM(BaseLLM):
    """Groq cloud inference — free tier, fast. Get key at console.groq.com."""

    def __init__(
        self, model: str = "meta-llama/llama-4-scout-17b-16e-instruct", api_key: str | None = None
    ):
        try:
            from groq import Groq
        except ImportError:
            raise ImportError("Install groq: pip install groq")
        self.client = Groq(api_key=api_key or os.environ["GROQ_API_KEY"])
        self.model = model
        logger.info(f"GroqLLM initialized: model={model}")

    def complete(
        self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 4096
    ) -> str:
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content
            except Exception as e:
                is_rate_limit = "429" in str(e) or "rate_limit" in str(e).lower()
                if is_rate_limit and attempt < 2:
                    match = re.search(r"try again in (\d+\.?\d*)s", str(e))
                    wait = min(float(match.group(1)) if match else 30.0, 60.0)
                    logger.warning(f"Groq rate limit hit, retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise


class OllamaLLM(BaseLLM):
    """Ollama local inference — fully offline, no API key required."""

    def __init__(self, model: str = "llama3.2:3b", base_url: str | None = None):
        import requests as _requests

        self._requests = _requests
        self.model = model
        self.base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        logger.info(f"OllamaLLM initialized: model={model}, base_url={self.base_url}")

    def complete(
        self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 4096
    ) -> str:
        resp = self._requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def create_llm(provider: str | None = None, model: str | None = None) -> BaseLLM:
    """Factory — reads LLM_PROVIDER and LLM_MODEL from env if not specified."""
    provider = provider or os.environ.get("LLM_PROVIDER", "groq")
    if provider == "groq":
        # Default: llama-4-scout — 17B Llama 4 model, fast on Groq, good reasoning
        # For best quality, set LLM_MODEL=llama-3.3-70b-versatile (100K token/day limit)
        model = model or os.environ.get("LLM_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
        return GroqLLM(model=model)
    if provider == "ollama":
        model = model or os.environ.get("LLM_MODEL", "llama3.2:3b")
        return OllamaLLM(model=model)
    raise ValueError(f"Unknown LLM provider: {provider!r}. Choose 'groq' or 'ollama'.")
