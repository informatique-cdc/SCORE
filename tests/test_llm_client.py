"""Tests for llm/client.py — LLMClient initialization, chat, embed, retry, fallback."""

from unittest.mock import patch


from llm.client import LLMClient, LLMResponse, get_llm_client


# ---------------------------------------------------------------------------
# LLMResponse dataclass
# ---------------------------------------------------------------------------


class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse(content="hello", model="gpt-4o")
        assert r.content == "hello"
        assert r.model == "gpt-4o"
        assert r.usage == {}

    def test_with_usage(self):
        r = LLMResponse(content="hi", model="m", usage={"total_tokens": 10})
        assert r.usage["total_tokens"] == 10


# ---------------------------------------------------------------------------
# LLMClient initialization
# ---------------------------------------------------------------------------


class TestLLMClientInit:
    @patch("llm.client.OpenAI")
    def test_openai_provider(self, mock_openai_cls, settings):
        settings.LLM_CONFIG = {
            "provider": "openai",
            "openai": {
                "api_key": "sk-test",
                "chat_model": "gpt-4o",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimensions": 1536,
            },
            "azure": {
                "api_key": "",
                "endpoint": "",
                "api_version": "",
                "embedding_deployment": "",
                "embedding_endpoint": "",
                "embedding_api_key": "",
                "embedding_dimensions": 1536,
            },
            "azure_mistral": {
                "api_key": "",
                "endpoint": "",
                "deployment_name": "",
                "chat_model": "",
            },
            "requests_per_minute": 60,
            "embedding_batch_size": 100,
            "fallback_models": [],
            "fallback_retries_per_model": 2,
            "batch_model": "",
            "batch_poll_interval_seconds": 30,
            "batch_max_wait_seconds": 1800,
        }
        settings.ANALYSIS_CONFIG = {}

        client = LLMClient()
        assert client.provider == "openai"
        assert client._chat_model == "gpt-4o"
        assert client._embed_model == "text-embedding-3-small"
        mock_openai_cls.assert_called_once_with(api_key="sk-test")

    @patch("llm.client.AzureOpenAI")
    def test_azure_provider(self, mock_azure_cls, settings):
        settings.LLM_CONFIG = {
            "provider": "azure",
            "openai": {
                "api_key": "",
                "chat_model": "",
                "embedding_model": "",
                "embedding_dimensions": 1536,
            },
            "azure": {
                "api_key": "az-key",
                "endpoint": "https://myendpoint.openai.azure.com",
                "api_version": "2024-06-01",
                "chat_deployment": "gpt-4o",
                "embedding_deployment": "text-embedding",
                "embedding_endpoint": "",
                "embedding_api_key": "",
                "embedding_dimensions": 1536,
            },
            "azure_mistral": {
                "api_key": "",
                "endpoint": "",
                "deployment_name": "",
                "chat_model": "",
            },
            "requests_per_minute": 60,
            "embedding_batch_size": 100,
            "fallback_models": [],
            "fallback_retries_per_model": 2,
            "batch_model": "",
            "batch_poll_interval_seconds": 30,
            "batch_max_wait_seconds": 1800,
        }
        settings.ANALYSIS_CONFIG = {}

        client = LLMClient()
        assert client.provider == "azure"
        assert client._chat_model == "gpt-4o"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    @patch("llm.client.OpenAI")
    def test_rate_limit_sleeps(self, mock_openai_cls, settings):
        settings.LLM_CONFIG = {
            "provider": "openai",
            "openai": {
                "api_key": "k",
                "chat_model": "m",
                "embedding_model": "e",
                "embedding_dimensions": 1536,
            },
            "azure": {
                "api_key": "",
                "endpoint": "",
                "api_version": "",
                "embedding_deployment": "",
                "embedding_endpoint": "",
                "embedding_api_key": "",
                "embedding_dimensions": 1536,
            },
            "azure_mistral": {
                "api_key": "",
                "endpoint": "",
                "deployment_name": "",
                "chat_model": "",
            },
            "requests_per_minute": 6000,  # very high RPM
            "embedding_batch_size": 100,
            "fallback_models": [],
            "fallback_retries_per_model": 2,
            "batch_model": "",
            "batch_poll_interval_seconds": 30,
            "batch_max_wait_seconds": 1800,
        }
        settings.ANALYSIS_CONFIG = {}

        client = LLMClient()
        assert client._min_interval < 0.1  # very short interval at 6000 RPM


# ---------------------------------------------------------------------------
# _supports_temperature
# ---------------------------------------------------------------------------


class TestSupportsTemperature:
    @patch("llm.client.OpenAI")
    def test_normal_model(self, mock_openai_cls, settings):
        settings.LLM_CONFIG = {
            "provider": "openai",
            "openai": {
                "api_key": "k",
                "chat_model": "gpt-4o",
                "embedding_model": "e",
                "embedding_dimensions": 1536,
            },
            "azure": {
                "api_key": "",
                "endpoint": "",
                "api_version": "",
                "embedding_deployment": "",
                "embedding_endpoint": "",
                "embedding_api_key": "",
                "embedding_dimensions": 1536,
            },
            "azure_mistral": {
                "api_key": "",
                "endpoint": "",
                "deployment_name": "",
                "chat_model": "",
            },
            "requests_per_minute": 60,
            "embedding_batch_size": 100,
            "fallback_models": [],
            "fallback_retries_per_model": 2,
            "batch_model": "",
            "batch_poll_interval_seconds": 30,
            "batch_max_wait_seconds": 1800,
        }
        settings.ANALYSIS_CONFIG = {}
        client = LLMClient()
        assert client._supports_temperature("gpt-4o") is True
        assert client._supports_temperature("gpt-4.1") is True

    @patch("llm.client.OpenAI")
    def test_reasoning_models(self, mock_openai_cls, settings):
        settings.LLM_CONFIG = {
            "provider": "openai",
            "openai": {
                "api_key": "k",
                "chat_model": "o1",
                "embedding_model": "e",
                "embedding_dimensions": 1536,
            },
            "azure": {
                "api_key": "",
                "endpoint": "",
                "api_version": "",
                "embedding_deployment": "",
                "embedding_endpoint": "",
                "embedding_api_key": "",
                "embedding_dimensions": 1536,
            },
            "azure_mistral": {
                "api_key": "",
                "endpoint": "",
                "deployment_name": "",
                "chat_model": "",
            },
            "requests_per_minute": 60,
            "embedding_batch_size": 100,
            "fallback_models": [],
            "fallback_retries_per_model": 2,
            "batch_model": "",
            "batch_poll_interval_seconds": 30,
            "batch_max_wait_seconds": 1800,
        }
        settings.ANALYSIS_CONFIG = {}
        client = LLMClient()
        assert client._supports_temperature("o1") is False
        assert client._supports_temperature("o3") is False
        assert client._supports_temperature("o4-mini") is False
        assert client._supports_temperature("gpt-5") is False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestGetLLMClient:
    @patch("llm.client.OpenAI")
    def test_singleton(self, mock_openai_cls, settings):
        settings.LLM_CONFIG = {
            "provider": "openai",
            "openai": {
                "api_key": "k",
                "chat_model": "m",
                "embedding_model": "e",
                "embedding_dimensions": 1536,
            },
            "azure": {
                "api_key": "",
                "endpoint": "",
                "api_version": "",
                "embedding_deployment": "",
                "embedding_endpoint": "",
                "embedding_api_key": "",
                "embedding_dimensions": 1536,
            },
            "azure_mistral": {
                "api_key": "",
                "endpoint": "",
                "deployment_name": "",
                "chat_model": "",
            },
            "requests_per_minute": 60,
            "embedding_batch_size": 100,
            "fallback_models": [],
            "fallback_retries_per_model": 2,
            "batch_model": "",
            "batch_poll_interval_seconds": 30,
            "batch_max_wait_seconds": 1800,
        }
        settings.ANALYSIS_CONFIG = {}

        import llm.client as mod

        mod._client = None  # Reset singleton

        c1 = get_llm_client()
        c2 = get_llm_client()
        assert c1 is c2

        mod._client = None  # Clean up
