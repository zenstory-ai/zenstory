"""
Unified LLM client with sync/async/streaming support.

Features:
- Standard completion (sync and async)
- Streaming completion for SSE
- Structured output with JSON Schema
- Error handling and retry logic
"""

import json
from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

from agent.core.deepseek_client import (
    DEEPSEEK_CHAT_MODEL,
    DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S,
    DEEPSEEK_CLIENT_MAX_RETRIES,
    DEEPSEEK_CLIENT_TIMEOUT_S,
    get_deepseek_base_url,
)
from config.datetime_utils import utcnow
from utils.logger import get_logger, log_with_context

if TYPE_CHECKING:
    from services.usage.llm_usage_service import LLMUsageAttribution

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)



class LLMClient:
    """
    Unified LLM client for all agent operations.

    Supports:
    - Sync/async completion
    - Streaming output
    - Structured output with Pydantic models
    """

    # Model aliases — intentionally fixed for DeepSeek-only operation.
    MODEL_FAST = DEEPSEEK_CHAT_MODEL
    MODEL_QUALITY = DEEPSEEK_CHAT_MODEL

    # Default inference parameters
    DEFAULT_TEMPERATURE = 1.0
    DEFAULT_TOP_P = 0.95

    @classmethod
    def _resolve_model(cls, model: str | None = None) -> str:
        """Return the only supported model or reject legacy multi-model overrides."""
        requested = (model or cls.MODEL_QUALITY).strip()
        if requested == DEEPSEEK_CHAT_MODEL:
            return DEEPSEEK_CHAT_MODEL
        raise ValueError(
            f"Unsupported LLM model {requested!r}; "
            f"ZenStory only supports {DEEPSEEK_CHAT_MODEL!r}"
        )

    def __init__(self):
        """Initialize from DeepSeek runtime environment variables only."""
        import os

        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.base_url = get_deepseek_base_url()
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")

        self._sync_client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None

        log_with_context(
            logger,
            20,  # INFO
            "LLMClient initialized",
            base_url=self.base_url,
            model_fast=self.MODEL_FAST,
            model_quality=self.MODEL_QUALITY,
        )

    @property
    def sync_client(self) -> OpenAI:
        """Lazy-load sync client."""
        if self._sync_client is None:
            self._sync_client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    DEEPSEEK_CLIENT_TIMEOUT_S,
                    connect=DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S,
                ),
                max_retries=DEEPSEEK_CLIENT_MAX_RETRIES,
            )
        return self._sync_client

    @property
    def async_client(self) -> AsyncOpenAI:
        """Lazy-load async client."""
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    DEEPSEEK_CLIENT_TIMEOUT_S,
                    connect=DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S,
                ),
                max_retries=DEEPSEEK_CLIENT_MAX_RETRIES,
            )
        return self._async_client

    # ========== Sync Methods ==========

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int = 2000,
    ) -> str:
        """
        Standard synchronous completion.

        Args:
            messages: Chat messages
            model: Model to use (default: MODEL_QUALITY)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            Generated text
        """
        start_time = utcnow()
        model_name = self._resolve_model(model)

        log_with_context(
            logger,
            20,  # INFO
            "LLM sync completion started",
            model=model_name,
            message_count=len(messages),
            max_tokens=max_tokens,
        )

        try:
            response = self.sync_client.chat.completions.create(
                model=model_name,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature if temperature is not None else self.DEFAULT_TEMPERATURE,
                top_p=top_p if top_p is not None else self.DEFAULT_TOP_P,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content or ""
            duration_ms = int((utcnow() - start_time).total_seconds() * 1000)

            # Extract token usage if available
            usage = response.usage
            if usage:
                log_with_context(
                    logger,
                    20,  # INFO
                    "LLM sync completion completed",
                    model=model_name,
                    duration_ms=duration_ms,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    response_length=len(content),
                )
            else:
                log_with_context(
                    logger,
                    20,  # INFO
                    "LLM sync completion completed",
                    model=model_name,
                    duration_ms=duration_ms,
                    response_length=len(content),
                )

            return content
        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "LLM sync completion failed",
                model=model_name,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=int((utcnow() - start_time).total_seconds() * 1000),
            )
            raise

    @staticmethod
    def _chunk_text(chunk: Any) -> str:
        """取出一个流式 chunk 的正文增量。

        开启 stream_options.include_usage 后，DeepSeek 会在最后补一个
        ``choices == []`` 的纯 usage chunk；直接 ``chunk.choices[0]`` 会 IndexError。
        """
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        return getattr(delta, "content", None) or ""

    @staticmethod
    def _collect_usage(chunk: Any, usage_sink: dict[str, Any] | None) -> None:
        """把 chunk 上的 usage 写进调用方提供的容器。

        流式封装原先只 yield 正文增量，usage 被整个丢弃——走这条路的调用方
        （如 suggest_service）其 token 消耗完全不进任何统计。用 sink 而不是改
        yield 的类型，是为了不破坏既有 ``Generator[str, ...]`` 契约。
        """
        if usage_sink is None:
            return
        usage = getattr(chunk, "usage", None)
        if usage is None:
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if isinstance(value, int) and not isinstance(value, bool):
                usage_sink[key] = value

    def complete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        usage_sink: dict[str, Any] | None = None,
    ) -> Generator[str, None, None]:
        """
        Streaming synchronous completion.

        Args:
            messages: Chat messages
            model: Model to use
            temperature: Sampling temperature
            usage_sink: 可选容器；流结束时会被填入 prompt/completion/total tokens

        Yields:
            Text chunks as they are generated
        """
        stream = self.sync_client.chat.completions.create(
            model=self._resolve_model(model),
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature if temperature is not None else self.DEFAULT_TEMPERATURE,
            top_p=top_p if top_p is not None else self.DEFAULT_TOP_P,
            stream=True,
            # 不带 stream_options 时 DeepSeek 全程不回发 usage，统计必然为空
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            self._collect_usage(chunk, usage_sink)
            text = self._chunk_text(chunk)
            if text:
                yield text

    def complete_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        model: str | None = None,
    ) -> T:
        """
        Structured output using JSON Schema.

        Args:
            messages: Chat messages
            response_model: Pydantic model for response
            model: Model to use

        Returns:
            Validated Pydantic model instance
        """
        response = self.sync_client.chat.completions.create(
            model=self._resolve_model(model),
            messages=self._structured_messages(messages, response_model),  # type: ignore[arg-type]
            response_format={"type": "json_object"},
            temperature=0.0,
        )  # type: ignore[call-overload]
        data = json.loads(response.choices[0].message.content or "{}")
        return response_model.model_validate(data)

    # ========== Async Methods ==========

    async def acomplete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int = 2000,
        thinking_enabled: bool = True,
        usage_attribution: "LLMUsageAttribution | None" = None,
    ) -> str:
        """
        Async completion.

        Args:
            messages: Chat messages
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            thinking_enabled: Whether the model may reason before answering.
                             deepseek-flash reasons by default and reasoning tokens
                             count against ``max_tokens``. False sends
                             ``thinking={"type": "disabled"}`` (verified against the
                             live API on 2026-10-03), so short-budget calls spend the
                             whole budget on the answer. True sends nothing and the
                             model uses its default effort.
            usage_attribution: Who to bill; when given, the call's tokens are
                             written to the usage ledger.

        Returns:
            Generated text
        """
        start_time = utcnow()
        model_name = self._resolve_model(model)

        log_with_context(
            logger,
            20,  # INFO
            "LLM async completion started",
            model=model_name,
            message_count=len(messages),
            max_tokens=max_tokens,
            thinking_enabled=thinking_enabled,
        )

        # deepseek-flash 默认先推理，reasoning token 计入 max_tokens；小预算调用
        # （如输入建议的 150 token）会被推理吃光、正文为空。调用方声明不需要推理时
        # 显式关闭。
        extra_body: dict[str, object] = {}
        if not thinking_enabled:
            extra_body["thinking"] = {"type": "disabled"}

        try:
            response = await self.async_client.chat.completions.create(
                model=model_name,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature if temperature is not None else self.DEFAULT_TEMPERATURE,
                top_p=top_p if top_p is not None else self.DEFAULT_TOP_P,
                max_tokens=max_tokens,
                extra_body=extra_body if extra_body else None,
            )

            content = response.choices[0].message.content or ""
            duration_ms = int((utcnow() - start_time).total_seconds() * 1000)

            # Extract token usage if available
            usage = response.usage
            if usage_attribution is not None:
                from services.usage.llm_usage_service import record_llm_usage_async

                await record_llm_usage_async(usage_attribution, model=model_name, usage=usage)
            if usage:
                log_with_context(
                    logger,
                    20,  # INFO
                    "LLM async completion completed",
                    model=model_name,
                    duration_ms=duration_ms,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    response_length=len(content),
                )
            else:
                log_with_context(
                    logger,
                    20,  # INFO
                    "LLM async completion completed",
                    model=model_name,
                    duration_ms=duration_ms,
                    response_length=len(content),
                )

            return content
        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "LLM async completion failed",
                model=model_name,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=int((utcnow() - start_time).total_seconds() * 1000),
            )
            raise

    async def acomplete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        usage_sink: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Async streaming completion for SSE.

        Args:
            messages: Chat messages
            model: Model to use
            temperature: Sampling temperature
            usage_sink: 可选容器；流结束时会被填入 prompt/completion/total tokens

        Yields:
            Text chunks as they are generated
        """
        stream = await self.async_client.chat.completions.create(
            model=self._resolve_model(model),
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature if temperature is not None else self.DEFAULT_TEMPERATURE,
            top_p=top_p if top_p is not None else self.DEFAULT_TOP_P,
            stream=True,
            # 不带 stream_options 时 DeepSeek 全程不回发 usage，统计必然为空
            stream_options={"include_usage": True},
        )
        async for chunk in stream:  # type: ignore[union-attr]
            self._collect_usage(chunk, usage_sink)
            text = self._chunk_text(chunk)
            if text:
                yield text

    async def acomplete_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        model: str | None = None,
    ) -> T:
        """
        Async structured output.

        Args:
            messages: Chat messages
            response_model: Pydantic model for response
            model: Model to use

        Returns:
            Validated Pydantic model instance
        """
        response = await self.async_client.chat.completions.create(
            model=self._resolve_model(model),
            messages=self._structured_messages(messages, response_model),  # type: ignore[arg-type]
            response_format={"type": "json_object"},
            temperature=0.0,
        )  # type: ignore[call-overload]
        data = json.loads(response.choices[0].message.content or "{}")
        return response_model.model_validate(data)

    # ========== Helpers ==========

    @staticmethod
    def _structured_messages(
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
    ) -> list[dict[str, str]]:
        """Steer structured output via DeepSeek's json_object mode.

        DeepSeek's OpenAI-compatible endpoint rejects
        ``response_format={"type": "json_schema"}`` with HTTP 400
        ("This response_format type is unavailable now"), but supports
        ``{"type": "json_object"}``. We therefore prepend the target JSON schema as a
        system instruction and let the caller request a plain JSON object. The literal
        word "json" in the instruction also satisfies the API's json_object requirement.
        """
        schema = response_model.model_json_schema()
        instruction = (
            "Respond with a single valid JSON object that conforms to the following "
            "JSON schema. Output only the JSON object — no markdown fences, no commentary.\n"
            f"JSON schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        return [{"role": "system", "content": instruction}, *messages]


# Singleton instance
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get or create the singleton LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
