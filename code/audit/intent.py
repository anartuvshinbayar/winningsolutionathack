"""Does the request text authorize deletion?

A model (Claude or Gemini, see llm.py) reads the requests in batches of 20 distinct texts, in any language,
and returns a structured verdict for each. Multilingual rules give a verdict offline, act as the fallback for
any text the model did not answer, and cross-check the model: main.py prints every disagreement.
"""
import re

from . import llm

PROMPT_VERSION = "intent-v3-batch"
BATCH_SIZE = 20

SYSTEM = """You review privacy requests sent to an online shop's customer-data team.
Decide whether the customer is asking for their account and personal data to be deleted, erased, or
closed. Requests for access, a copy or export of data, correction of details, password or login help,
invoices, or opting out of marketing are NOT deletion requests, even when they mention data or the account.
A deletion request stays a deletion request when it also allows some records to be kept (anonymous
statistics, security logs, backups that cannot be restored, an internal reference). Asking you to confirm
or verify that the account was deleted, or to finish a deletion, is also a deletion request.
The requests may be in any language. You get a JSON list of requests, each with an id; return one result
per id. Reply with the JSON object only."""

ITEM_SCHEMA = {  # one result; llm.structured_batch adds the id and wraps the list
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
    # Portuguese, Spanish, French, Japanese, Korean refusals and access/correction requests
    r"não (quero|desejo) (excluir|apagar|eliminar|fechar)", r"sem (excluir|apagar)", r"c[óo]pia d[oe]s? meus dados", r"corrigir",
    r"no (quiero|deseo) (eliminar|borrar|cerrar)", r"sin (eliminar|borrar)", r"copia de mis datos", r"corregir",
    r"ne (veux|souhaite) pas (supprimer|fermer)", r"sans supprimer", r"copie de mes données", r"corriger",
    r"削除しないで", r"削除せず", r"삭제하지 ?(마|말)", r"삭제 없이",
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


def model_verdicts(texts):
    """{text: bool} for every distinct text the model answered (now or from the cache)."""
    distinct = sorted(set(texts))  # sorted, so the batches are the same on every run
    answers = llm.structured_batch(SYSTEM, {t: t for t in distinct}, ITEM_SCHEMA, PROMPT_VERSION, BATCH_SIZE)
    return {t: bool(a["authorizes_deletion"]) for t, a in answers.items()
            if isinstance(a.get("authorizes_deletion"), bool)}


def classify_texts(texts):
    """({text: True when it authorizes deletion}, set of texts the model decided). Rules decide the rest."""
    model = model_verdicts(texts)
    return {t: model.get(t, rule_verdict(t)) for t in set(texts)}, set(model)


def classify(text, case_id=None):
    """True when one request authorizes deletion."""
    return classify_texts([text])[0][text]
