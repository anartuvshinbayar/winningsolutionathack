"""Entry point: python3 code/main.py [--sample] [--out PATH]

Reads dataset/, audits every request, writes output.csv at the repository root.
--sample audits dataset/sample_deletion_requests.csv instead (for evaluation).

Request intent comes from Claude when ANTHROPIC_API_KEY is set, from Gemini when GEMINI_API_KEY is set, and from
the multilingual rules otherwise (keys from the environment or an uncommitted .env). The rules also cross-check
every model verdict and the run prints each disagreement.
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
from audit.intent import classify_texts, rule_verdict  # noqa: E402
from evaluation.structure import report as check_structure  # noqa: E402
from evaluation.usage import write_usage_report  # noqa: E402


def case_sort_key(case_id):
    tail = case_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else case_id


def classify_all():
    """Intent for every known request (sample and evaluation share deletion-job batches).

    The model decides every text it answered; the rules decide the rest and cross-check the model."""
    requests = [req for name in ("sample_deletion_requests.csv", "deletion_requests.csv") for req in read_csv(name)]
    verdicts, decided = classify_texts([r["request_text"] for r in requests])
    llm.save_cache()
    intents = {r["case_id"]: verdicts[r["request_text"]] for r in requests}
    disagreements = [r["case_id"] for r in requests if r["request_text"] in decided
                     and verdicts[r["request_text"]] != rule_verdict(r["request_text"])]
    if decided:
        source = ", ".join(sorted(llm.USAGE.models)) or "model"
        print(f"intent: {source} decided {len(decided)} of {len(verdicts)} distinct texts, rules the rest")
        if disagreements:
            for case_id in disagreements:
                print(f"intent: model says {'deletion' if intents[case_id] else 'not deletion'} for {case_id}, "
                      f"rules say {'not deletion' if intents[case_id] else 'deletion'}")
        else:
            print("intent: model and rules agree on every request")
    else:
        print(f"intent: no model answers, the multilingual rules decided all {len(verdicts)} distinct texts")
    return intents, {"disagreements": disagreements, "decided": len(decided), "distinct": len(verdicts)}


def run(requests_file, out_path):
    started = time.time()
    intents, intent_info = classify_all()
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
                "llm_online": llm.available(), "llm_provider": llm.provider(), "llm_model": llm.model_name(),
                "intent_disagreements": intent_info["disagreements"], "intent_decided": intent_info["decided"],
                "intent_distinct": intent_info["distinct"],
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
