"""Attribute deletion-job activity to cases.

Many records carry no identifier once a deletion job has touched them (a 404 account lookup, an
anonymized analytics row). The activity stream still shows the job that touched them:

* The job submits the subject's primary profile for deletion 44-75 minutes before the workflow
  reports its status, and the submission result mirrors that status
  (COMPLETE -> SUCCESS, PARTIALLY_COMPLETE -> PARTIAL, NOT_STARTED -> FAILED).
* Every later action of the same job (policy actions, result callbacks, connector and restore
  checks, replication checkpoints) runs on the job's clock: its offset from the profile submission
  is a whole number of half-minutes plus 10 or 21 seconds (+-1s).

Cases that share a reported_at form one batch; we solve each batch jointly, fixing the links that
identifiers prove first and eliminating from there.
"""
from collections import defaultdict
from datetime import datetime

from .data import read_csv

STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
JOB_RESIDUALS = {9, 10, 11, 20, 21, 22}
WINDOW_BEFORE = 80 * 60      # account submission may start this long before reported_at
WINDOW_AFTER = 10 * 60
TAIL = 5 * 3600              # job callbacks continue this long after reported_at
JOB_TYPES = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
             "RETAIN_RECORD", "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS", "SYNC_RECORD", "RESTORE_BACKUP"}


def ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def same_job(anchor_time, event_time, reported_at=None):
    """Does an event run on the clock of the job whose profile submission happened at anchor_time?

    Before the status report, policy actions run in 7.5-minute slots starting 29.5 minutes before
    reported_at; slot k sits (40.5 + 29.5k) mod 60 seconds after the submission. After the report,
    result callbacks sit 20-22 seconds (mod 30) after it."""
    delta = int((ts(event_time) - ts(anchor_time)).total_seconds())
    if delta <= 0:
        return False
    if reported_at is not None and ts(event_time) < ts(reported_at):
        lead = (ts(reported_at) - ts(event_time)).total_seconds()
        slot = min(3, max(0, round((29.5 * 60 - lead) / 450)))
        center = (40.5 + 29.5 * slot) % 60
        gap = abs(delta % 60 - center)
        return min(gap, 60 - gap) <= 1.0
    if reported_at is not None:
        return delta % 30 in (20, 21, 22)
    return delta % 30 in JOB_RESIDUALS


class JobLinker:
    def __init__(self, cases, owners):
        """cases: list of dicts with case_id, user, reported_at, reported_status.
        owners: rid -> set(user_id) proven by identifiers."""
        self.owners = owners
        self.activities = read_csv("system_activity.csv")
        self.submissions = [a for a in self.activities
                            if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service"
                            and a["source_system"] == "deletion_worker"]
        self.cases = {c["case_id"]: c for c in cases}
        self.account = {}       # case_id -> submission activity row
        self.extra = defaultdict(dict)  # case_id -> rid -> activity row
        self._assign_accounts()
        self._attribute_actions()

    # ------------------------------------------------------------------
    def _window(self, case):
        rep = ts(case["reported_at"])
        return [a for a in self.submissions
                if -WINDOW_BEFORE <= (ts(a["event_time"]) - rep).total_seconds() <= WINDOW_AFTER]

    def _linked_activity(self, case):
        """Job-shaped activity on records the case's user provably owns (anchors for the fingerprint)."""
        rep = ts(case["reported_at"])
        out = []
        for a in self.activities:
            if not job_event(a):
                continue
            if case["user"] in self.owners.get(a["resource_id"], ()):
                d = (ts(a["event_time"]) - rep).total_seconds()
                if -WINDOW_BEFORE <= d <= TAIL:
                    out.append(a)
        return out

    def _assign_accounts(self):
        cand = {}
        for cid, case in self.cases.items():
            window = self._window(case)
            own = [a for a in window if case["user"] in self.owners.get(a["resource_id"], ())]
            if own:
                self.account[cid] = min(own, key=lambda a: abs((ts(a["event_time"]) - ts(case["reported_at"])).total_seconds()))
                continue
            free = [a for a in window if not self.owners.get(a["resource_id"])]
            want = STATUS_RESULT.get(case["reported_status"])
            matching = [a for a in free if a["reported_result"] == want] or free
            anchors = self._linked_activity(case)
            if anchors:
                scored = [(sum(same_job(a["event_time"], x["event_time"], case["reported_at"]) for x in anchors), a)
                          for a in matching]
                best = max((s for s, _ in scored), default=0)
                if best > 0:
                    matching = [a for s, a in scored if s == best]
            cand[cid] = matching
        taken = {a["activity_id"] for a in self.account.values()}
        changed = True
        while changed:
            changed = False
            for cid, options in cand.items():
                if cid in self.account:
                    continue
                options = [a for a in options if a["activity_id"] not in taken]
                cand[cid] = options
                if len(options) == 1:
                    self.account[cid] = options[0]
                    taken.add(options[0]["activity_id"])
                    changed = True
        for cid, options in sorted(cand.items()):
            if cid in self.account:
                continue
            options = [a for a in options if a["activity_id"] not in taken]
            if options:
                # Still ambiguous: take the submission closest to the usual job start (about an hour early).
                rep = ts(self.cases[cid]["reported_at"])
                pick = min(options, key=lambda a: abs((rep - ts(a["event_time"])).total_seconds() - 3540))
                self.account[cid] = pick
                taken.add(pick["activity_id"])

    def _attribute_actions(self):
        """Give unowned job actions to the one case whose submission they share a clock with."""
        anchored = [(cid, self.cases[cid]["reported_at"], a) for cid, a in self.account.items()]
        for a in self.activities:
            if not job_event(a) or self.owners.get(a["resource_id"]):
                continue
            t = ts(a["event_time"])
            hits = [cid for cid, rep, sub in anchored
                    if -WINDOW_BEFORE <= (t - ts(rep)).total_seconds() <= TAIL
                    and sub["activity_id"] != a["activity_id"] and same_job(sub["event_time"], a["event_time"], rep)]
            if len(hits) == 1:
                self.extra[hits[0]].setdefault(a["resource_id"], a)


def job_event(a):
    """Activity a deletion job emits (as opposed to routine background traffic)."""
    if a["source_system"] == "deletion_worker":
        return a["target_system"] != "account_service"
    if a["event_type"] == "SYNC_RECORD":
        return a["target_system"] in ("search_service", "account_replication_cluster", "account_failover_replica")
    return a["event_type"] == "RESTORE_BACKUP"
