"""Screenshots and PDFs.

Gemini (llm.GEMINI_VISION_MODEL) reads each file once and lists every visible record as structured JSON:
system, resource id, category, identifiers, state, timestamps. code/tools/extract_media.py stores that
answer with its token usage in code/cache/media/<file_id>.json; runs only read the cache, so they stay
offline. Each artifact is a business screen focused on one record. We find that record through the data
catalog's LINK_RESOURCE_IDENTITY activity (or, in a PDF, any resource id it prints) and take its state from
what kind of screen it is. The model's own state for the record is a cross-check (extract_media.py prints it).
"""
import json
import re
from functools import lru_cache

from . import llm
from .data import CACHE_DIR, read_csv

PROMPT_VERSION = "media-v1"
USE = "screenshots and PDFs"  # label of these calls in the usage report

CATEGORIES = ["USER_PROFILE", "SEARCH_INDEX_ENTRY", "ANALYTICS_PROFILE", "SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT",
              "USER_EXPORT", "DATA_FLOW", "BACKUP_ARCHIVE", "SECURITY_AUDIT_LOG", "ACCOUNT_TOMBSTONE",
              "LEGAL_HOLD_RECORD", "MARKETING_PROFILE", "ORDER_OR_FINANCE_RECORD", "OTHER"]
STATES = ["PRESENT", "IDENTIFIABLE", "REDACTED", "ANONYMIZED", "ABSENT", "ACTIVE", "DISABLED", "BLOCKED"]

SYSTEM = """You read screenshots and PDF exports from an online shop's internal tools for a privacy audit.
List every record the file shows: customer profiles, search documents, analytics profiles, support tickets
and attachments, exports, connectors, backups, audit events, retained references, legal or finance entries,
marketing profiles, stored files.
For each record give:
- system: the tool or service it lives in, in lower_snake_case (for example account_service, search_service)
- resource_id: the record's id exactly as printed (forms like account_1234, search_56, audit_record_789,
  tombstone_12, data_flow_345, or a file name), or "" when none is printed
- category and state from the allowed values
- identifiers: email, phone, name, user_id (forms like user_12) and email_hash exactly as printed, "" when
  not shown
- other_references: order ids, ticket ids, file names and other references printed with the record
- timestamps: dates and times printed with the record, as printed
States: PRESENT the record exists; IDENTIFIABLE an analytics profile, support message or log that still shows
a person's name, email or phone; REDACTED the record exists with personal fields removed or masked;
ANONYMIZED statistics no longer linked to a person; ABSENT the file says the record was deleted or not found;
ACTIVE a connector that is running or a backup that can still be restored; DISABLED a connector that is off;
BLOCKED a backup whose restore is blocked.
Copy every id, email, phone number and name character for character. Never fill in a value that is not
visible. Reply with the JSON object only."""

USER = "List every record in this file."

SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string"},
        "records": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "system": {"type": "string"},
                "resource_id": {"type": "string"},
                "category": {"type": "string", "enum": CATEGORIES},
                "state": {"type": "string", "enum": STATES},
                "identifiers": {"type": "object", "properties": {
                    "email": {"type": "string"}, "phone": {"type": "string"}, "name": {"type": "string"},
                    "user_id": {"type": "string"}, "email_hash": {"type": "string"}},
                    "required": ["email", "phone", "name", "user_id", "email_hash"]},
                "other_references": {"type": "array", "items": {"type": "string"}},
                "timestamps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["system", "resource_id", "category", "state", "identifiers", "other_references",
                         "timestamps"],
        }},
    },
    "required": ["screen", "records"],
    "additionalProperties": False,
}

# origin (from the manifest) -> state the screen shows for its focus record
SCREEN_STATE = {
    "account_admin_portal": ("PRESENT", "USER_PROFILE"),            # live customer profile page
    "search_index_admin": ("PRESENT", "SEARCH_INDEX_ENTRY"),        # raw search document
    "integration_admin": ("ACTIVE", "DATA_FLOW"),                   # connector listed as running
    "analytics_platform": ("IDENTIFIABLE", "ANALYTICS_PROFILE"),    # profile shown with name/email
    "export_service": ("PRESENT", "USER_EXPORT"),                   # export ready for download
    "support_platform": ("IDENTIFIABLE", "SUPPORT_MESSAGE"),        # ticket thread with contact details
    "backup_service": ("ACTIVE", "BACKUP_ARCHIVE"),                 # archive restorable from console
    "security_service": ("PRESENT", "SECURITY_AUDIT_LOG"),          # signed audit event
    "identity_service": ("REDACTED", "ACCOUNT_TOMBSTONE"),          # retained reference, no personal fields
    "object_storage": ("PRESENT", "SUPPORT_ATTACHMENT"),            # uploaded file still stored
    "marketing_platform": ("PRESENT", "MARKETING_PROFILE"),         # live audience profile
    "account_history_service": ("PRESENT", "USER_EXPORT"),          # generated customer statement
}

CATALOG_RE = re.compile(r"linked (\S+) \(([^)]+)\) to (\S+) and (user_\d+)|"
                        r"confirmed (\S+) \(([^)]+)\) belongs only to (\S+) and (user_\d+)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
USER_RE = re.compile(r"\buser_\d+\b")
_counted = set()  # files whose call usage this run already recorded


@lru_cache(maxsize=1)
def catalog_links():
    """resource_id -> (alt_name, email, user_id, system) from data-catalog activities."""
    links = {}
    for row in read_csv("system_activity.csv"):
        if row["event_type"] != "LINK_RESOURCE_IDENTITY":
            continue
        m = CATALOG_RE.search(row["details"])
        if not m:
            continue
        g = [x for x in m.groups() if x is not None]
        rid, alt, email, user = g[0], g[1], g[2].rstrip(";.,"), g[3]
        links[rid] = (alt, email, user, row["target_system"], row["activity_id"])
    return links


def squash(text):
    return re.sub(r"[\s_.\-]", "", text.lower())


def cache_path(src):
    return CACHE_DIR / "media" / f"{src}.json"


def load_reading(src):
    """The cached model reading of one file, or None when it has not been extracted."""
    path = cache_path(src)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def reading_text(value):
    """Every string in a reading, in order, joined: what the screen shows, as text."""
    if isinstance(value, dict):
        return " | ".join(reading_text(v) for v in value.values())
    if isinstance(value, list):
        return " | ".join(reading_text(v) for v in value)
    return value if isinstance(value, str) else ""


def parse_media(src, path, captured):
    from .evidence import Obs, RID_RE, system_of
    reading = load_reading(src)
    if reading is None:
        return []
    if src not in _counted:
        _counted.add(src)
        llm.USAGE.record("gemini", reading["model"], reading["usage"], cached=True, use=USE)
    records, text = reading["result"]["records"], reading_text(reading["result"])
    flat = squash(text)
    manifest = {r["file_id"]: r for r in read_csv("evidence_files.csv")}
    origin = manifest[src]["origin"]
    out = []
    seen = set()
    for rid, (alt, email, user, system, _aid) in catalog_links().items():
        # A bare user id only identifies the focus record on a reference-registry screen.
        alt_hit = squash(alt) in flat and (not re.fullmatch(r"user_\d+", alt) or origin == "identity_service")
        if squash(rid) in flat or alt_hit:
            state, cat = SCREEN_STATE.get(origin, ("PRESENT", None))
            out.append(Obs(src, captured, system, rid, state, cat, {"email": email, "user_id": user},
                           set(), f"media:{origin}"))
            seen.add(rid)
    if path.suffix.lower() == ".pdf":
        for rid in sorted(set(RID_RE.findall(text)) - seen):
            system = system_of(rid)
            state, cat = ("PRESENT", None)
            if system == "support_platform":
                state, cat = "PRESENT", "SUPPORT_ATTACHMENT"
            elif system == "security_service":
                state, cat = "PRESENT", "SECURITY_AUDIT_LOG"
            own = next((r["identifiers"] for r in records if r.get("resource_id") == rid), {})
            ids = {}
            email = own.get("email") or next(iter(EMAIL_RE.findall(text)), "")
            user = own.get("user_id") or next(iter(USER_RE.findall(text)), "")
            if email:
                ids["email"] = email
            if user:
                ids["user_id"] = user
            out.append(Obs(src, captured, system, rid, state, cat, ids, set(), f"media:{origin}"))
    return out


def model_view(src, rid, alt):
    """The model's own (state, category) for a record it listed: the first record whose id, references or
    identifier fields contain the resource id or the catalog alt name."""
    reading = load_reading(src) or {"result": {"records": []}}
    wanted = {squash(rid), squash(alt or "")} - {""}
    for r in reading["result"]["records"]:
        names = [r.get("resource_id", ""), *r.get("other_references", []), *r.get("identifiers", {}).values()]
        if any(w in squash(n) for w in wanted for n in names if n):
            return r["state"], r["category"]
    return None
