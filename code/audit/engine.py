"""Per-case reasoning: who is the subject, which records are theirs, what state each is in at
audit_at, what the policy requires, and the ordered steps that follow."""
from collections import Counter, defaultdict

from .data import num_key, read_csv
from .evidence import load_all, system_of
from .identity import Directory, ident_keys
from .jobs import JobLinker
from .media import catalog_links

ACTION_STATE = {"DELETE": "ABSENT", "REDACT": "REDACTED", "ANONYMIZE": "ANONYMIZED", "RETAIN": "PRESENT"}

# required final state -> (finding op, finding state, remediation op, verification op)
PLAYBOOK = {
    "ABSENT": ("REPORT_REMAINING_RECORD", "PRESENT", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACTED": ("REPORT_EXPOSED_PII", "PRESENT", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZED": ("REPORT_EXPOSED_PII", "PRESENT", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "DISABLED": ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCKED": ("REPORT_RESTORE_RISK", "ACTIVE", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "PRESENT": (None, None, None, "VERIFY_RECORD_PRESENT"),
}
OP_STATE = {
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
OP_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
EXPOSURE = {"IDENTIFIABLE": 3, "PRESENT": 2, "ACTIVE": 2}

# Category when no capture names one: the system a resource id lives in.
DEFAULT_CATEGORY = {
    "account_service": "USER_PROFILE", "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE", "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT", "integration_service": "DATA_FLOW", "backup_service": "BACKUP_ARCHIVE",
    "security_service": "SECURITY_AUDIT_LOG", "identity_service": "ACCOUNT_TOMBSTONE",
    "legal_service": "LEGAL_HOLD_RECORD", "marketing_platform": "MARKETING_PROFILE",
    "cache_service": "CACHE_ENTRY", "email_delivery_service": "EMAIL_DELIVERY_LOG",
}


def load_policy():
    policy = {}
    for row in read_csv("deletion_rules.csv"):
        state = ACTION_STATE[row["required_action"]]
        if row["data_category"] == "BACKUP_ARCHIVE":
            state = "BLOCKED"  # retained, but restore access must be blocked
        policy[row["data_category"]] = state
    policy["DATA_FLOW"] = "DISABLED"  # an active sync can recreate deleted data
    return policy


def compliant(actual, required):
    if actual is None:
        return True  # nothing observed: no evidence of a violation
    if required == "ABSENT":
        return actual == "ABSENT"
    if required in ("REDACTED", "ANONYMIZED"):
        return actual in (required, "ABSENT")
    return actual == required


class Auditor:
    def __init__(self, intents=None):
        """intents: case_id -> True when the request authorizes deletion (non-deletion cases start no job)."""
        intents = intents or {}
        self.obs, self.manifest = load_all()
        self.people = Directory()
        self.policy = load_policy()
        self.file_time = {fid: row["captured_at"] for fid, row in self.manifest.items()}
        self.by_rid = defaultdict(list)
        self.system = {}
        for o in self.obs:
            self.by_rid[o.rid].append(o)
            self.system.setdefault(o.rid, o.system)
        self.links = catalog_links()
        for rid, (_alt, _email, _user, system, _aid) in self.links.items():
            self.system.setdefault(rid, system)
        self.owners = self._owners()
        cases = []
        for name in ("sample_deletion_requests.csv", "deletion_requests.csv"):
            for r in read_csv(name):
                if intents.get(r["case_id"], True):
                    cases.append(dict(r, user=self.people.resolve(r["email"])))
        self.cases_of = defaultdict(list)
        for c in cases:
            self.cases_of[c["user"]].append(c)
        self.jobs = JobLinker(cases, self.owners)
        self.snapshots = defaultdict(set)  # system -> SQL captures that hold rows for it
        for o in self.obs:
            if o.kind.startswith("sql:"):
                self.snapshots[o.system].add(o.src)

    def _owners(self):
        """rid -> users whose identifiers appear on it (any capture time)."""
        key_owner = defaultdict(set)
        for uid, p in self.people.people.items():
            for k in p.keys():
                key_owner[k].add(uid)
        owners = defaultdict(set)
        self.link_time = {}
        for o in self.obs:
            for v in o.identifiers():
                for k in ident_keys(v):
                    for uid in key_owner.get(k, ()):
                        owners[o.rid].add(uid)
                        prev = self.link_time.get((o.rid, uid))
                        self.link_time[(o.rid, uid)] = min(prev, o.t) if prev else o.t
        acts = {a["activity_id"]: a for a in read_csv("system_activity.csv")}
        for rid, (_alt, _email, user, _system, aid) in self.links.items():
            owners[rid].add(user)
            t = acts[aid]["event_time"]
            prev = self.link_time.get((rid, user))
            self.link_time[(rid, user)] = min(prev, t) if prev else t
        return owners

    # -- discovery ---------------------------------------------------------
    def usable(self, o, audit_at):
        return self.file_time.get(o.src, o.t) <= audit_at and o.t <= audit_at

    def case_for_link(self, rid, user):
        """When a person filed several requests, a record belongs to the first request audited
        after the capture that tied it to them."""
        cases = sorted(self.cases_of[user], key=lambda c: c["audit_at"])
        t = self.link_time.get((rid, user), "")
        for c in cases:
            if t <= c["audit_at"]:
                return c["case_id"]
        return cases[-1]["case_id"]

    def subject_resources(self, user, case_id):
        found = {rid: self.system.get(rid) or system_of(rid) for rid, users in self.owners.items() if user in users}
        if len(self.cases_of[user]) > 1:
            found = {rid: s for rid, s in found.items() if self.case_for_link(rid, user) == case_id}
        sub = self.jobs.account.get(case_id)
        if sub:
            found.setdefault(sub["resource_id"], "account_service")
        for rid, act in self.jobs.extra.get(case_id, {}).items():
            found.setdefault(rid, act["target_system"] if system_of(rid) is None else system_of(rid))
        return {rid: s for rid, s in found.items() if s}

    # -- state ---------------------------------------------------------------
    def current(self, rid, audit_at):
        seen = [o for o in self.by_rid[rid] if self.usable(o, audit_at)]
        cats = [o.cat for o in sorted(self.by_rid[rid], key=lambda o: o.t, reverse=True) if o.cat]
        if not seen:
            return None, (cats[0] if cats else None)
        top = max(o.t for o in seen)
        same_time = [o for o in seen if o.t == top]
        # Copies captured together (primary and replicas): the one still exposing data decides.
        latest = max(same_time, key=lambda o: (EXPOSURE.get(o.state, 0), self.file_time.get(o.src, "")))
        return latest, (cats[0] if cats else None)

    # -- planning ------------------------------------------------------------
    def audit(self, req):
        audit_at = req["audit_at"]
        user = self.people.resolve(req["email"])
        if not user:
            return "COMPLETE", []
        items = []
        late_accounts = []  # the person's account, seen only in captures after audit_at
        job_case = req.get("job_case", req["case_id"])  # custom test cases may reuse a real case's job
        for rid, system in self.subject_resources(user, job_case).items():
            latest, cat = self.current(rid, audit_at)
            cat = cat or DEFAULT_CATEGORY.get(system)
            if cat not in self.policy:
                continue
            required = self.policy[cat]
            from_job = rid in self.jobs.extra.get(job_case, {}) or \
                rid == self.jobs.account.get(job_case, {}).get("resource_id")
            if latest is None and not from_job and required != "PRESENT":
                # Only captures taken after audit_at know about this record; they cannot shape the answer.
                # The account itself is the exception: an incomplete case still verifies it (below).
                if system == "account_service" and cat == "USER_PROFILE" and self.by_rid[rid]:
                    late_accounts.append(dict(system=system, rid=rid, cat=cat, required=required, actual=None,
                                              ok=True, evidence=None))
                continue
            actual = latest.state if latest else None
            if actual == "IDENTIFIABLE" and required == "ABSENT":
                actual = "PRESENT"
            items.append(dict(system=system, rid=rid, cat=cat, required=required, actual=actual,
                              ok=compliant(actual, required), evidence=latest.src if latest else None))
        incomplete = any(not it["ok"] for it in items)
        if incomplete:
            # Every incomplete sample verifies the account after remediation (22/22). The verification is
            # future work, so it needs no admissible capture; complete cases stay evidence-only.
            items.extend(late_accounts)
        dominant = Counter(it["evidence"] for it in items if it["evidence"]).most_common(1)
        fallback = dominant[0][0] if dominant else ""
        for it in items:
            if not it["evidence"] and not incomplete:
                # No capture mentions the record: the latest snapshot of its system that predates the
                # audit and does not contain it shows it absent.
                snaps = [f for f in self.snapshots.get(it["system"], ()) if self.file_time[f] <= audit_at]
                if not fallback and snaps and it["required"] == "ABSENT":
                    it["evidence"] = max(snaps, key=lambda f: self.file_time[f])
        steps = []
        for it in items:
            find_op, find_state, fix_op, verify_op = PLAYBOOK[it["required"]]
            if incomplete:
                if not it["ok"] and find_op:
                    steps.append(("FINDING", it["system"], it["rid"], find_op, find_state, it["evidence"]))
                    steps.append(("REMEDIATION", it["system"], it["rid"], fix_op, OP_STATE[fix_op], ""))
                steps.append(("VERIFICATION", it["system"], it["rid"], verify_op, OP_STATE[verify_op], ""))
            else:
                steps.append(("VERIFICATION", it["system"], it["rid"], verify_op, OP_STATE[verify_op],
                              it["evidence"] or fallback))
        steps.sort(key=lambda s: (PHASE_ORDER[s[0]], OP_ORDER.index(s[3]), s[1], num_key(s[2])))
        out = [dict(step_no=i + 1, phase=s[0], system_id=s[1], resource_id=s[2], operation=s[3],
                    state=s[4], evidence_id=s[5]) for i, s in enumerate(steps)]
        return ("INCOMPLETE" if incomplete else "COMPLETE"), out
