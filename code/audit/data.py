"""Paths and CSV loading. All paths resolve from the repository root."""
import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "dataset"
CACHE_DIR = REPO_ROOT / "code" / "cache"


def read_csv(name):
    with open(DATASET / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def norm(value):
    """Canonical form for identifier matching: trimmed, lower-case, None for empty/null."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text.lower()


def num_key(resource_id):
    """Sort key that orders `search_052` and `search_1308` by prefix, then number."""
    prefix, _, tail = resource_id.rpartition("_")
    return (prefix, int(tail)) if tail.isdigit() else (resource_id, -1)
