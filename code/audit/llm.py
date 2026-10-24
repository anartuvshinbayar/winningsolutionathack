"""Claude calls with a disk cache and token/cost accounting.

The engine never needs the model to be online: every call is cached by (model, prompt version, input)
in code/cache/llm_cache.json, and callers fall back to rules when no credentials are available.
Credentials come from the environment only (ANTHROPIC_API_KEY, or an `ant auth login` profile).
"""
import hashlib
import json
import os
from collections import defaultdict

from .data import CACHE_DIR

MODEL = os.environ.get("AUDIT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AUDIT_EFFORT", "low")
# USD per million tokens (input, output), Claude API first-party rates.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
CACHE_FILE = CACHE_DIR / "llm_cache.json"


class Usage:
    """Per-model counters for the run that produced output.csv."""

    def __init__(self):
        self.models = defaultdict(lambda: {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0})

    def record(self, model, input_tokens, output_tokens, cached):
        m = self.models[model]
        m["cached" if cached else "calls"] += 1
        m["input_tokens"] += input_tokens
        m["output_tokens"] += output_tokens

    def summary(self):
        out = {}
        for model, m in self.models.items():
            p_in, p_out = PRICES.get(model, (0.0, 0.0))
            out[model] = dict(m, cost_usd=round(m["input_tokens"] * p_in / 1e6 + m["output_tokens"] * p_out / 1e6, 6))
        return out


USAGE = Usage()
_cache = None
_client = None


def _load_cache():
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    return _cache


def save_cache():
    if _cache is not None:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_cache, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def available():
    """True when the SDK is installed and some credential source is configured."""
    if os.environ.get("AUDIT_OFFLINE"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE"))


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def structured(system, user, schema, version):
    """One JSON-schema-constrained call. Returns the parsed dict, or None if the model is unavailable
    or declined. Cache hits are counted as cached calls with their original token usage."""
    cache = _load_cache()
    key = hashlib.sha256(json.dumps([MODEL, version, system, user], ensure_ascii=False).encode()).hexdigest()
    hit = cache.get(key)
    if hit:
        USAGE.record(hit["model"], hit["input_tokens"], hit["output_tokens"], cached=True)
        return hit["result"]
    if not available():
        return None
    import anthropic
    try:
        response = _get_client().beta.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as exc:
        print(f"[llm] call failed, using rules instead: {exc.__class__.__name__}")
        return None
    USAGE.record(MODEL, response.usage.input_tokens, response.usage.output_tokens, cached=False)
    if response.stop_reason == "refusal":
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return None
    result = json.loads(text)
    cache[key] = {"model": MODEL, "result": result, "input_tokens": response.usage.input_tokens,
                  "output_tokens": response.usage.output_tokens}
    return result
