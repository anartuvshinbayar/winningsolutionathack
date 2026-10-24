"""How did each case get its account? Tags every request by the weakest link the engine used.

python3 code/tools/risk_map.py                         # the 518 evaluation cases, lists the GUESS cases
python3 code/tools/risk_map.py --sample                # the 32 labelled cases, with whether each row scored exactly
python3 code/tools/risk_map.py --list GUESS,NONE,SINGLE  # list other tags too

Tags, in the order jobs.JobLinker tries them:
  IDENTIFIER    a deletion-job submission inside the time window names a record the person provably owns
                (an evidence file or a data-catalog link carries their identifier)
  SINGLE        nothing proven; the time window, the reported-status filter and batch elimination left one submission
  FINGERPRINT   several submissions were left; the job-clock fingerprint against the person's proven records chose
  GUESS         still several; the engine took the submission closest to 3540 s before reported_at
  NONE          no submission in the window at all
  NON_DELETION  the request does not authorize deletion, so no job is looked for

This script replays JobLinker._assign_accounts step by step and checks that its picks equal the engine's,
so the tags describe what the engine really did. It changes nothing.
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import DATASET, read_csv  # noqa: E402
from audit.engine import Auditor  # noqa: E402
from audit.intent import rule_verdict  # noqa: E402
from audit.jobs import STATUS_RESULT, same_job, ts  # noqa: E402
from evaluation.main import key as step_key  # noqa: E402
from evaluation.main import load as load_rows  # noqa: E402

TAGS = ["IDENTIFIER", "SINGLE", "FINGERPRINT", "GUESS", "NONE", "NON_DELETION"]


def lead(case, sub):
    """Seconds between the submission and the status report (positive = submitted earlier)."""
    return int((ts(case["reported_at"]) - ts(sub["event_time"])).total_seconds())


def trace(linker, catalog):
    """Replay JobLinker._assign_accounts; return case_id -> (tag, detail, pick)."""
    out, cand, note = {}, {}, {}
    for cid, case in linker.cases.items():
        window = linker._window(case)
        own = [a for a in window if case["user"] in linker.owners.get(a["resource_id"], ())]
        if own:
            pick = min(own, key=lambda a: abs(lead(case, a)))
            how = "catalog" if catalog.get(pick["resource_id"], (None,) * 3)[2] == case["user"] else "evidence"
            out[cid] = ("IDENTIFIER", f"owned via {how}", pick)
            continue
        free = [a for a in window if not linker.owners.get(a["resource_id"])]
        want = STATUS_RESULT.get(case["reported_status"])
        matching = [a for a in free if a["reported_result"] == want] or free
        anchors = linker._linked_activity(case)
        narrowed = False
        before = len(matching)
        if anchors:
            scored = [(sum(same_job(a["event_time"], x["event_time"], case["reported_at"]) for x in anchors), a)
                      for a in matching]
            best = max((s for s, _ in scored), default=0)
            if best > 0:
                kept = [a for s, a in scored if s == best]
                narrowed = len(kept) < len(matching)
                matching = kept
        cand[cid] = matching
        note[cid] = (len(window), before, len(anchors), narrowed)
    taken = {a["activity_id"] for _, _, a in out.values()}
    changed = True
    while changed:
        changed = False
        for cid, options in cand.items():
            if cid in out:
                continue
            options = [a for a in options if a["activity_id"] not in taken]
            cand[cid] = options
            if len(options) == 1:
                window, before, anchors, narrowed = note[cid]
                tag = "FINGERPRINT" if narrowed else "SINGLE"
                detail = f"window {window}, status-matched {before}, anchors {anchors}"
                out[cid] = (tag, detail, options[0])
                taken.add(options[0]["activity_id"])
                changed = True
    for cid, options in sorted(cand.items()):
        if cid in out:
            continue
        options = [a for a in options if a["activity_id"] not in taken]
        case = linker.cases[cid]
        if not options:
            window, before, anchors, _ = note[cid]
            out[cid] = ("NONE", f"window {window}, status-matched {before}", None)
            continue
        ranked = sorted(options, key=lambda a: abs(lead(case, a) - 3540))
        pick = ranked[0]
        taken.add(pick["activity_id"])
        if len(ranked) == 1:
            detail = f"{pick['resource_id']}@{lead(case, pick)}s is what an earlier guess in its batch left over"
        else:
            others = ", ".join(f"{a['resource_id']}@{lead(case, a)}s" for a in ranked[1:])
            detail = f"{len(ranked)} left, picked {pick['resource_id']}@{lead(case, pick)}s over {others}"
        out[cid] = ("GUESS", detail, pick)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--list", default="GUESS", help="comma-separated tags whose cases to list")
    args = ap.parse_args()
    requests = read_csv("sample_deletion_requests.csv" if args.sample else "deletion_requests.csv")
    intents = {r["case_id"]: rule_verdict(r["request_text"])
               for f in ("sample_deletion_requests.csv", "deletion_requests.csv") for r in read_csv(f)}
    A = Auditor(intents)
    tags = trace(A.jobs, A.links)

    bad = [cid for cid in A.jobs.cases
           if (tags[cid][2] or {}).get("activity_id") != (A.jobs.account.get(cid) or {}).get("activity_id")]
    if bad:
        print(f"WARNING: replay differs from the engine for {len(bad)} cases: {bad[:10]}")
    gold = load_rows(DATASET / "sample_output.csv") if args.sample else {}

    rows = []
    for req in requests:
        cid = req["case_id"]
        if not intents[cid]:
            rows.append(dict(cid=cid, tag="NON_DELETION", detail="", status="NOT_APPLICABLE", steps=0, job_steps=0,
                             user=A.people.resolve(req["email"]), req=req, exact=None))
            continue
        tag, detail, _ = tags[cid]
        user = A.people.resolve(req["email"])
        status, steps = A.audit(req)
        job_steps = sum(1 for s in steps if user not in A.owners.get(s["resource_id"], ()))
        exact = None
        if gold:
            g_status, g_steps = gold[cid]
            exact = status == g_status and [step_key(s) for s in steps] == [step_key(s) for s in g_steps]
        rows.append(dict(cid=cid, tag=tag, detail=detail, status=status, steps=len(steps), job_steps=job_steps,
                         user=user, req=req, exact=exact))

    statuses = ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]
    print(f"{len(rows)} cases from {'sample' if args.sample else 'evaluation'} requests\n")
    print(f"{'tag':14s}{'cases':>6s}" + "".join(f"{s:>16s}" for s in statuses)
          + f"{'steps':>8s}{'job-only':>10s}{'cases w/ job':>14s}" + (f"{'exact':>8s}" if gold else ""))
    for tag in TAGS:
        group = [r for r in rows if r["tag"] == tag]
        if not group:
            continue
        by_status = Counter(r["status"] for r in group)
        line = f"{tag:14s}{len(group):>6d}" + "".join(f"{by_status.get(s, 0):>16d}" for s in statuses)
        line += f"{sum(r['steps'] for r in group):>8d}{sum(r['job_steps'] for r in group):>10d}"
        line += f"{sum(1 for r in group if r['job_steps']):>14d}"
        if gold:
            line += f"{sum(1 for r in group if r['exact']):>8d}"
        print(line)
    by_how = Counter(r["detail"] for r in rows if r["tag"] == "IDENTIFIER")
    print("\nIDENTIFIER split: " + ", ".join(f"{k} {v}" for k, v in sorted(by_how.items())))
    print("job-only = steps on records that no identifier ties to the person (account or clock-attributed action)")

    for tag in [t.strip().upper() for t in args.list.split(",") if t.strip()]:
        group = [r for r in rows if r["tag"] == tag]
        if not group:
            continue
        print(f"\n{tag} ({len(group)}):")
        for r in group:
            req = r["req"]
            flag = "" if r["exact"] is None else ("  exact" if r["exact"] else "  MISS")
            print(f"  {r['cid']:9s} {r['user']:9s} {req['reported_status']:18s} reported {req['reported_at']}"
                  f"  -> {r['status']:11s} {r['steps']:2d} steps ({r['job_steps']} job-only){flag}")
            print(f"            {r['detail']}")


if __name__ == "__main__":
    main()
