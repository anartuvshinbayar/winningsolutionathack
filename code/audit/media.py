"""Screenshots and PDFs.

Text comes from code/cache/media/<file_id>.txt (built by code/tools/extract_media.py with local OCR
or pdftotext). Each artifact is a business screen focused on one record. We find that record through
the data catalog's LINK_RESOURCE_IDENTITY activity (or a resource id printed in the text) and read its
state from what kind of screen it is.
"""
import re
from functools import lru_cache

from .data import CACHE_DIR, read_csv

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


def parse_media(src, path, captured):
    from .evidence import Obs, RID_RE, system_of
    cache = CACHE_DIR / "media" / f"{src}.txt"
    if not cache.exists():
        return []
    text = cache.read_text(encoding="utf-8")
    flat = squash(text)
    manifest = {r["file_id"]: r for r in read_csv("evidence_files.csv")}
    origin = manifest[src]["origin"]
    out = []
    seen = set()
    for rid, (alt, email, user, system, _aid) in catalog_links().items():
        # A bare user id in the text only identifies the focus record on a reference-registry screen.
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
            ids = {}
            emails = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
            users = re.findall(r"\buser_\d+\b", text)
            if emails:
                ids["email"] = emails[0]
            if users:
                ids["user_id"] = users[0]
            out.append(Obs(src, captured, system, rid, state, cat, ids, set(), f"media:{origin}"))
    return out
