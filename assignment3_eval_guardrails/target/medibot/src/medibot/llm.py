"""Thin LLM wrapper: plain text and schema-constrained JSON calls.

Providers (``LLM_PROVIDER``): ``anthropic`` (default) or ``groq``. Callers only use
``complete()`` / ``structured()`` and never see the provider.

Anthropic notes:
* Model thinking is always on for claude-opus-5-5, so depth is controlled with
  ``output_config.effort`` (never ``thinking``/``temperature``).
* Server-side refusal fallback is enabled by default (beta), configurable via settings.
* ``stop_reason`` is checked before content is read.
"""

import json
import logging
from functools import lru_cache

import anthropic

from medibot.config import get_settings

log = logging.getLogger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The model call failed or returned an unusable response."""


class LLMRefusal(LLMError):
    """The model declined to answer (safety classifier)."""


@lru_cache
def get_client() -> anthropic.Anthropic:
    s = get_settings()
    return anthropic.Anthropic(api_key=s.anthropic_api_key or None, max_retries=3, timeout=120.0)


def _create(client: anthropic.Anthropic, use_fallbacks: bool, **kwargs):
    if use_fallbacks:
        return client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
    return client.messages.create(**kwargs)


@lru_cache
def get_groq_client():
    from groq import Groq

    return Groq(api_key=get_settings().groq_api_key or None, max_retries=3, timeout=120.0)


def _call_groq(system: str, user: str, effort: str, max_tokens: int, schema: dict | None) -> str:
    import groq

    s = get_settings()
    if not s.groq_api_key:
        raise LLMError("No Groq credentials found - set GROQ_API_KEY in .env.")
    kwargs: dict = dict(
        model=s.groq_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        reasoning_effort=effort,
        # reasoning tokens count against this budget, so leave headroom
        max_completion_tokens=max(max_tokens, 2048),
    )
    if schema is not None:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
    try:
        resp = get_groq_client().chat.completions.create(**kwargs)
    except groq.AuthenticationError as e:
        raise LLMError("Groq authentication failed - check GROQ_API_KEY.") from e
    except groq.RateLimitError as e:
        raise LLMError("Groq rate limit reached; please retry shortly.") from e
    except groq.APIConnectionError as e:
        raise LLMError("Could not reach the Groq API.") from e
    except groq.APIStatusError as e:
        raise LLMError(f"Groq API error {e.status_code}: {e.message}") from e
    choice = resp.choices[0]
    text = (choice.message.content or "").strip()
    if choice.finish_reason == "length" and schema is not None:
        raise LLMError("Model output was truncated before the JSON completed.")
    if not text:
        raise LLMError("Model returned no text.")
    return text


def _call(system: str, user: str, effort: str, max_tokens: int, schema: dict | None = None) -> str:
    s = get_settings()
    if s.llm_provider == "groq":
        return _call_groq(system, user, effort, max_tokens, schema)
    if s.llm_provider != "anthropic":
        raise LLMError(f"Unknown LLM_PROVIDER {s.llm_provider!r} (use 'anthropic' or 'groq').")
    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs = dict(
        model=s.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config=output_config,
    )
    client = get_client()
    try:
        try:
            resp = _create(client, s.use_fallbacks, **kwargs)
        except anthropic.BadRequestError as e:
            # Fallback beta not accepted for this account/platform: retry once without it.
            if s.use_fallbacks and "fallback" in str(e).lower():
                log.warning("fallbacks rejected (%s); retrying without", e)
                resp = _create(client, False, **kwargs)
            else:
                raise
    except TypeError as e:
        # The SDK raises a bare TypeError when no credential can be resolved at all.
        if "authentication method" in str(e):
            raise LLMError("No Anthropic credentials found - set ANTHROPIC_API_KEY in .env.") from e
        raise
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic authentication failed - check ANTHROPIC_API_KEY.") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e

    if resp.stop_reason == "refusal":
        raise LLMRefusal("The model declined to answer this request.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if resp.stop_reason == "max_tokens" and schema is not None:
        raise LLMError("Model output was truncated before the JSON completed.")
    if not text:
        raise LLMError("Model returned no text.")
    return text


def complete(system: str, user: str, effort: str = "medium", max_tokens: int = 4096) -> str:
    return _call(system, user, effort, max_tokens)


def structured(system: str, user: str, schema: dict, effort: str = "low", max_tokens: int = 2048) -> dict:
    text = _call(system, user, effort, max_tokens, schema=schema)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model returned invalid JSON: {text[:200]}") from e
