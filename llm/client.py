"""
LLM client abstraction supporting OpenAI, Azure OpenAI, and Azure Mistral.

Usage:
    from llm.client import get_llm_client
    client = get_llm_client()
    response = client.chat("Summarize this document", system="You are a helpful assistant.")
    embeddings = client.embed(["text1", "text2"])
"""
import io
import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.conf import settings
from openai import APIConnectionError, APITimeoutError, AzureOpenAI, OpenAI, RateLimitError

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict = field(default_factory=dict)


class LLMClient:
    """Unified interface for chat and embedding calls, with rate limiting and batching."""

    def __init__(self):
        config = settings.LLM_CONFIG
        self.provider = config["provider"]
        self._rpm = config.get("requests_per_minute", 60)
        self._batch_size = config.get("embedding_batch_size", 100)
        self._last_call_time = 0.0
        self._min_interval = 60.0 / self._rpm if self._rpm > 0 else 0

        if self.provider == "azure":
            azure_cfg = config["azure"]
            self._client = AzureOpenAI(
                api_key=azure_cfg["api_key"],
                azure_endpoint=azure_cfg["endpoint"],
                api_version=azure_cfg["api_version"],
            )
            self._chat_model = azure_cfg["chat_deployment"]
            self._embed_model = azure_cfg["embedding_deployment"]
            # Separate Azure endpoint/key for embeddings (different resource)
            embed_endpoint = azure_cfg.get("embedding_endpoint")
            embed_api_key = azure_cfg.get("embedding_api_key") or azure_cfg["api_key"]
            if embed_endpoint:
                self._embed_client = AzureOpenAI(
                    api_key=embed_api_key,
                    azure_endpoint=embed_endpoint,
                    api_version=azure_cfg["api_version"],
                )
            else:
                self._embed_client = self._client
        elif self.provider == "azure_mistral":
            # Chat: Mistral on Azure AI Foundry (serverless MaaS)
            # Endpoint expects: {host}/models/chat/completions?api-version=...
            # OpenAI SDK appends /chat/completions to base_url, so base_url = {host}/models
            mistral_cfg = config["azure_mistral"]
            parsed = urlparse(mistral_cfg["endpoint"])
            base = f"{parsed.scheme}://{parsed.netloc}"
            path = parsed.path.rstrip("/")
            if "/models" in path:
                base_url = base + path[:path.index("/models") + len("/models")]
            else:
                base_url = base + "/models"
            self._client = OpenAI(
                base_url=base_url,
                api_key=mistral_cfg["api_key"],
                default_query={"api-version": mistral_cfg.get("api_version", "2024-05-01-preview")},
            )
            self._chat_model = mistral_cfg["deployment_name"] or mistral_cfg["chat_model"]
            # Embeddings: reuse Azure OpenAI endpoint
            azure_cfg = config["azure"]
            self._embed_model = azure_cfg["embedding_deployment"]
            embed_endpoint = azure_cfg.get("embedding_endpoint")
            embed_api_key = azure_cfg.get("embedding_api_key") or azure_cfg["api_key"]
            if embed_endpoint:
                self._embed_client = AzureOpenAI(
                    api_key=embed_api_key,
                    azure_endpoint=embed_endpoint,
                    api_version=azure_cfg["api_version"],
                )
            else:
                self._embed_client = AzureOpenAI(
                    api_key=azure_cfg["api_key"],
                    azure_endpoint=azure_cfg["endpoint"],
                    api_version=azure_cfg["api_version"],
                )
        else:
            openai_cfg = config["openai"]
            self._client = OpenAI(api_key=openai_cfg["api_key"])
            self._embed_client = self._client
            self._chat_model = openai_cfg["chat_model"]
            self._embed_model = openai_cfg["embedding_model"]

        # Mistral API uses "max_tokens"; OpenAI/Azure OpenAI use "max_completion_tokens"
        self._max_tokens_key = (
            "max_tokens" if self.provider == "azure_mistral" else "max_completion_tokens"
        )

        embed_provider = "azure" if self.provider == "azure_mistral" else self.provider
        self._embed_dimensions = config.get(embed_provider, {}).get(
            "embedding_dimensions", 1536
        )

        # Fallback config
        self._fallback_models = config.get("fallback_models", [])
        self._fallback_retries = config.get("fallback_retries_per_model", 2)

        # Batch API config
        self._batch_model = config.get("batch_model") or self._chat_model
        self._batch_poll_interval = config.get("batch_poll_interval_seconds", 30)
        self._batch_max_wait = config.get("batch_max_wait_seconds", 1800)

        # Pipeline trace collector
        self._trace = None
        # Thread-local trace override for parallel phase execution
        self._trace_local = threading.local()

    def set_trace(self, collector):
        """Set the pipeline trace collector. Use clear_trace() when done."""
        self._trace = collector

    def clear_trace(self):
        """Remove the pipeline trace collector."""
        self._trace = None

    _MAX_RETRIES = 5

    # Models that reject explicit temperature (reasoning / GPT-5 family).
    _NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4-mini", "gpt-5")

    def _supports_temperature(self, model: str) -> bool:
        return not any(model.startswith(p) for p in self._NO_TEMPERATURE_PREFIXES)

    @property
    def _active_trace(self):
        """Return thread-local trace if set, else the global trace."""
        return getattr(self._trace_local, "trace", None) or self._trace

    def _rate_limit(self):
        """Simple rate limiter: sleep if calling too fast."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_time = time.monotonic()

    def _call_with_retry(self, fn, *args, **kwargs):
        """Call fn with exponential backoff on 429 RateLimitError."""
        for attempt in range(self._MAX_RETRIES):
            self._rate_limit()
            try:
                return fn(*args, **kwargs)
            except RateLimitError as exc:
                if attempt == self._MAX_RETRIES - 1:
                    raise
                retry_after = getattr(exc, "retry_after", None)
                if retry_after:
                    wait = float(retry_after)
                else:
                    wait = min(2 ** attempt + random.random(), 60)
                logger.warning(
                    "Rate limited (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, self._MAX_RETRIES, wait,
                )
                time.sleep(wait)

    def _call_with_fallback(self, fn, **kwargs):
        """Call fn with model fallback chain on 429 RateLimitError.

        Tries the primary model first, then each fallback model in order.
        Each model gets ``fallback_retries_per_model`` attempts with exponential backoff.
        """
        models = [kwargs.get("model", self._chat_model)] + list(self._fallback_models)

        for model_idx, model in enumerate(models):
            kwargs["model"] = model
            for attempt in range(self._fallback_retries):
                self._rate_limit()
                try:
                    return fn(**kwargs)
                except RateLimitError as exc:
                    is_last_model = model_idx == len(models) - 1
                    is_last_attempt = attempt == self._fallback_retries - 1

                    if is_last_model and is_last_attempt:
                        raise

                    retry_after = getattr(exc, "retry_after", None)
                    if retry_after:
                        wait = float(retry_after)
                    else:
                        wait = min(2 ** attempt + random.random(), 60)

                    if is_last_attempt:
                        logger.warning(
                            "Rate limited on model %s after %d attempts, falling back to %s",
                            model, self._fallback_retries, models[model_idx + 1],
                        )
                        break  # move to next model
                    else:
                        logger.warning(
                            "Rate limited on model %s (attempt %d/%d), retrying in %.1fs",
                            model, attempt + 1, self._fallback_retries, wait,
                        )
                        time.sleep(wait)

    def chat(
        self,
        user_message: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion request."""
        t0 = time.monotonic()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_message})

        kwargs = {
            "model": self._chat_model,
            "messages": messages,
            self._max_tokens_key: max_tokens,
        }
        if self._supports_temperature(self._chat_model):
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._call_with_fallback(
            self._client.chat.completions.create, **kwargs
        )
        choice = response.choices[0]

        result = LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        )

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "llm_chat",
                prompt_tokens=result.usage.get("prompt_tokens", 0),
                completion_tokens=result.usage.get("completion_tokens", 0),
                total_tokens=result.usage.get("total_tokens", 0),
                duration=time.monotonic() - t0,
                model_name=result.model,
            )

        return result

    def chat_messages(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion with a pre-built messages list (for multi-turn)."""
        t0 = time.monotonic()
        kwargs = {
            "model": self._chat_model,
            "messages": messages,
            self._max_tokens_key: max_tokens,
        }
        if self._supports_temperature(self._chat_model):
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._call_with_fallback(
            self._client.chat.completions.create, **kwargs
        )
        choice = response.choices[0]
        result = LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        )

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "llm_chat",
                prompt_tokens=result.usage.get("prompt_tokens", 0),
                completion_tokens=result.usage.get("completion_tokens", 0),
                total_tokens=result.usage.get("total_tokens", 0),
                duration=time.monotonic() - t0,
                model_name=result.model,
            )

        return result

    def embed(self, texts: list[str], on_progress=None) -> list[list[float]]:
        """Generate embeddings for a list of texts, with automatic batching."""
        if not texts:
            return []
        # OpenAI rejects empty strings in input
        texts = [t if t.strip() else " " for t in texts]
        t0 = time.monotonic()
        all_embeddings = []
        total_prompt_tokens = 0
        total_tokens = 0
        total_batches = (len(texts) + self._batch_size - 1) // self._batch_size
        for batch_idx, i in enumerate(range(0, len(texts), self._batch_size)):
            batch = texts[i : i + self._batch_size]

            kwargs = {"model": self._embed_model, "input": batch}
            # OpenAI and Azure OpenAI support dimensions param for text-embedding-3-* models
            if self._embed_dimensions:
                kwargs["dimensions"] = self._embed_dimensions

            response = self._call_with_retry(
                self._embed_client.embeddings.create, **kwargs
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

            if response.usage:
                total_prompt_tokens += getattr(response.usage, "prompt_tokens", 0) or 0
                total_tokens += getattr(response.usage, "total_tokens", 0) or 0

            if on_progress:
                on_progress(len(all_embeddings), len(texts))

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "llm_embed",
                prompt_tokens=total_prompt_tokens,
                total_tokens=total_tokens,
                item_count=len(texts),
                duration=time.monotonic() - t0,
                model_name=self._embed_model,
            )

        return all_embeddings

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string."""
        return self.embed([text])[0]

    def chat_concurrent(
        self,
        prompts: list[str],
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
        max_workers: int | None = None,
        on_progress=None,
    ) -> list[LLMResponse | None]:
        """Run multiple chat completions concurrently using a thread pool.

        Returns a list of LLMResponse (or None on error) in the same order as prompts.
        """
        if not prompts:
            return []

        workers = max_workers or min(len(prompts), max(self._rpm // 10, 2), 12)
        results: list[LLMResponse | None] = [None] * len(prompts)
        done_count = 0

        # Capture the caller's trace so sub-threads inherit it
        caller_trace = self._active_trace

        def _call(idx: int, prompt: str) -> tuple[int, LLMResponse | None]:
            # Propagate the caller's trace to this thread
            if caller_trace is not None:
                self._trace_local.trace = caller_trace
            try:
                return idx, self.chat(prompt, system=system, temperature=temperature,
                                      max_tokens=max_tokens, json_mode=json_mode)
            except (RateLimitError, APIConnectionError, APITimeoutError, ValueError) as exc:
                logger.warning("Concurrent chat call %d failed: %s", idx, exc)
                return idx, None
            finally:
                if caller_trace is not None:
                    self._trace_local.trace = None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_call, i, p) for i, p in enumerate(prompts)]
            for future in as_completed(futures):
                idx, response = future.result()
                results[idx] = response
                done_count += 1
                if on_progress:
                    on_progress(done_count, len(prompts))

        return results

    # ── Batch API ────────────────────────────────────────────────────

    def chat_batch(
        self,
        prompts: list[str],
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
        on_progress=None,
    ) -> list[LLMResponse | None]:
        """Submit prompts via the OpenAI Batch API and poll for results.

        Falls back to ``chat_concurrent()`` on any error.
        """
        if on_progress:
            on_progress(0, len(prompts))
        try:
            results = self._chat_batch_inner(
                prompts, system=system, temperature=temperature,
                max_tokens=max_tokens, json_mode=json_mode,
            )
            if on_progress:
                on_progress(len(prompts), len(prompts))
            return results
        except (RateLimitError, APIConnectionError, APITimeoutError, TimeoutError, OSError) as exc:
            logger.warning("Batch API failed (%s), falling back to chat_concurrent", exc)
            return self.chat_concurrent(
                prompts, system=system, temperature=temperature,
                max_tokens=max_tokens, json_mode=json_mode,
                on_progress=on_progress,
            )

    def _chat_batch_inner(
        self,
        prompts: list[str],
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> list[LLMResponse | None]:
        """Core Batch API logic: build JSONL, upload, create batch, poll, download."""
        t0 = time.monotonic()
        # 1. Build JSONL in memory
        endpoint = "/chat/completions" if self.provider == "azure" else "/v1/chat/completions"
        buf = io.BytesIO()
        for idx, prompt in enumerate(prompts):
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            body = {
                "model": self._batch_model,
                "messages": messages,
                self._max_tokens_key: max_tokens,
            }
            if self._supports_temperature(self._batch_model):
                body["temperature"] = temperature
            if json_mode:
                body["response_format"] = {"type": "json_object"}

            line = {
                "custom_id": f"req-{idx}",
                "method": "POST",
                "url": endpoint,
                "body": body,
            }
            buf.write(json.dumps(line).encode("utf-8"))
            buf.write(b"\n")

        buf.seek(0)

        # 2. Upload file
        file_obj = self._client.files.create(file=("batch_input.jsonl", buf), purpose="batch")
        logger.info("Batch file uploaded: %s", file_obj.id)

        # 3. Create batch
        batch = self._client.batches.create(
            input_file_id=file_obj.id,
            endpoint=endpoint,
            completion_window="24h",
        )
        logger.info("Batch created: %s (%d requests)", batch.id, len(prompts))

        # 4. Poll for completion
        results = self._poll_batch(batch.id, len(prompts))

        _trace = self._active_trace
        if _trace:
            total_prompt = sum(
                r.usage.get("prompt_tokens", 0) for r in results if r
            )
            total_completion = sum(
                r.usage.get("completion_tokens", 0) for r in results if r
            )
            _trace.record_event(
                "llm_chat_batch",
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                total_tokens=total_prompt + total_completion,
                item_count=len(prompts),
                duration=time.monotonic() - t0,
                model_name=self._batch_model,
            )

        return results

    def _poll_batch(self, batch_id: str, num_requests: int) -> list[LLMResponse | None]:
        """Poll a batch until completion, then download results."""
        start = time.monotonic()

        while True:
            elapsed = time.monotonic() - start
            if elapsed > self._batch_max_wait:
                # Cancel the batch and raise so caller falls back
                try:
                    self._client.batches.cancel(batch_id)
                except Exception:
                    pass
                raise TimeoutError(
                    f"Batch {batch_id} did not complete within {self._batch_max_wait}s"
                )

            time.sleep(self._batch_poll_interval)
            batch = self._client.batches.retrieve(batch_id)

            if batch.status == "completed":
                logger.info("Batch %s completed", batch_id)
                if not batch.output_file_id:
                    raise RuntimeError(f"Batch {batch_id} completed but output_file_id is None")
                return self._download_batch_results(batch.output_file_id, num_requests)

            if batch.status in ("failed", "expired", "cancelled"):
                raise RuntimeError(f"Batch {batch_id} ended with status: {batch.status}")

            logger.debug(
                "Batch %s status: %s (%.0fs elapsed)", batch_id, batch.status, elapsed
            )

    def _download_batch_results(
        self, output_file_id: str, num_requests: int
    ) -> list[LLMResponse | None]:
        """Download and parse batch output JSONL into ordered LLMResponse list."""
        content = self._client.files.content(output_file_id)
        raw = content.read()

        results: list[LLMResponse | None] = [None] * num_requests

        for line in raw.decode("utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            custom_id = entry.get("custom_id", "")
            if not custom_id.startswith("req-"):
                continue
            try:
                idx = int(custom_id[4:])
            except (ValueError, IndexError):
                continue

            if idx < 0 or idx >= num_requests:
                continue

            resp_body = entry.get("response", {}).get("body", {})
            choices = resp_body.get("choices", [])
            usage = resp_body.get("usage", {})

            if choices:
                message_content = choices[0].get("message", {}).get("content", "")
                results[idx] = LLMResponse(
                    content=message_content,
                    model=resp_body.get("model", self._batch_model),
                    usage={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                )

        return results

    def chat_batch_or_concurrent(
        self,
        prompts: list[str],
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
        max_workers: int | None = None,
        on_progress=None,
    ) -> list[LLMResponse | None]:
        """Route to Batch API or chat_concurrent based on config.

        Uses Batch API when ``use_batch_api`` is enabled and there are >= 3 prompts.
        Otherwise falls back to ``chat_concurrent``.
        """
        use_batch = settings.ANALYSIS_CONFIG.get("use_batch_api", False)
        if use_batch and len(prompts) >= 10:
            return self.chat_batch(
                prompts, system=system, temperature=temperature,
                max_tokens=max_tokens, json_mode=json_mode,
                on_progress=on_progress,
            )
        return self.chat_concurrent(
            prompts, system=system, temperature=temperature,
            max_tokens=max_tokens, json_mode=json_mode,
            max_workers=max_workers,
            on_progress=on_progress,
        )

    @property
    def embedding_dimensions(self) -> int:
        return self._embed_dimensions


# Module-level singleton (thread-safe)
import threading

_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """Get or create the singleton LLM client."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = LLMClient()
    return _client
