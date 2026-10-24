"""Our own evaluation cases, built on the real evidence with hand-checked expectations.

python3 code/evaluation/custom_cases.py

Each case is a request we wrote (or a real request with one field changed) plus the expected status and the
steps that must / must not appear. They target the traps the challenge describes: historical identifiers,
identifier formatting, evidence captured after audit_at, and non-deletion requests in other languages.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from audit.data import read_csv  # noqa: E402
from audit.engine import Auditor  # noqa: E402
from audit.intent import classify, rule_verdict  # noqa: E402

BASE = {r["case_id"]: r for f in ("sample_deletion_requests.csv", "deletion_requests.csv") for r in read_csv(f)}


def from_case(base_id, **changes):
    req = dict(BASE[base_id])
    req["job_case"] = base_id
    req.update(changes)
    return req


CASES = [
    {
        "name": "C1 historical email",
        "why": "Request arrives with Haruka's pre-May address; the profile screenshot (evidence_04) shows the account live.",
        "request": from_case("case_34", case_id="custom_01", email="haruka.s183889@zoho.com"),
        "status": "INCOMPLETE",
        "must": [("FINDING", "account_service", "account_5762", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_04"),
                 ("FINDING", "support_platform", "support_9538", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
                 ("VERIFICATION", "security_service", "audit_record_3753", "VERIFY_RECORD_PRESENT", "PRESENT", "")],
        "must_not": [],
    },
    {
        "name": "C2 mixed-case email",
        "why": "Same subject as case_53 written as Kagyeman8947@FastMail.com; identity matching must ignore case.",
        "request": from_case("case_53", case_id="custom_02", email="Kagyeman8947@FastMail.com"),
        "status": "INCOMPLETE",
        "must": [("FINDING", "support_platform", "support_1034", "REPORT_EXPOSED_PII", "PRESENT", "evidence_28")],
        "must_not": [],
    },
    {
        "name": "C3 evidence after audit_at is ignored",
        "why": "case_121 as filed: search_3573 is only seen in a capture two days after the audit, so it cannot "
               "change the answer.",
        "request": from_case("case_121", case_id="custom_03"),
        "status": "COMPLETE",
        "must": [("VERIFICATION", "account_service", "account_081", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_88"),
                 ("VERIFICATION", "security_service", "audit_record_2103", "VERIFY_RECORD_PRESENT", "PRESENT", "evidence_88")],
        "must_not": [("FINDING", "search_service", "search_3573", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_11")],
    },
    {
        "name": "C4 same case audited after the late capture",
        "why": "Move case_121's audit_at past evidence_11: the search entry is now visible and must be reported.",
        "request": from_case("case_121", case_id="custom_04", audit_at="2026-08-25T11:00:00Z"),
        "status": "INCOMPLETE",
        "must": [("FINDING", "search_service", "search_3573", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_11"),
                 ("REMEDIATION", "search_service", "search_3573", "DELETE_RECORD", "ABSENT", "")],
        "must_not": [],
    },
    {
        "name": "C5 Portuguese correction that mentions deletion",
        "why": "'Não quero excluir a conta' contains the deletion verb but refuses deletion.",
        "request": from_case("case_40", case_id="custom_05",
                             request_text="Não quero excluir a conta. Só preciso corrigir o número de telefone."),
        "status": "NOT_APPLICABLE",
        "must": [],
        "must_not": [],
    },
    {
        "name": "C6 Spanish export request",
        "why": "An access/export request in Spanish that names the account but asks to keep it.",
        "request": from_case("case_40", case_id="custom_06",
                             request_text="Necesito una copia de mis datos personales. No quiero eliminar mi cuenta."),
        "status": "NOT_APPLICABLE",
        "must": [],
        "must_not": [],
    },
]


def key(step):
    return (step["phase"], step["system_id"], step["resource_id"], step["operation"], step["state"], step["evidence_id"])


def run(auditor=None):
    if auditor is None:
        intents = {cid: rule_verdict(r["request_text"]) for cid, r in BASE.items()}
        auditor = Auditor(intents)
    results = []
    for case in CASES:
        req = case["request"]
        if classify(req["request_text"], req["case_id"]):
            status, steps = auditor.audit(req)
        else:
            status, steps = "NOT_APPLICABLE", []
        got = {key(s) for s in steps}
        problems = []
        if status != case["status"]:
            problems.append(f"status {status} != {case['status']}")
        problems += [f"missing {s}" for s in case["must"] if s not in got]
        problems += [f"unexpected {s}" for s in case["must_not"] if s in got]
        results.append((case, status, problems))
    return results


def main():
    results = run()
    for case, status, problems in results:
        print(f"{'PASS' if not problems else 'FAIL'}  {case['name']}  -> {status}")
        for p in problems:
            print(f"      {p}")
    print(f"\n{sum(not p for _, _, p in results)}/{len(results)} custom cases pass")


if __name__ == "__main__":
    main()
