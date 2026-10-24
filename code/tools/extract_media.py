"""Rebuild the screenshot and PDF readings in code/cache/media/ (run once; the results are committed).

python3 code/tools/extract_media.py            # read every file without a current cached reading
python3 code/tools/extract_media.py --force    # read all of them again

Gemini (llm.GEMINI_VISION_MODEL, default gemini-3.8-flash) reads each PNG or PDF, sent inline, and returns
every visible record as JSON (prompt and schema in audit/media.py). The committed readings were made with
GEMINI_VISION_MODEL=gemini-3.5-flash-lite, because gemini-3.8-flash kept timing out on the free tier; each
reading records the model that made it, and files are only read again with --force or when the prompt or
the file changes. Each answer goes to
code/cache/media/<file_id>.json with the model, prompt version, file hash and token usage; the usage report
counts those calls. Needs GEMINI_API_KEY. Runs of code/main.py only read the cache, so they stay offline.
At the end it prints, for every focus record, the state its screen type implies next to the model's own.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit import llm, media  # noqa: E402
from audit.data import DATASET, read_csv  # noqa: E402


def media_rows():
    return [r for r in read_csv("evidence_files.csv") if Path(r["file_path"]).suffix.lower() in llm.MEDIA_TYPES]


def extract(rows, force):
    if not os.environ.get("GEMINI_API_KEY") or os.environ.get("AUDIT_OFFLINE"):
        print("no GEMINI_API_KEY (environment or .env) or AUDIT_OFFLINE set; nothing read")
        return
    for row in rows:
        path = DATASET / row["file_path"]
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        old = media.load_reading(row["file_id"])
        if old and not force and old.get("prompt_version") == media.PROMPT_VERSION and old.get("sha256") == sha:
            continue
        result, usage = llm.read_file(path, media.SYSTEM, media.USER, media.SCHEMA)
        if result is None or not isinstance(result.get("records"), list):
            print(f"failed  {row['file_id']} {path.name}: {llm._gave_up or 'no usable answer'}")
            if llm._gave_up:
                break
            continue
        reading = {"file_id": row["file_id"], "file_path": row["file_path"], "sha256": sha,
                   "model": llm.GEMINI_VISION_MODEL, "prompt_version": media.PROMPT_VERSION,
                   "usage": usage, "result": result}
        media.cache_path(row["file_id"]).parent.mkdir(parents=True, exist_ok=True)
        media.cache_path(row["file_id"]).write_text(
            json.dumps(reading, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print(f"read    {row['file_id']} {path.name}: {len(result['records'])} records, {usage['input_tokens']} in / "
              f"{usage['output_tokens']} out / {usage['thinking_tokens']} thinking tokens")


def cross_check(rows):
    print("\nfocus records: state from the screen type vs the model's own reading")
    for row in rows:
        if media.load_reading(row["file_id"]) is None:
            print(f"  MISSING {row['file_id']} has no cached reading")
            continue
        for o in media.parse_media(row["file_id"], DATASET / row["file_path"], row["captured_at"]):
            alt = media.catalog_links().get(o.rid, (None,))[0]
            view = media.model_view(row["file_id"], o.rid, alt)
            mark = "agree " if view == (o.state, o.cat) else "DIFFER"
            print(f"  {mark} {row['file_id']} {o.rid:18s} screen {o.state}/{o.cat}  model {view}")


if __name__ == "__main__":
    rows = media_rows()
    extract(rows, "--force" in sys.argv)
    cross_check(rows)
