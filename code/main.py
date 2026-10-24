"""Entry point: python3 code/main.py [--sample] [--out PATH]

Reads dataset/, audits every request, writes output.csv at the repository root.
--sample audits dataset/sample_deletion_requests.csv instead (for evaluation).

Set ANTHROPIC_API_KEY to let Claude classify request intent; without it the multilingual rules decide.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import llm  # noqa: E402
from audit.data import REPO_ROOT, read_csv  # noqa: E402
from audit.engine import Auditor  # noqa: E402
from audit.intent import classify, rule_verdict  # noqa: E402
from evaluation.structure import report as check_structure  # noqa: E402
from evaluation.usage import write_usage_report  # noqa: E402


def case_sort_key(case_id):
    tail = case_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else case_id


def classify_all():
    """Intent for every known request (sample and evaluation share deletion-job batches)."""
    intents, disagreements = {}, []
    for name in ("sample_deletion_requests.csv", "deletion_requests.csv"):
        for req in read_csv(name):
            verdict = classify(req["request_text"], req["case_id"])
            intents[req["case_id"]] = verdict
            if verdict != rule_verdict(req["request_text"]):
                disagreements.append(req["case_id"])
    llm.save_cache()
    return intents, disagreements


def run(requests_file, out_path):
    started = time.time()
    intents, disagreements = classify_all()
    auditor = Auditor(intents)
    rows = []
    for req in read_csv(requests_file):
        if not intents[req["case_id"]]:
            rows.append((req["case_id"], "NOT_APPLICABLE", []))
            continue
        status, steps = auditor.audit(req)
        rows.append((req["case_id"], status, steps))
    rows.sort(key=lambda r: case_sort_key(r[0]))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for case_id, status, steps in rows:
            w.writerow([case_id, status, json.dumps(steps, separators=(",", ":"), ensure_ascii=False)])
    run_info = {"requests_file": requests_file, "rows": len(rows), "seconds": round(time.time() - started, 2),
                "llm_online": llm.available(), "intent_disagreements": disagreements,
                "usage": llm.USAGE.summary()}
    return rows, run_info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    requests_file = "sample_deletion_requests.csv" if args.sample else "deletion_requests.csv"
    out = Path(args.out) if args.out else REPO_ROOT / ("sample_predictions.csv" if args.sample else "output.csv")
    rows, info = run(requests_file, out)
    print(f"wrote {len(rows)} rows to {out} in {info['seconds']}s (llm online: {info['llm_online']})")
    ok = check_structure(out, requests_file)
    if not args.sample:
        write_usage_report(info)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
