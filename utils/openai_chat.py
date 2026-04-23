"""OpenAI chat helpers with model-family token compatibility handling."""

from __future__ import annotations

from typing import Any


MODEL_PRICING_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
}


def _uses_max_completion_tokens(model: str) -> bool:
    """Return True when the model family expects max_completion_tokens."""
    return model.casefold().startswith("gpt-5")


def _read_nested_value(container: Any, *path: str) -> Any:
    """Read a nested usage value from dict- or attribute-based SDK objects."""
    current = container
    for key in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
            continue
        current = getattr(current, key, None)
    return current


def _coerce_int(value: Any) -> int:
    """Convert usage values into stable integer counters."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_pricing_model(model: str) -> str | None:
    """Resolve a concrete model name to the pricing family used for estimation."""
    normalized_model = str(model).strip().casefold()
    for prefix in sorted(MODEL_PRICING_PER_MILLION_TOKENS, key=len, reverse=True):
        if normalized_model.startswith(prefix):
            return prefix
    return None


def extract_completion_usage(response: Any | None, *, model: str) -> dict[str, Any]:
    """Extract token usage and estimate cost for a chat completion response."""
    usage = _read_nested_value(response, "usage") if response is not None else None
    prompt_tokens = _coerce_int(_read_nested_value(usage, "prompt_tokens"))
    completion_tokens = _coerce_int(_read_nested_value(usage, "completion_tokens"))
    total_tokens = _coerce_int(_read_nested_value(usage, "total_tokens"))
    cached_tokens = _coerce_int(_read_nested_value(usage, "prompt_tokens_details", "cached_tokens"))

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    pricing_model = _resolve_pricing_model(model)
    input_rate = 0.0
    output_rate = 0.0
    estimated_cost_usd = 0.0
    pricing_available = pricing_model is not None
    if pricing_model is not None:
        input_rate = MODEL_PRICING_PER_MILLION_TOKENS[pricing_model]["input"]
        output_rate = MODEL_PRICING_PER_MILLION_TOKENS[pricing_model]["output"]
        estimated_cost_usd = round(
            (prompt_tokens / 1_000_000) * input_rate
            + (completion_tokens / 1_000_000) * output_rate,
            6,
        )

    return {
        "model": str(model).strip(),
        "pricing_model": pricing_model or "",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "input_rate_per_million": input_rate,
        "output_rate_per_million": output_rate,
        "estimated_cost_usd": estimated_cost_usd,
        "pricing_available": pricing_available,
    }


def create_chat_completion(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    token_limit: int,
    temperature: float | None = None,
) -> Any:
    """Create a chat completion while handling token parameter differences."""
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        request_kwargs["temperature"] = temperature

    token_key = "max_completion_tokens" if _uses_max_completion_tokens(model) else "max_tokens"
    request_kwargs[token_key] = token_limit

    try:
        return client.chat.completions.create(**request_kwargs)
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        if "max_tokens" not in error_text and "max_completion_tokens" not in error_text:
            raise

        fallback_kwargs = dict(request_kwargs)
        fallback_kwargs.pop(token_key, None)
        fallback_key = "max_tokens" if token_key == "max_completion_tokens" else "max_completion_tokens"
        fallback_kwargs[fallback_key] = token_limit
        return client.chat.completions.create(**fallback_kwargs)