"""Tests for LLM factory (create_llm), GroqLLM, and OllamaLLM.

GroqLLM imports groq.Groq *inside* __init__ (lazy import), so we patch via
sys.modules rather than patch("agent.llm.Groq").
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from agent.llm import BaseLLM, GroqLLM, OllamaLLM, create_llm


def _mock_groq_module() -> MagicMock:
    """Return a mock that stands in for the groq package."""
    mod = MagicMock()
    mod.Groq = MagicMock()
    return mod


# ---------------------------------------------------------------------------
# create_llm factory
# ---------------------------------------------------------------------------


class TestCreateLLM:
    def test_groq_provider_returns_groq_llm(self):
        mock_mod = _mock_groq_module()
        with (
            patch.dict(sys.modules, {"groq": mock_mod}),
            patch.dict(os.environ, {"GROQ_API_KEY": "key"}),
        ):
            llm = create_llm(provider="groq")
        assert isinstance(llm, GroqLLM)

    def test_ollama_provider_returns_ollama_llm(self):
        llm = create_llm(provider="ollama")
        assert isinstance(llm, OllamaLLM)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm(provider="gpt99")

    def test_default_provider_is_groq_when_env_unset(self):
        mock_mod = _mock_groq_module()
        env = {k: v for k, v in os.environ.items() if k not in ("LLM_PROVIDER", "LLM_MODEL")}
        env["GROQ_API_KEY"] = "key"
        with patch.dict(sys.modules, {"groq": mock_mod}), patch.dict(os.environ, env, clear=True):
            llm = create_llm()
        assert isinstance(llm, GroqLLM)

    def test_reads_provider_from_env(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}):
            llm = create_llm()
        assert isinstance(llm, OllamaLLM)

    def test_model_kwarg_overrides_default(self):
        mock_mod = _mock_groq_module()
        with (
            patch.dict(sys.modules, {"groq": mock_mod}),
            patch.dict(os.environ, {"GROQ_API_KEY": "key"}),
        ):
            llm = create_llm(provider="groq", model="custom-model-x")
        assert llm.model == "custom-model-x"

    def test_reads_model_from_env(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "LLM_MODEL": "mistral:7b"}):
            llm = create_llm()
        assert llm.model == "mistral:7b"

    def test_ollama_default_model(self):
        env = {k: v for k, v in os.environ.items() if k not in ("LLM_PROVIDER", "LLM_MODEL")}
        with patch.dict(os.environ, env, clear=True):
            llm = create_llm(provider="ollama")
        assert llm.model == "llama3.2:3b"

    def test_groq_default_model_contains_llama(self):
        mock_mod = _mock_groq_module()
        with (
            patch.dict(sys.modules, {"groq": mock_mod}),
            patch.dict(os.environ, {"GROQ_API_KEY": "key"}),
        ):
            llm = create_llm(provider="groq")
        assert "llama" in llm.model.lower()

    def test_returns_base_llm_subclass(self):
        llm = create_llm(provider="ollama")
        assert isinstance(llm, BaseLLM)


# ---------------------------------------------------------------------------
# GroqLLM
# ---------------------------------------------------------------------------


class TestGroqLLM:
    def _make_groq(self, api_key: str = "test_key", model: str = "test-model") -> tuple:
        """Return (llm, mock_client) with groq module patched."""
        mock_mod = _mock_groq_module()
        mock_client_instance = MagicMock()
        mock_mod.Groq.return_value = mock_client_instance
        with patch.dict(sys.modules, {"groq": mock_mod}):
            llm = GroqLLM(api_key=api_key, model=model)
        return llm, mock_client_instance, mock_mod

    def test_missing_api_key_raises_key_error(self):
        mock_mod = _mock_groq_module()
        env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
        with patch.dict(sys.modules, {"groq": mock_mod}), patch.dict(os.environ, env, clear=True):
            with pytest.raises(KeyError):
                GroqLLM()

    def test_missing_groq_package_raises_import_error(self):
        with patch.dict(sys.modules, {"groq": None}):
            with pytest.raises(ImportError, match="pip install groq"):
                GroqLLM(api_key="key")

    def test_uses_api_key_from_env(self):
        mock_mod = _mock_groq_module()
        with (
            patch.dict(sys.modules, {"groq": mock_mod}),
            patch.dict(os.environ, {"GROQ_API_KEY": "env_key"}),
        ):
            GroqLLM()
        mock_mod.Groq.assert_called_once_with(api_key="env_key")

    def test_explicit_api_key_overrides_env(self):
        mock_mod = _mock_groq_module()
        with (
            patch.dict(sys.modules, {"groq": mock_mod}),
            patch.dict(os.environ, {"GROQ_API_KEY": "env_key"}),
        ):
            GroqLLM(api_key="explicit_key")
        mock_mod.Groq.assert_called_once_with(api_key="explicit_key")

    def test_complete_returns_message_content(self):
        llm, mock_client, _ = self._make_groq()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "The answer to your question."
        mock_client.chat.completions.create.return_value = mock_response

        result = llm.complete([{"role": "user", "content": "What is GDP?"}])
        assert result == "The answer to your question."

    def test_complete_passes_model_temperature_max_tokens(self):
        llm, mock_client, _ = self._make_groq(model="my-model")
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "ok"
        mock_client.chat.completions.create.return_value = mock_response

        llm.complete([{"role": "user", "content": "hi"}], temperature=0.7, max_tokens=256)

        kw = mock_client.chat.completions.create.call_args[1]
        assert kw["model"] == "my-model"
        assert kw["temperature"] == 0.7
        assert kw["max_tokens"] == 256

    def test_complete_passes_full_message_list(self):
        llm, mock_client, _ = self._make_groq()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "ok"
        mock_client.chat.completions.create.return_value = mock_response

        messages = [
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "Hello"},
        ]
        llm.complete(messages)

        passed = mock_client.chat.completions.create.call_args[1]["messages"]
        assert passed == messages

    def test_model_stored_on_instance(self):
        mock_mod = _mock_groq_module()
        with (
            patch.dict(sys.modules, {"groq": mock_mod}),
            patch.dict(os.environ, {"GROQ_API_KEY": "k"}),
        ):
            llm = GroqLLM(model="special-model")
        assert llm.model == "special-model"


# ---------------------------------------------------------------------------
# OllamaLLM
# ---------------------------------------------------------------------------


class TestOllamaLLM:
    def test_default_base_url(self):
        env = {k: v for k, v in os.environ.items() if k != "OLLAMA_BASE_URL"}
        with patch.dict(os.environ, env, clear=True):
            llm = OllamaLLM()
        assert llm.base_url == "http://localhost:11434"

    def test_base_url_from_env(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://gpu-box:11434"}):
            llm = OllamaLLM()
        assert llm.base_url == "http://gpu-box:11434"

    def test_explicit_base_url_overrides_env(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://env-box:11434"}):
            llm = OllamaLLM(base_url="http://explicit:11434")
        assert llm.base_url == "http://explicit:11434"

    def test_model_stored_on_instance(self):
        llm = OllamaLLM(model="phi3:mini")
        assert llm.model == "phi3:mini"

    def test_complete_returns_message_content(self):
        llm = OllamaLLM(model="llama3.2:3b")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "Ollama says hello."}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(llm._requests, "post", return_value=mock_resp):
            result = llm.complete([{"role": "user", "content": "hi"}])

        assert result == "Ollama says hello."

    def test_complete_posts_to_correct_url(self):
        llm = OllamaLLM(base_url="http://my-server:11434")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "ok"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(llm._requests, "post", return_value=mock_resp) as mock_post:
            llm.complete([])

        assert mock_post.call_args[0][0] == "http://my-server:11434/api/chat"

    def test_complete_sends_correct_json_body(self):
        llm = OllamaLLM(model="gemma:2b")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "ok"}}
        mock_resp.raise_for_status = MagicMock()
        messages = [{"role": "user", "content": "test"}]

        with patch.object(llm._requests, "post", return_value=mock_resp) as mock_post:
            llm.complete(messages, temperature=0.3, max_tokens=512)

        body = mock_post.call_args[1]["json"]
        assert body["model"] == "gemma:2b"
        assert body["messages"] == messages
        assert body["stream"] is False
        assert body["options"]["temperature"] == 0.3
        assert body["options"]["num_predict"] == 512

    def test_complete_raises_on_bad_status(self):
        llm = OllamaLLM()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("Connection refused")

        with patch.object(llm._requests, "post", return_value=mock_resp):
            with pytest.raises(Exception, match="Connection refused"):
                llm.complete([])
