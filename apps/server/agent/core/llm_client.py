"""Single-shot async chat completions for suggestions and natural polish.

Calls go through ``agent/core/llm_failover.py``: the primary provider, or the
backup when the primary is unavailable.
"""

from typing import TYPE_CHECKING, Any

from agent.core import llm_failover
from config.datetime_utils import utcnow
from utils.logger import get_logger, log_with_context

if TYPE_CHECKING:
    from services.usage.llm_usage_service import LLMUsageAttribution

logger = get_logger(__name__)


class LLMClient:
    """Async completion with provider failover and usage metering."""

    DEFAULT_TEMPERATURE = 1.0
    DEFAULT_TOP_P = 0.95

    async def acomplete(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int = 2000,
        thinking_enabled: bool = True,
        usage_attribution: "LLMUsageAttribution | None" = None,
        served_model_sink: dict[str, Any] | None = None,
    ) -> str:
        """
        Async completion.

        Args:
            messages: Chat messages
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            thinking_enabled: Whether the model may reason before answering.
                             Reasoning tokens count against ``max_tokens``. False
                             prefers a provider that can turn reasoning off
                             (DeepSeek: ``thinking={"type": "disabled"}``, verified
                             against the live API on 2026-10-03), so short-budget
                             calls spend the whole budget on the answer; a provider
                             that cannot (MiniMax) gets the lowest effort and extra
                             token headroom. True sends the provider's default.
            usage_attribution: Who to bill; when given, the call's tokens are
                             written to the usage ledger.
            served_model_sink: Optional dict; ``"model"`` is set to the model
                             that served the call.

        Returns:
            Generated text
        """
        start_time = utcnow()

        log_with_context(
            logger,
            20,  # INFO
            "LLM async completion started",
            message_count=len(messages),
            max_tokens=max_tokens,
            thinking_enabled=thinking_enabled,
        )

        try:
            from services.usage.cost_budget import budget_attribution
            with budget_attribution(usage_attribution):
                # With an attribution every attempt is written to the usage ledger there.
                response, provider = await llm_failover.acreate_chat_completion(
                    call_site=getattr(usage_attribution, "source", None) or "llm_client",
                    usage_attribution=usage_attribution,
                    thinking_enabled=thinking_enabled,
                    messages=messages,
                    temperature=temperature if temperature is not None else self.DEFAULT_TEMPERATURE,
                    top_p=top_p if top_p is not None else self.DEFAULT_TOP_P,
                    max_tokens=max_tokens,
                )
        except Exception as e:
            status_code = getattr(e, "status_code", None)
            log_with_context(
                logger,
                40,  # ERROR
                "LLM async completion failed",
                exc_info=False,
                error_type=type(e).__name__,
                failure_category="provider_request_failed",
                status_code=status_code if isinstance(status_code, int) else None,
                duration_ms=int((utcnow() - start_time).total_seconds() * 1000),
            )
            raise

        content = response.choices[0].message.content or ""
        duration_ms = int((utcnow() - start_time).total_seconds() * 1000)
        if served_model_sink is not None:
            served_model_sink["model"] = provider.model

        usage = response.usage
        log_with_context(
            logger,
            20,  # INFO
            "LLM async completion completed",
            model=provider.model,
            provider=provider.name,
            duration_ms=duration_ms,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            response_length=len(content),
        )
        return content


# Singleton instance
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get or create the singleton LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
