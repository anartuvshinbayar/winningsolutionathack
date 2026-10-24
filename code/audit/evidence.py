"""Turn every evidence file into one flat list of observations.

An observation says: at time `t`, source `src` saw resource `rid` in `system`
in state `state`, carrying identifiers `ids` and found through lookup `keys`.

States:
  PRESENT / ABSENT            record exists or not
  IDENTIFIABLE                record exists and still carries direct identifiers
  ANONYMIZED / REDACTED       record exists with identifiers removed
  ACTIVE / DISABLED           data flow (connector) state
  ACTIVE / BLOCKED            backup restore path state
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field

from .data import DATASET, norm, read_csv
from . import media

PREFIX_SYSTEM = [
    ("audit_record_", "security_service"),
    ("data_flow_", "integration_service"),
    ("email_delivery_", "email_delivery_service"),
    ("legal_hold_", "legal_service"),
    ("tombstone_", "identity_service"),
    ("analytics_", "analytics_platform"),
    ("account_", "account_service"),
    ("support_", "support_platform"),
    ("search_", "search_service"),
    ("export_", "export_service"),
    ("backup_", "backup_service"),
    ("marketing_", "marketing_platform"),
    ("cache_", "cache_service"),
]

# Identifier fields. DIRECT ones are personal data; REF ones are the stable internal user reference.
EMAIL_FIELDS = {"email", "customer_email", "owner_email", "requester_email", "uploaded_by", "matched_email"}
PHONE_FIELDS = {"phone", "owner_phone", "requester_phone"}
NAME_FIELDS = {"full_name", "display_name", "requester_name", "name"}
HASH_FIELDS = {"email_hash"}
REF_FIELDS = {"customer_reference", "user_id", "subject_reference", "subject_ref"}
DIRECT_FIELDS = EMAIL_FIELDS | PHONE_FIELDS | NAME_FIELDS | HASH_FIELDS
KEY_FIELDS = {"lookup_key", "match_key", "query", "correlation_key", "subject_key", "scan_key",
              "owner_key", "lookup_value", "subject_selector", "subject_reference"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+\d[\d ()-]{6,}\d")
USER_RE = re.compile(r"\buser_\d+\b")
RID_RE = re.compile(r"\b(?:audit_record|data_flow|email_delivery|legal_hold|tombstone|analytics|account|"
                    r"support|search|export|backup|marketing|cache)_\d+\b")


def system_of(rid):
    for prefix, system in PREFIX_SYSTEM:
        if rid.startswith(prefix):
            return system
    return None


@dataclass
class Obs:
    src: str
    t: str
    system: str
    rid: str
    state: str
    cat: str = None
    ids: dict = field(default_factory=dict)
    keys: set = field(default_factory=set)
    kind: str = ""

    def identifiers(self):
        """All identifier values (normalized) that tie this record to a person."""
        vals = {norm(v) for v in self.ids.values()} | {norm(k) for k in self.keys}
        return {v for v in vals if v}


def clean_ids(record):
    """Pick identifier fields out of a flat record; drop nulls."""
    ids = {}
    for key, value in record.items():
        if key in DIRECT_FIELDS | REF_FIELDS and norm(value) and not isinstance(value, (dict, list)):
            ids[key] = str(value).strip()
    return ids


def has_direct(ids):
    return any(k in DIRECT_FIELDS for k in ids)


def text_identifiers(text):
    """Emails and phone numbers embedded in free text (support messages, OCR)."""
    if not text:
        return {}
    found = {}
    for i, m in enumerate(EMAIL_RE.findall(text)):
        found[f"text_email_{i}"] = m
    for i, m in enumerate(PHONE_RE.findall(text)):
        found[f"text_phone_{i}"] = m
    return found


def truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y")


# ---------------------------------------------------------------------------
# State inference per system from a generic flat record.

def state_from_record(system, rec, collection=""):
    """Return (state, category, ids) for a record that exists in a snapshot/export."""
    ids = clean_ids(rec)
    if system == "account_service":
        status = (rec.get("account_status") or "").upper()
        if status in ("DELETED", "PURGED"):
            return ("ABSENT" if not has_direct(ids) else "PRESENT"), "USER_PROFILE", ids
        return "PRESENT", "USER_PROFILE", ids
    if system == "search_service":
        return "PRESENT", "SEARCH_INDEX_ENTRY", ids
    if system == "analytics_platform":
        return ("IDENTIFIABLE" if ids else "ANONYMIZED"), "ANALYTICS_PROFILE", ids
    if system == "support_platform":
        if "attachment" in collection or rec.get("file_name") or rec.get("storage_class"):
            return "PRESENT", "SUPPORT_ATTACHMENT", ids
        text = rec.get("message_text") or rec.get("message_excerpt") or ""
        ids.update(text_identifiers(text))
        exposed = has_direct(ids) or any(k.startswith("text_") for k in ids)
        return ("IDENTIFIABLE" if exposed else "REDACTED"), "SUPPORT_MESSAGE", ids
    if system == "export_service":
        avail = rec.get("download_available", rec.get("available", True))
        return ("PRESENT" if avail is None or truthy(avail) or avail is True else "ABSENT"), "USER_EXPORT", ids
    if system == "integration_service":
        return ("ACTIVE" if truthy(rec.get("enabled")) or rec.get("enabled") is True else "DISABLED"), "DATA_FLOW", ids
    if system == "backup_service":
        allowed = rec.get("restore_allowed")
        return ("ACTIVE" if allowed is True or truthy(allowed) else "BLOCKED"), "BACKUP_ARCHIVE", ids
    if system == "security_service":
        return "PRESENT", "SECURITY_AUDIT_LOG", ids
    if system == "identity_service":
        return ("IDENTIFIABLE" if has_direct(ids) else "REDACTED"), "ACCOUNT_TOMBSTONE", ids
    if system == "legal_service":
        return "PRESENT", "LEGAL_HOLD_RECORD", ids
    if system == "marketing_platform":
        return "PRESENT", "MARKETING_PROFILE", ids
    if system == "email_delivery_service":
        return ("IDENTIFIABLE" if has_direct(ids) else "REDACTED"), "EMAIL_DELIVERY_LOG", ids
    return "PRESENT", None, ids


RESULT_STATE = {
    "FOUND": "PRESENT", "NOT_FOUND": "ABSENT", "IDENTIFIABLE": "IDENTIFIABLE",
    "ANONYMIZED": "ANONYMIZED", "REDACTED": "REDACTED", "ACTIVE": "ACTIVE",
    "DISABLED": "DISABLED", "BLOCKED": "BLOCKED", "ABSENT": "ABSENT", "PRESENT": "PRESENT",
}


def record_style(src, t, system, rid, record_type, result, subject, kind):
    """Rows that already state system/resource/category/result explicitly."""
    state = RESULT_STATE.get((result or "").upper(), "PRESENT")
    if record_type == "SUPPORT_MESSAGE" and state == "PRESENT":
        state = "IDENTIFIABLE"
    subject = (subject or "").strip()
    keys = {subject} if subject and subject.lower() not in ("no direct identifier", "null") else set()
    return Obs(src, t, system or system_of(rid), rid, state, record_type, {}, keys, kind)


# ---------------------------------------------------------------------------
# SQL snapshots

def parse_sql(src, path, captured):
    out = []
    con = sqlite3.connect(":memory:")
    con.executescript(path.read_text(encoding="utf-8"))
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    typed = {
        "customer_profiles": "account_service",
        "customer_search_documents": "search_service",
        "customer_analytics_profiles": "analytics_platform",
        "support_records": "support_platform",
        "customer_exports": "export_service",
        "connector_subscriptions": "integration_service",
        "backup_restore_entries": "backup_service",
        "security_audit_records": "security_service",
    }
    for table in tables:
        if table == "snapshot_scope":
            continue
        rows = [dict(r) for r in con.execute(f'select * from "{table}"')]
        for row in rows:
            rid = row["resource_id"]
            if "record_json" in row:
                rec = json.loads(row["record_json"])
                rec = flatten_document(rec)
                system = row.get("origin_system") or system_of(rid)
                state, cat, ids = state_from_record(system, rec, row.get("collection_name", ""))
                kind = f"sql:{table}"
            else:
                system = typed.get(table) or system_of(rid)
                rec = dict(row)
                if table.startswith("search_documents"):
                    rec.pop("source_resource_id", None)
                state, cat, ids = state_from_record(system, rec)
                kind = f"sql:{table}"
            out.append(Obs(src, captured, system, rid, state, cat, ids, set(), kind))
        # Replication drift: a replica table can hold rows its primary lost. Absence from the
        # primary table is evidence too, recorded per resource seen anywhere in this file.
    primaries = [t for t in tables if t.endswith("_primary")]
    for primary in primaries:
        present = {r[0] for r in con.execute(f'select resource_id from "{primary}"')}
        for table in tables:
            if table == primary or table == "snapshot_scope":
                continue
            for (rid,) in con.execute(f'select resource_id from "{table}"'):
                if rid not in present:
                    out.append(Obs(src, captured, system_of(rid), rid, "ABSENT", None, {}, set(),
                                   f"sql:{primary}:missing"))
    con.close()
    return out


def flatten_document(rec):
    """Search documents nest identifiers under `document`; lift them to the top level."""
    if isinstance(rec.get("document"), dict):
        merged = dict(rec)
        merged.update(rec["document"])
        merged.pop("document")
        return merged
    return rec


# ---------------------------------------------------------------------------
# Logs

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_kv(text):
    out = {}
    for key, value in KV_RE.findall(text):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        out[key] = None if value in ("null", "None", "") else value
    return out


def parse_log_line(line):
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, rest = m.groups()
    if rest.startswith("{"):
        rec = json.loads(rest)
        rec = {k: (None if v in ("null", None) else v) for k, v in rec.items()}
        rec.setdefault("service", rec.get("logger"))
    elif rest.startswith("<"):
        parts = rest.split(" ", 6)
        rec = parse_kv(parts[6] if len(parts) > 6 else "")
        rec["service"], rec["trace_id"] = parts[2], parts[3]
    elif rest.startswith("["):
        m2 = re.match(r"\[(\w+)\] \[([\w-]+)\] \[([\w-]+)\] (\w+): ?(.*)$", rest)
        if not m2:
            return None
        level, service, trace, event, kvs = m2.groups()
        rec = parse_kv(kvs)
        rec.update(level=level, service=service, trace_id=trace, event=event)
    elif " | " in rest[:12]:
        parts = rest.split(" | ", 4)
        rec = parse_kv(parts[4] if len(parts) > 4 else "")
        rec.update(level=parts[0], service=parts[1], trace_id=parts[2], event=parts[3])
    elif rest.startswith("requestId="):
        outer = parse_kv(rest)
        rec = parse_kv(outer.get("message") or "")
        rec.update(trace_id=outer.get("requestId"), service=outer.get("function"), level=outer.get("level"))
    else:
        rec = parse_kv(rest)
        if rest.split(" ", 1)[0] in ("INFO", "WARN", "DEBUG", "ERROR"):
            rec.setdefault("level", rest.split(" ", 1)[0])
    rec["ts"] = ts
    return rec


SERVICE_ALIASES = {"profile_service": "account_service", "support_replica": "support_platform"}
START_KEYS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "subject_ref", "query")


def parse_log(src, path, captured):
    out = []
    lookups = {}  # trace_id -> lookup keys from *_started events
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = parse_log_line(line)
        if not rec:
            continue
        event = rec.get("event") or ""
        trace = rec.get("trace_id")
        if event.endswith("_started"):
            for k in START_KEYS:
                if rec.get(k):
                    lookups.setdefault(trace, set()).add(rec[k])
            continue
        if event == "record_lookup":
            out.append(record_style(src, rec["ts"], rec.get("system"), rec["resource_id"],
                                    rec.get("record_type"), rec.get("result"), rec.get("subject_reference"),
                                    "log:record_lookup"))
            continue
        rid = rec.get("resource_id")
        if not rid or not RID_RE.fullmatch(rid):
            continue
        keys = set(lookups.get(trace, set()))
        for k in ("correlation_key", "lookup_key"):
            if rec.get(k):
                keys.add(rec[k])
        obs = log_event_obs(src, rec, event, rid, keys)
        if obs:
            out.append(obs)
    return out


def log_event_obs(src, rec, event, rid, keys):
    t = rec["ts"]
    system = system_of(rid)
    ids = clean_ids(rec)
    if rec.get("matched_value"):
        mv = rec["matched_value"]
        ids["matched_value"] = mv
    kind = f"log:{event}"

    def mk(state, cat=None):
        return Obs(src, t, system, rid, state, cat, ids, keys, kind)

    if event in ("profile_lookup_completed",):
        rows = rec.get("rows")
        status = rec.get("http_status")
        found = (rows not in (None, "0")) if rows is not None else status == "200"
        return mk("PRESENT" if found else "ABSENT", "USER_PROFILE")
    if event == "index_query_completed":
        hit = (rec.get("result") or "").lower() == "hit" and rec.get("total_hits") not in ("0",)
        return mk("PRESENT" if hit else "ABSENT", "SEARCH_INDEX_ENTRY")
    if event in ("analytics_profile_read_completed", "analytics_profile_read", "analytics_identity_scan"):
        direct = rec.get("direct_identifiers")
        identifiable = bool(ids) if direct is None else direct not in ("0",)
        return mk("IDENTIFIABLE" if identifiable else "ANONYMIZED", "ANALYTICS_PROFILE")
    if event == "contact_lookup_completed":
        # A marketing-contact lookup that still resolves (opt-in flag, hashed email key).
        found = rec.get("rows") not in ("0", None)
        return mk("PRESENT" if found else "ABSENT", "MARKETING_PROFILE")
    if event in ("support_ticket_read_completed", "message_read"):
        text = rec.get("message_text") or ""
        ids.update(text_identifiers(text))
        exposed = has_direct(ids) or any(k.startswith("text_") for k in ids)
        return mk("IDENTIFIABLE" if exposed else "REDACTED", "SUPPORT_MESSAGE")
    if event == "message_pii_scan":
        return mk("IDENTIFIABLE", "SUPPORT_MESSAGE")
    if event == "attachment_pii_scan":
        return mk("PRESENT", "SUPPORT_ATTACHMENT")
    if event in ("contact_query_completed",):
        # Legacy contact-index row keyed by the email itself: a secondary identifier, not a conversation.
        found = rec.get("rows") not in ("0", None)
        return mk("PRESENT" if found else "ABSENT", "SECONDARY_IDENTIFIER")
    if event == "deletion_marker_check":
        return None
    if event in ("connector_config_read_completed", "connector_heartbeat", "connector_config_loaded"):
        return mk("ACTIVE" if truthy(rec.get("enabled")) else "DISABLED", "DATA_FLOW")
    if event in ("restore_policy_read_completed",):
        return mk("ACTIVE" if truthy(rec.get("restore_allowed")) else "BLOCKED", "BACKUP_ARCHIVE")
    if event == "restore_candidate_evaluated":
        risky = truthy(rec.get("eligible")) and not truthy(rec.get("deletion_block"))
        return mk("ACTIVE" if risky else "BLOCKED", "BACKUP_ARCHIVE")
    if event == "restore_authorization_completed":
        return mk("BLOCKED" if (rec.get("decision") or "").lower() == "deny" else "ACTIVE", "BACKUP_ARCHIVE")
    if event in ("audit_record_read_completed",):
        return mk("PRESENT" if rec.get("rows") not in ("0",) else "ABSENT", "SECURITY_AUDIT_LOG")
    if event in ("export_download_check_completed", "export_metadata_read_completed"):
        return mk("PRESENT" if truthy(rec.get("available")) else "ABSENT", "USER_EXPORT")
    return None


# ---------------------------------------------------------------------------
# JSON records

SERVICE_EXPORT_KINDS = {
    "profile_lookups": "account_service", "profiles": "analytics_platform",
    "restore_entries": "backup_service", "exports": "export_service", "connectors": "integration_service",
    "index_queries": "search_service", "audit_records": "security_service", "tickets": "support_platform",
}


def parse_json(src, path, captured):
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    if "snapshots" in data:
        for snap in data["snapshots"]:
            t = snap.get("captured_at") or captured
            for rec in snap["records"]:
                if snap["collection"] == "operational_record_history":
                    out.append(record_style(src, rec.get("recorded_at") or t, rec.get("system"), rec["resource_id"],
                                            rec.get("record_type"), rec.get("result"), rec.get("lookup_value"),
                                            "json:history"))
                    continue
                flat = flatten_document(rec)
                system = snap["data_store"] if snap["data_store"] != "backup_restore_service" else system_of(rec["resource_id"])
                state, cat, ids = state_from_record(system, flat, snap["collection"])
                keys = {flat["subject_selector"]} if flat.get("subject_selector") else set()
                out.append(Obs(src, t, system, rec["resource_id"], state, cat, ids, keys, f"json:{snap['collection']}"))
    elif "service_exports" in data:
        for system, groups in data["service_exports"].items():
            for group, recs in groups.items():
                for rec in recs:
                    out.extend(service_export_obs(src, system, group, rec, captured))
    elif "records" in data and data["records"] and "record_type" in data["records"][0]:
        for rec in data["records"]:
            out.append(record_style(src, rec.get("recorded_at") or captured, rec.get("system"), rec["resource_id"],
                                    rec.get("record_type"), rec.get("result"), rec.get("subject_reference"),
                                    "json:records"))
    elif "records" in data:
        t = data.get("captured_at") or captured
        for rec in data["records"]:
            system = system_of(rec["resource_id"])
            state, cat, ids = state_from_record(system, rec, data.get("collection", ""))
            out.append(Obs(src, t, system, rec["resource_id"], state, cat, ids, set(), f"json:{data.get('collection')}"))
    return out


def service_export_obs(src, system, group, rec, captured):
    t = rec.get("observed_at") or captured
    rid = rec.get("resource_id")
    keys = {rec["query"]} if rec.get("query") else set()
    if group == "profile_lookups":
        prof = rec.get("profile")
        ids = clean_ids(prof) if prof else {}
        state = "PRESENT" if str(rec.get("status_code")) == "200" or prof else "ABSENT"
        return [Obs(src, t, "account_service", rid, state, "USER_PROFILE", ids, keys, "json:profile_lookups")]
    if group == "index_queries":
        docs = (rec.get("hits") or {}).get("documents") or []
        out = []
        for doc in docs:
            out.append(Obs(src, t, "search_service", doc["resource_id"], "PRESENT", "SEARCH_INDEX_ENTRY",
                           clean_ids(doc), keys, "json:index_queries"))
        if not docs and rid:
            out.append(Obs(src, t, "search_service", rid, "ABSENT", "SEARCH_INDEX_ENTRY", {}, keys, "json:index_queries"))
        return out
    state, cat, ids = state_from_record(system, rec)
    return [Obs(src, t, system, rid, state, cat, ids, keys, f"json:{group}")]


# ---------------------------------------------------------------------------
# Plain-text handover notes

def parse_text(src, path, captured):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- "):
            continue
        parts = [p.strip() for p in line[2:].split("|")]
        if len(parts) >= 6:
            t, system, rid, cat, result, subject = parts[:6]
            out.append(record_style(src, t, system, rid, cat, result, subject, "txt:handover"))
    return out


# ---------------------------------------------------------------------------

def load_all():
    """Parse every manifest file. Returns (observations, manifest dict file_id -> row)."""
    manifest = {r["file_id"]: r for r in read_csv("evidence_files.csv")}
    observations = []
    for fid, row in manifest.items():
        path = DATASET / row["file_path"]
        captured = row["captured_at"]
        suffix = path.suffix.lower()
        if suffix == ".sql":
            obs = parse_sql(fid, path, captured)
        elif suffix == ".log":
            obs = parse_log(fid, path, captured)
        elif suffix == ".json":
            obs = parse_json(fid, path, captured)
        elif suffix == ".txt":
            obs = parse_text(fid, path, captured)
        elif suffix in (".png", ".pdf"):
            obs = media.parse_media(fid, path, captured)
        else:
            obs = []
        observations.extend(o for o in obs if o.system and o.rid)
    return observations, manifest
