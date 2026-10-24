"""Entry point: python3 code/main.py [--sample] [--out PATH]

Reads dataset/, audits every request, writes output.csv at the repository root.
--sample audits dataset/sample_deletion_requests.csv instead (for evaluation).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit.data import REPO_ROOT, read_csv  # noqa: E402
from audit.engine import Auditor  # noqa: E402
from audit.intent import classify  # noqa: E402


def case_sort_key(case_id):
    tail = case_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else case_id


def run(requests_file, out_path):
    auditor = Auditor()
    rows = []
    for req in read_csv(requests_file):
        if not classify(req["request_text"], req["case_id"]):
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
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    requests_file = "sample_deletion_requests.csv" if args.sample else "deletion_requests.csv"
    out = Path(args.out) if args.out else REPO_ROOT / ("sample_predictions.csv" if args.sample else "output.csv")
    rows = run(requests_file, out)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
