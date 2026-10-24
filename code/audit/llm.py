"""Model calls for request intent: Claude or Gemini, with a disk cache and token/cost accounting.

Which model runs is decided by the environment (or an uncommitted .env at the repo root, which never
overrides the environment):
  ANTHROPIC_API_KEY set and the `anthropic` package installed -> Claude (AUDIT_MODEL, default claude-opus-5-5)
  otherwise GEMINI_API_KEY set -> Gemini (GEMINI_MODEL, default gemini-3.5-flash-lite) over the plain REST API
  otherwise, or AUDIT_OFFLINE set -> no model; callers fall back to rules

Calls are batched: one call answers up to 20 inputs against a JSON schema. Every answer is cached per input
in code/cache/llm_cache.json together with the usage of the call that produced it, so a re-run spends
nothing and still reports the tokens behind every answer it used. The Gemini path is stdlib only (urllib).
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

from .data import CACHE_DIR, REPO_ROOT


def _load_dotenv():
    """Read KEY=VALUE lines from an uncommitted .env at the repo root (never overrides the environment)."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

ANTHROPIC_MODEL = os.environ.get("AUDIT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AUDIT_EFFORT", "low")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")  # intent; gemini-3.8-flash is for screenshots later
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_THINKING_LEVEL = "low"   # Gemini 3 thinking level (low / medium / high); thinking is billed as output
MAX_ATTEMPTS = 6                # per call, for 429, transient 5xx answers and timeouts
TIMEOUT = 60                    # seconds per HTTP request

# USD per million tokens (input, output including thinking). Anthropic first-party list prices; Gemini API
# paid-tier standard list prices (ai.google.dev/gemini-api/docs/pricing, checked 2026-10-25). Google lists
# gemini-3.8-flash (kept for the planned screenshot reading) at these rates through 2026-12-31 and at
# $1.50 / $7.50 from 2027-01-01.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50),
    "gemini-3.5-flash-lite": (0.30, 2.50), "gemini-3.8-flash": (0.75, 3.75),
}
FREE_TIER = {"gemini-3.5-flash-lite", "gemini-3.8-flash"}  # the Gemini API free tier bills these models at $0
CACHE_FILE = CACHE_DIR / "llm_cache.json"


class Usage:
    """Per-model counters for the run that produced output.csv."""

    def __init__(self):
        self.models = defaultdict(lambda: {"provider": "", "calls": 0, "cached": 0, "input_tokens": 0,
                                           "output_tokens": 0, "thinking_tokens": 0})

    def record(self, provider, model, usage, cached):
        m = self.models[model]
        m["provider"] = provider
        m["cached" if cached else "calls"] += 1
        m["input_tokens"] += usage.get("input_tokens", 0)
        m["output_tokens"] += usage.get("output_tokens", 0)
        m["thinking_tokens"] += usage.get("thinking_tokens", 0)

    def summary(self):
        out = {}
        for model, m in self.models.items():
            p_in, p_out = PRICES.get(model, (0.0, 0.0))
            cost = m["input_tokens"] * p_in / 1e6 + (m["output_tokens"] + m["thinking_tokens"]) * p_out / 1e6
            out[model] = dict(m, cost_usd=round(cost, 6))
        return out


USAGE = Usage()
_cache = None
_dirty = False
_gave_up = None   # reason the model was abandoned for the rest of the run (quota, bad key, network)
_client = None
_notes = set()


def _note(text):
    if text not in _notes:
        _notes.add(text)
        print(f"[llm] {text}")


# -- provider choice -----------------------------------------------------------------------------

def _anthropic_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE"))


def provider():
    """'anthropic', 'gemini' or None, from the configured credentials."""
    if os.environ.get("AUDIT_OFFLINE"):
        return None
    if _anthropic_configured():
        try:
            import anthropic  # noqa: F401
            return "anthropic"
        except ImportError:
            _note("Anthropic credentials found but the anthropic package is not installed; trying Gemini")
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def model_name():
    return {"anthropic": ANTHROPIC_MODEL, "gemini": GEMINI_MODEL}.get(provider())


def available():
    """True while a model is configured and has not been abandoned during this run."""
    return provider() is not None and _gave_up is None


# -- cache ---------------------------------------------------------------------------------------

def _load_cache():
    global _cache
    if _cache is None:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        _cache = {"items": data.get("items", {}), "calls": data.get("calls", {})}
    return _cache


def save_cache():
    if _cache is not None and _dirty:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_cache, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


# -- batched structured calls ----------------------------------------------------------------------

def structured_batch(system, items, item_schema, version, batch_size=20):
    """Answer many inputs with few calls.

    items: {key: text}. Each call sends up to batch_size texts as a JSON list with short ids and must return
    {"results": [{"id": ..., <item_schema fields>}]}. Returns {key: result} for every item answered now or
    earlier (cache); items the model could not answer are left out so the caller can fall back."""
    global _dirty
    cache = _load_cache()
    model, prov = model_name(), provider()
    # Without a key, answers cached by an earlier run with either default model are still used, so an
    # offline run reproduces that run's verdicts.
    lookup = [model] if model else [ANTHROPIC_MODEL, GEMINI_MODEL]
    out, used_calls, pending = {}, set(), []
    for key, text in items.items():
        hit = next((cache["items"][k] for k in (_digest(m, version, system, text) for m in lookup)
                    if k in cache["items"]), None)
        if hit:
            out[key] = hit["result"]
            used_calls.add(hit["call"])
        else:
            pending.append((key, text))
    for call_id in sorted(used_calls):
        call = cache["calls"][call_id]
        USAGE.record(call["provider"], call["model"], call, cached=True)
    schema = _batch_schema(item_schema)
    for start in range(0, len(pending), batch_size):
        if not available():
            break
        chunk = pending[start:start + batch_size]
        ids = {f"r{i + 1}": (key, text) for i, (key, text) in enumerate(chunk)}
        user = ("Answer every request below exactly once, using its id.\n\n"
                + json.dumps([{"id": i, "text": t} for i, (_k, t) in ids.items()], ensure_ascii=False, indent=1))
        result, usage = _call(prov, system, user, schema)
        if usage:
            USAGE.record(prov, model, usage, cached=False)
        answers = {}
        for row in (result or {}).get("results", []):
            if isinstance(row, dict) and row.get("id") in ids and row["id"] not in answers:
                answers[row["id"]] = {k: v for k, v in row.items() if k != "id"}
        calls = -(-len(pending) // batch_size)
        print(f"[llm] {model} call {start // batch_size + 1}/{calls}: {len(answers)} of {len(chunk)} answered")
        if not answers:
            continue
        call_id = _digest(model, version, system, [t for _k, t in chunk])[:24]
        cache["calls"][call_id] = dict(usage, provider=prov, model=model, size=len(chunk))
        for short_id, (key, text) in ids.items():
            if short_id in answers:
                out[key] = answers[short_id]
                cache["items"][_digest(model, version, system, text)] = {"result": answers[short_id], "call": call_id}
        _dirty = True
    return out


def _batch_schema(item_schema):
    row = dict(item_schema, properties=dict({"id": {"type": "string"}}, **item_schema["properties"]),
               required=["id"] + list(item_schema.get("required", [])))
    return {"type": "object", "properties": {"results": {"type": "array", "items": row}},
            "required": ["results"], "additionalProperties": False}


def _call(prov, system, user, schema):
    """One call. Returns (parsed JSON or None, usage dict or None)."""
    if prov == "anthropic":
        return _call_anthropic(system, user, schema)
    if prov == "gemini":
        return _call_gemini(system, user, schema)
    return None, None


def _give_up(reason):
    global _gave_up
    _gave_up = reason
    _note(f"model abandoned for this run, rules decide the rest: {reason}")


# -- Anthropic -----------------------------------------------------------------------------------

def _call_anthropic(system, user, schema):
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic(max_retries=MAX_ATTEMPTS)
    try:
        response = _client.beta.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as exc:
        _give_up(f"Anthropic call failed ({exc.__class__.__name__})")
        return None, None
    # Anthropic counts thinking inside output_tokens.
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
             "thinking_tokens": 0}
    if response.stop_reason == "refusal":
        return None, usage
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        return (json.loads(text) if text else None), usage
    except json.JSONDecodeError:
        return None, usage


# -- Gemini (REST, stdlib only) ------------------------------------------------------------------------

def gemini_schema(schema):
    """JSON Schema -> the OpenAPI subset Gemini's responseSchema accepts (upper-case types, no
    additionalProperties)."""
    out = {}
    for key, value in schema.items():
        if key == "type":
            out["type"] = value.upper()
        elif key == "properties":
            out["properties"] = {name: gemini_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out["items"] = gemini_schema(value)
        elif key in ("required", "enum", "description", "format", "nullable"):
            out[key] = value
    return out


def _call_gemini(system, user, schema):
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        # No temperature: Google recommends the default 1.0 for Gemini 3 (lower values can loop or degrade).
        # Reruns are reproducible through the cache instead. thinkingLevel replaces the older thinkingBudget;
        # sending both is a 400.
        "generationConfig": {
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema(schema),
            "thinkingConfig": {"thinkingLevel": GEMINI_THINKING_LEVEL},
        },
    }
    data = _post_gemini(body)
    if data is None:
        return None, None
    meta = data.get("usageMetadata") or {}
    usage = {"input_tokens": meta.get("promptTokenCount", 0), "output_tokens": meta.get("candidatesTokenCount", 0),
             "thinking_tokens": meta.get("thoughtsTokenCount", 0)}
    candidate = (data.get("candidates") or [{}])[0]
    if candidate.get("finishReason", "STOP") != "STOP":
        _note(f"Gemini stopped with {candidate.get('finishReason')}; those requests fall back to rules")
        return None, usage
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        return json.loads(text), usage
    except json.JSONDecodeError:
        _note("Gemini returned text that is not JSON; those requests fall back to rules")
        return None, usage


def _post_gemini(body):
    """POST with retries on 429 and transient 5xx. Returns the response JSON, or None after giving up."""
    url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            message = _error_message(detail)
            if exc.code == 429 and "PerDay" in detail:
                _give_up(f"Gemini daily quota exhausted ({message})")
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS - 1:
                wait = _retry_delay(exc, detail, attempt)
                print(f"[llm] Gemini HTTP {exc.code}, retry {attempt + 1}/{MAX_ATTEMPTS - 1} in {wait:.0f}s")
                time.sleep(wait)
                continue
            _give_up(f"Gemini HTTP {exc.code}: {message}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
                if attempt < MAX_ATTEMPTS - 1:
                    wait = min(2.0 * 2 ** attempt, 60.0)
                    print(f"[llm] Gemini timed out after {TIMEOUT}s, retry {attempt + 1}/{MAX_ATTEMPTS - 1} in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                _give_up(f"Gemini timed out {MAX_ATTEMPTS} times")
                return None
            _give_up(f"Gemini unreachable ({exc.__class__.__name__})")
            return None
    return None


def _retry_delay(exc, detail, attempt):
    """Seconds to wait: the server's RetryInfo or Retry-After when given, else exponential backoff."""
    match = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', detail)
    if match:
        return min(float(match.group(1)) + 1, 90.0)
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header and header.strip().isdigit():
        return min(float(header), 90.0)
    return min(2.0 * 2 ** attempt, 60.0)


def _error_message(detail):
    try:
        return json.loads(detail)["error"]["message"][:160]
    except (ValueError, KeyError, TypeError):
        return detail[:160]
