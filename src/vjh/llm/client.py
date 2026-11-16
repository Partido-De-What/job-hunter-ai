"""Claude API client for evaluations. No tools, no files: the model only returns JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models.settings import LLMSettings, ModelPrice


@dataclass
class LLMResult:
    text: str
    stop_reason: str | None
    model: str                      # model that actually served the request
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    refusal_category: str | None = None


class LLM(Protocol):
    def complete_json(self, *, system: str, user: str, schema: dict, model: str) -> LLMResult: ...


def estimate_cost(r: LLMResult, price: ModelPrice | None) -> float:
    if price is None:
        return 0.0
    return round((r.input_tokens * price.input + r.cache_write_tokens * price.input * 1.25
                  + r.cache_read_tokens * price.cache_read + r.output_tokens * price.output) / 1_000_000, 6)


class AnthropicLLM:
    """Real client. Credentials come from ANTHROPIC_API_KEY (in .env) or an `ant auth` profile."""

    def __init__(self, settings: LLMSettings):
        import anthropic

        self.settings = settings
        self.client = anthropic.Anthropic(max_retries=settings.max_retries)

    def complete_json(self, *, system: str, user: str, schema: dict, model: str) -> LLMResult:
        kwargs: dict = dict(
            model=model,
            max_tokens=self.settings.max_output_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.settings.effort,
                           "format": {"type": "json_schema", "schema": schema}},
        )
        if self.settings.refusal_fallback:
            resp = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        u = resp.usage
        details = getattr(resp, "stop_details", None)
        return LLMResult(
            text=text,
            stop_reason=resp.stop_reason,
            model=resp.model,
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            refusal_category=getattr(details, "category", None) if details else None,
        )
