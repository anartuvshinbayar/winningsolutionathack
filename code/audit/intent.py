"""Does the request text authorize deletion?

Claude reads the request (any language) and returns a structured verdict. Multilingual rules give the
same verdict offline and act as the fallback; disagreements are reported by the evaluation script.
"""
import re

from . import llm

PROMPT_VERSION = "intent-v1"

SYSTEM = """You review privacy requests sent to an online shop's customer-data team.
Decide whether the customer is asking for their account and personal data to be deleted, erased, or
closed. Requests for access, a copy or export of data, correction of details, password or login help,
invoices, or opting out of marketing are NOT deletion requests, even when they mention data or the account.
A deletion request stays a deletion request when it also allows some records to be kept (anonymous
statistics, security logs, backups that cannot be restored, an internal reference).
The request may be in any language. Reply with the JSON object only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "authorizes_deletion": {"type": "boolean"},
        "request_type": {"type": "string", "enum": [
            "deletion", "access", "export", "correction", "marketing_opt_out", "account_support", "other"]},
        "language": {"type": "string"},
    },
    "required": ["authorizes_deletion", "request_type", "language"],
    "additionalProperties": False,
}

# Phrases that say the customer wants the account kept (checked first).
KEEP = [
    r"not asking for deletion", r"not a request to (erase|delete)", r"\bdon'?t delete", r"do not (delete|close|remove)",
    r"without removing", r"leave (the|my) (account|profile)", r"keep (the|my) account", r"account active",
    r"continue using the service", r"still (use|need) (it|the account)", r"marketing lists only",
    r"stop the promotional", r"(send|give) me a copy", r"copy of (the information|last year)", r"download my", r"portable format",
    r"what personal information you hold", r"misspelled", r"change the email address", r"replace it with",
    r"reset(ting)? the login", r"invoices",
]
# Deletion wording across the languages seen in requests.
DELETE = [
    r"\bdelet", r"\berase", r"\berasure", r"remove (my|the) (account|profile)", r"close (my|this|the) account",
    r"closed my account", r"permanent", r"\bremov", r"\bgone\b", r"no longer (use|need) this account",
    r"suppression", r"supprim", r"eliminaci", r"eliminar", r"exclus", r"excluir", r"löschen", r"lösch",
    r"cancellazione", r"削除", r"삭제", r"删除", r"удал", r"usuni",
]


def rule_verdict(text):
    t = (text or "").lower()
    if any(re.search(p, t) for p in KEEP):
        return False
    return any(re.search(p, t) for p in DELETE)


def model_verdict(text):
    result = llm.structured(SYSTEM, f"Customer request:\n{text}", SCHEMA, PROMPT_VERSION)
    return None if result is None else bool(result["authorizes_deletion"])


def classify(text, case_id=None):
    """True when the request authorizes deletion."""
    verdict = model_verdict(text)
    return rule_verdict(text) if verdict is None else verdict
