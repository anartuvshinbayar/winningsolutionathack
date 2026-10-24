"""Structural checks every predictions file must pass (AGENTS.md section 6.3-6.6).

python3 code/evaluation/structure.py                      # checks output.csv against deletion_requests.csv
python3 code/evaluation/structure.py PRED REQUESTS.csv     # any file

Both code/main.py and code/evaluation/main.py print the result of these checks on every run.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "dataset"

HEADER = "case_id,deletion_status,steps_json"
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
OPERATIONS = {  # phase -> operation -> the only state it may carry
    "FINDING": {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"},
    "REMEDIATION": {"DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
                    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED"},
    "VERIFICATION": {"VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
                     "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                     "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"},
}


def case_number(case_id):
    tail = case_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else -1


def check_output(pred_path, requests_name="deletion_requests.csv"):
    """Return a list of problems with a predictions file; an empty list means it passes."""
    with open(DATASET / requests_name, newline="", encoding="utf-8") as f:
        requests = {r["case_id"]: r for r in csv.DictReader(f)}
    with open(DATASET / "evidence_files.csv", newline="", encoding="utf-8") as f:
        captured = {r["file_id"]: r["captured_at"] for r in csv.DictReader(f)}
    with open(pred_path, newline="", encoding="utf-8") as f:
        text = f.read()
    problems = []
    if text.splitlines()[:1] != [HEADER]:
        problems.append("header is not exactly " + HEADER)
    rows = list(csv.DictReader(text.splitlines()))
    ids = [r.get("case_id", "") for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate case_id rows")
    missing, extra = set(requests) - set(ids), set(ids) - set(requests)
    if missing or extra:
        problems.append(f"{len(rows)} rows for {len(requests)} requests ({len(missing)} missing, {len(extra)} unknown)")
    if ids != sorted(ids, key=case_number):
        problems.append("rows are not sorted by case_id")
    for row in rows:
        problems.extend(f"{row.get('case_id')}: {p}" for p in check_row(row, requests, captured))
    return problems


def check_row(row, requests, captured):
    status = row.get("deletion_status")
    if status not in STATUSES:
        return [f"unknown status {status!r}"]
    try:
        steps = json.loads(row.get("steps_json") or "")
    except json.JSONDecodeError:
        return ["steps_json is not valid JSON"]
    if not isinstance(steps, list):
        return ["steps_json is not a JSON array"]
    if status == "NOT_APPLICABLE":
        return [] if not steps else ["NOT_APPLICABLE with steps"]
    out = []
    audit_at = requests.get(row["case_id"], {}).get("audit_at", "")
    if [s.get("step_no") for s in steps] != list(range(1, len(steps) + 1)):
        out.append("step_no not consecutive from 1")
    order = [PHASES.index(s["phase"]) for s in steps if s.get("phase") in PHASES]
    if order != sorted(order):
        out.append("phases out of order")
    for s in steps:
        if set(s) != STEP_KEYS:
            out.append(f"step {s.get('step_no')}: fields {sorted(s)}")
            continue
        allowed = OPERATIONS.get(s["phase"], {})
        if allowed.get(s["operation"]) != s["state"]:
            out.append(f"step {s['step_no']}: {s['phase']} {s['operation']} {s['state']} not allowed")
        if status == "COMPLETE" and s["phase"] != "VERIFICATION":
            out.append(f"step {s['step_no']}: COMPLETE case has a {s['phase']} step")
        cites = s["phase"] == "FINDING" or (status == "COMPLETE" and s["phase"] == "VERIFICATION")
        if cites and s["evidence_id"] not in captured:
            out.append(f"step {s['step_no']}: evidence {s['evidence_id']!r} not in the manifest")
        elif cites and captured[s["evidence_id"]] > audit_at:
            out.append(f"step {s['step_no']}: {s['evidence_id']} captured after audit_at")
        elif not cites and s["evidence_id"] != "":
            out.append(f"step {s['step_no']}: future work must have an empty evidence_id")
    return out


def report(pred_path, requests_name="deletion_requests.csv", limit=10):
    """Print one line (plus up to `limit` problems) and return True when the file passes."""
    problems = check_output(pred_path, requests_name)
    name = Path(pred_path).name
    if not problems:
        print(f"structural checks: PASS ({name} against {requests_name})")
        return True
    print(f"structural checks: FAIL ({name}, {len(problems)} problems)")
    for p in problems[:limit]:
        print("   ", p)
    return False


if __name__ == "__main__":
    pred = sys.argv[1] if len(sys.argv) > 1 else ROOT / "output.csv"
    reqs = sys.argv[2] if len(sys.argv) > 2 else "deletion_requests.csv"
    sys.exit(0 if report(pred, reqs) else 1)
