"""Print everything the engine knows about one case: python3 code/tools/explain_case.py case_34 [case_35 ...]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import read_csv  # noqa: E402
from audit.engine import Auditor  # noqa: E402
from audit.intent import rule_verdict  # noqa: E402


def main(case_ids):
    reqs = {r["case_id"]: r for f in ("sample_deletion_requests.csv", "deletion_requests.csv") for r in read_csv(f)}
    intents = {cid: rule_verdict(r["request_text"]) for cid, r in reqs.items()}
    A = Auditor(intents)
    for cid in case_ids:
        req = reqs[cid]
        user = A.people.resolve(req["email"])
        p = A.people.people[user]
        print(f"\n===== {cid} {req['email']} -> {user} {p.name}")
        print(f"  requested {req['requested_at']}  reported {req['reported_status']} @ {req['reported_at']}  audit {req['audit_at']}")
        print(f"  text: {req['request_text'][:200]}")
        print(f"  emails {sorted(p.emails)} phones {sorted(p.phones)}")
        sub = A.jobs.account.get(cid)
        print(f"  job submission: {sub['activity_id'] + ' ' + sub['resource_id'] + ' ' + sub['event_time'] + ' ' + sub['reported_result'] if sub else None}")
        for rid, act in A.jobs.extra.get(cid, {}).items():
            print(f"  job action: {act['activity_id']} {act['event_time']} {act['event_type']} {rid} {act['reported_result']}")
        for rid, system in sorted(A.subject_resources(user, cid).items()):
            print(f"  -- {system} {rid}")
            for o in sorted(A.by_rid[rid], key=lambda o: o.t):
                flag = "  " if A.usable(o, req["audit_at"]) else "XX"
                print(f"     {flag} {o.t} {o.src:12s} {o.state:12s} {o.cat or '-':20s} {o.kind:32s} {dict(o.ids)} {sorted(o.keys)}")
        status, steps = A.audit(req)
        print(f"  => {status}")
        for s in steps:
            print(f"     {s['step_no']:2d} {s['phase']:12s} {s['system_id']:20s} {s['resource_id']:18s} {s['operation']:26s} {s['state']:10s} {s['evidence_id']}")


if __name__ == "__main__":
    main(sys.argv[1:])
