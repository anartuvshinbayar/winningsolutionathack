"""Score predictions against labelled cases.

python3 code/evaluation/main.py                # run the agent on the 32 samples and score them
python3 code/evaluation/main.py --diff         # also print every mismatching case
python3 code/evaluation/main.py --pred P --gold G --requests R   # score any files

Metrics
  status accuracy    deletion_status matches
  exact row          status and the whole steps array match
  step P/R/F1        steps as (phase, system_id, resource_id, operation, state, evidence_id), order ignored
  field accuracy     positional comparison of every step field, over the longer of the two arrays
  sequence accuracy  among cases whose step multiset matches, the order also matches
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def key(step):
    return tuple(step[f] for f in FIELDS[1:])


def score(pred, gold):
    n = len(gold)
    status_ok = exact = seq_ok = seq_n = 0
    tp = fp = fn = 0
    field_hits = field_total = 0
    per_field = {f: [0, 0] for f in FIELDS}
    failures = []
    for case_id, (g_status, g_steps) in gold.items():
        p_status, p_steps = pred.get(case_id, ("MISSING", []))
        status_ok += p_status == g_status
        same = p_status == g_status and [key(s) for s in p_steps] == [key(s) for s in g_steps]
        exact += same
        pc, gc = Counter(map(key, p_steps)), Counter(map(key, g_steps))
        common = sum((pc & gc).values())
        tp += common
        fp += sum(pc.values()) - common
        fn += sum(gc.values()) - common
        if pc == gc and g_steps:
            seq_n += 1
            seq_ok += [key(s) for s in p_steps] == [key(s) for s in g_steps]
        width = max(len(p_steps), len(g_steps))
        for i in range(width):
            a = p_steps[i] if i < len(p_steps) else {}
            b = g_steps[i] if i < len(g_steps) else {}
            field_hits += sum(a.get(f) == b.get(f) for f in FIELDS)
            field_total += len(FIELDS)
            for f in FIELDS:
                per_field[f][0] += a.get(f) == b.get(f)
                per_field[f][1] += 1
        if not same:
            failures.append((case_id, p_status, g_status, pc - gc, gc - pc))
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "exact_row_accuracy": exact / n,
        "step_precision": prec,
        "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "field_accuracy": field_hits / field_total if field_total else 1.0,
        "sequence_accuracy": seq_ok / seq_n if seq_n else 1.0,
        "sequence_cases": seq_n,
        **{f"field_{f}": hits / total if total else 1.0 for f, (hits, total) in per_field.items()},
    }, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred")
    ap.add_argument("--gold", default=str(ROOT / "dataset" / "sample_output.csv"))
    ap.add_argument("--diff", action="store_true")
    args = ap.parse_args()
    pred_path = args.pred
    if not pred_path:
        from main import run
        pred_path = ROOT / "sample_predictions.csv"
        run("sample_deletion_requests.csv", pred_path)
    metrics, failures = score(load(pred_path), load(args.gold))
    for k, v in metrics.items():
        print(f"{k:20s} {v:.3f}" if isinstance(v, float) else f"{k:20s} {v}")
    if args.diff:
        for case_id, p_status, g_status, extra, missing in failures:
            print(f"\n{case_id}: predicted {p_status}, expected {g_status}")
            for s in sorted(missing):
                print("   missing ", s)
            for s in sorted(extra):
                print("   extra   ", s)
            if not extra and not missing:
                print("   (same steps, different order)")


if __name__ == "__main__":
    main()
