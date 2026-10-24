@AGENTS.md

# CLAUDE.md — Ghost Data Still Exists (HackerRank Orchestrate, Oct 2026)

AGENTS.md (imported above) is the official contract: logging to `log.txt`, output format, allowed operations, evidence rules. Everything below adds our team rules and game plan on top of it. If the two ever conflict on the output contract, AGENTS.md wins.

## Team rules (always)

- **Authorship:** every commit is by `Anar Tuvshinbayar <anaridk56@gmail.com>` (set in this repo's local git config). No AI co-author lines, no "Generated with" footers, no AI credit anywhere in commits, PRs, code comments, README, or reports.
- **Nothing goes live without "go":** commit locally freely, but never push, open a PR, or submit anything until Anar says "go".
- **Short, plain answers.** Do exactly what's asked; if unclear, ask one short question.
- **Logging is mandatory:** append a `log.txt` entry for every turn exactly as AGENTS.md §5 says (it is the `chat_transcript` submission). Required fields there are not authorship credit.
- **No secrets:** API keys only from env vars / an uncommitted `.env`. Never in code, logs, reports, or commits.
- **No cheating:** predictions use only files under `dataset/`. Never hardcode case answers. `sample_output.csv` is for evaluation only, never a lookup table.

## Repo layout

```text
code/main.py                 entry point: python3 code/main.py -> writes ./output.csv
code/...                     our modules (see Architecture)
code/evaluation/main.py      scores our output vs dataset/sample_output.csv
code/evaluation/evaluation_report.md   required deliverable
code/evaluation/usage_report.md        required deliverable
dataset/                     challenge data (never edit, never zip)
output.csv                   final predictions (repo root)
log.txt                      agent transcript (repo root)
```

## Strategy: deterministic first, LLM only where it pays

Score comes from exact steps, not prose. A deterministic engine gets ordering, evidence IDs and policy right every time; LLMs are used only for fuzzy inputs, and their results are cached so the full run is cheap and reproducible (that also makes `usage_report.md` look good).

| Problem | Approach |
| --- | --- |
| Does `request_text` authorize deletion? (many languages; access / export / correction / marketing opt-out = `NOT_APPLICABLE`) | Small LLM classifier (`claude-haiku-5-5`), JSON output, cached by text hash; keyword fallback when no API key. `CLASSIFY_REQUEST` "non-deletion" activities are a strong cross-check. |
| PNG screenshots and PDFs in `evidence/images`, `evidence/documents` | Extract once into structured records (vision LLM or text extraction), cache to `code/cache/`, then treat like any other evidence. |
| SQL snapshots | Load each `.sql` into in-memory SQLite; read every table. |
| JSON records, logs, handover `.txt` | Plain parsers into one normalized observation table. |
| Identity, timeline, policy, steps | Pure Python, no LLM. |

## Architecture (pipeline)

1. **Load** all CSVs + evidence manifest. Parse every evidence file into normalized observations: `(evidence_id, captured_at, system_id, resource_id, data_category, identifiers{email, phone, name, email_hash, user_id}, state/fields)`.
2. **Identity resolution** per case: request email -> `user_id` via `user_identities.csv` and historical values in `user_audit_log.csv` (old emails, phones, names, hashes). Compare case-insensitively and trimmed. Also follow `LINK_RESOURCE_IDENTITY` activities.
3. **Discovery:** find every resource tied to the subject across activity + evidence (by user_id / customer_reference, any current or historical identifier, email hash, resource IDs that chain from those).
4. **State at `audit_at`:** use only evidence/activity with time <= `audit_at`. Latest observed state wins. `reported_status` and `reported_result` are claims; a "SUCCESS" delete that later evidence shows still present is still present. Watch for SYNC / RESTORE_BACKUP / replication re-creating data after a delete.
5. **Policy:** map each resource to a `data_category` and apply `deletion_rules.csv` (DELETE / REDACT / ANONYMIZE / RETAIN; `ACCOUNT_TOMBSTONE` keeps the `user_id` but no name/email/phone/email_hash).
6. **Steps + status:** `COMPLETE` -> verification steps only, each citing the evidence that proves it. `INCOMPLETE` -> findings (cite evidence), remediation (empty `evidence_id`), verification (empty `evidence_id`). `NOT_APPLICABLE` -> `[]`.
7. **Order:** phase (FINDING, REMEDIATION, VERIFICATION) -> operation order from the table in AGENTS.md §6.4 -> `system_id` -> `resource_id` (ascending). Renumber `step_no` from 1.
8. **Write** `output.csv`: 518 rows sorted by `case_id`, header `case_id,deletion_status,steps_json`, deterministic JSON.

## Data gotchas already spotted

- Decoy identities: near-identical emails (e.g. `g.silva76@...` vs `g.silva76.jr@...`) and UPPERCASE variants of real emails in evidence. Match exactly after lowercasing; never fuzzy-match emails.
- Request email can be a historical address — resolve through the audit log.
- `system_activity.csv` is global (4k rows, many unrelated users) and contains ~10 `data_catalog` rows that link resources to people.
- Evidence comes in many origins (primary, read/regional/reporting/failover replicas). Replication drift: a record gone from primary can still sit in a replica.
- The 32 samples contain no `NOT_APPLICABLE` cases, but the real 518 do. Don't overfit to samples.
- Resource IDs are not zero-padded consistently (`account_001` vs `account_7566`); sort as plain strings unless samples prove otherwise.

## Evaluation loop

- `python3 code/evaluation/main.py` compares our output on the 32 sample cases with `dataset/sample_output.csv`: case-status accuracy, step-level and field-level accuracy, sequence accuracy, plus a per-case diff for every miss.
- Add at least 3 custom test cases of our own (e.g. non-deletion request in another language, resurrection via sync after delete, tombstone with leftover email hash) and record them in `evaluation_report.md`.
- Every fix: re-run eval, note the failure + fix in `evaluation_report.md`.

## Commands

```bash
python3 code/main.py
python3 code/evaluation/main.py
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

## Submission checklist (only after Anar says "go")

- `output.csv`: exact header, 518 rows, sorted, valid JSON arrays, consecutive `step_no`.
- `code.zip`: code, prompts, our own `README.md` (setup + run), both evaluation reports; no dataset, no caches, no nested archives, no secrets.
- `log.txt` uploaded as `chat_transcript`; agent trace zipped per `agent_trace_instructions.md`.
- Submission link: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
