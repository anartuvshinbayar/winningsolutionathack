"""Current + historical identifiers for every user, and lookup from a request email."""
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .data import norm, read_csv


def phone_key(value):
    digits = re.sub(r"\D", "", value or "")
    return f"tel:{digits}" if len(digits) >= 7 else None


def ident_keys(value):
    """Normalized match keys for one identifier value (emails/hashes/user ids lower-cased, phones digits)."""
    n = norm(value)
    if not n:
        return set()
    keys = {n}
    if not re.search(r"[a-z@]", n):
        p = phone_key(n)
        if p:
            keys = {p}
    return keys


@dataclass
class Person:
    user_id: str
    name: str
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    hashes: set = field(default_factory=set)

    def keys(self):
        out = {self.user_id.lower()}
        out |= {e.lower() for e in self.emails}
        out |= {h.lower() for h in self.hashes}
        out |= {phone_key(p) for p in self.phones if phone_key(p)}
        return out


class Directory:
    def __init__(self):
        self.people = {}
        for row in read_csv("user_identities.csv"):
            p = Person(row["user_id"], row["name"])
            p.emails.add(row["email"])
            p.phones.add(row["phone"])
            p.hashes.add(row["email_hash"])
            self.people[row["user_id"]] = p
        self.history = defaultdict(list)  # user_id -> audit rows
        for row in read_csv("user_audit_log.csv"):
            p = self.people.get(row["user_id"])
            if not p:
                continue
            self.history[row["user_id"]].append(row)
            bucket = {"email": p.emails, "phone": p.phones, "email_hash": p.hashes}.get(row["field_name"])
            if bucket is not None:
                for v in (row["old_value"], row["new_value"]):
                    if v:
                        bucket.add(v)
        self.by_email = defaultdict(set)
        for p in self.people.values():
            for e in p.emails:
                self.by_email[e.lower()].add(p.user_id)
        # key -> owners, to detect identifiers shared by more than one person
        self.owners = defaultdict(set)
        for p in self.people.values():
            for k in p.keys():
                self.owners[k].add(p.user_id)

    def resolve(self, email):
        """User for a request email (current or historical). Prefers the current holder."""
        email = (email or "").strip().lower()
        ids = self.by_email.get(email, set())
        if len(ids) <= 1:
            return next(iter(ids), None)
        current = [u for u in ids if any(e.lower() == email for e in [read_current_email(self, u)])]
        return sorted(current or ids)[0]


def read_current_email(directory, user_id):
    # The first email added for a person came from the current snapshot row.
    for row in read_csv("user_identities.csv"):
        if row["user_id"] == user_id:
            return row["email"]
    return None
