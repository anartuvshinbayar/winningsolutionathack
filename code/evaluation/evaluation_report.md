# Evaluation report

Commands:

```bash
python3 code/evaluation/main.py --diff     # runs the agent on the 32 labelled samples and scores it
python3 code/evaluation/custom_cases.py    # our own cases (below)
```

## Metrics on the 32 labelled sample cases

| Metric | Score | How it is computed |
| --- | ---: | --- |
| Case-level status accuracy | **1.000** (32/32) | `deletion_status` matches |
| Exact row accuracy | **0.906** (29/32) | status and the whole `steps_json` array identical |
| Step precision | **1.000** | steps compared as (phase, system_id, resource_id, operation, state, evidence_id); order ignored |
| Step recall | **0.979** | same, over the expected steps |
| Step F1 | **0.990** | |
| Field accuracy (all fields) | **0.967** | positional comparison of every field of every step, over the longer array |
| Sequence accuracy | **1.000** (29/29) | among cases whose step multiset matches, the array order also matches |

Field-level accuracy (positional):

| step_no | phase | system_id | resource_id | operation | state | evidence_id |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.979 | 0.979 | 0.959 | 0.959 | 0.959 | 0.959 | 0.979 |

All 518 evaluation rows pass the structural checks: exact header, sorted by `case_id`, valid JSON arrays,
consecutive `step_no` from 1, findings and `COMPLETE` verifications cite evidence captured at or before
`audit_at`, remediation and post-remediation verification use an empty `evidence_id`.

Output mix on the 518 evaluation requests: 355 INCOMPLETE, 153 COMPLETE, 10 NOT_APPLICABLE (3,265 steps).

### The three remaining sample misses

| Case | Missing step | Why |
| --- | --- | --- |
| case_04 | `account_004` absent, `analytics_901` anonymized | Hand-written sample. No activity row or capture links either record to the subject; the expected answer relies on the `account_00N` = `user_0N` naming of the hand-written samples, which we do not hard-code. |
| case_18 | `audit_record_6645` present | The record id appears in no participant file at all. |
| case_20 | `audit_record_5569` present | Same: not present in any participant file. |

None of them can be recovered from participant-visible data without hard-coding labels.

## Custom evaluation cases

Defined in `code/evaluation/custom_cases.py`. Each reuses real evidence, changes one field of a request (or
writes a new request text), and states the expected status plus steps that must and must not appear.

| # | Case | What it tests | Expected | Result |
| --- | --- | --- | --- | --- |
| C1 | case_34 filed with Haruka's old address `haruka.s183889@zoho.com` | Historical identifiers from `user_audit_log.csv` | INCOMPLETE; `account_5762` remaining (evidence_04 screenshot), attachment `support_9538` remaining (evidence_01), `audit_record_3753` kept | PASS |
| C2 | case_53 filed as `Kagyeman8947@FastMail.com` | Identifier normalisation | INCOMPLETE; `support_1034` exposed PII (evidence_28 screenshot) | PASS |
| C3 | case_121 as filed | A search entry seen only in a capture two days after `audit_at` must not change the answer | COMPLETE with `account_081` absent and `audit_record_2103` present, both from evidence_88; no finding for `search_3573` | PASS |
| C4 | case_121 with `audit_at` moved to 2026-08-25 | The same capture is now admissible | INCOMPLETE; `search_3573` remaining, cited to evidence_11 | PASS |
| C5 | "Não quero excluir a conta. Só preciso corrigir o número de telefone." | Negated deletion verb in Portuguese | NOT_APPLICABLE | PASS (after fix 6) |
| C6 | "Necesito una copia de mis datos personales. No quiero eliminar mi cuenta." | Spanish access request naming deletion | NOT_APPLICABLE | PASS (after fix 6) |

## Failures found and improvements made

Each row is one iteration of: run the evaluator, read the per-case diff, find the cause in the evidence,
change the engine, re-run. Scores are on the 32 samples.

| # | Failure found | Fix | Step F1 | Exact rows |
| --- | --- | --- | ---: | ---: |
| 0 | First end-to-end run: deleted accounts (404 lookups carry no email) and anonymized analytics rows were never linked to the subject | (baseline) | 0.871 | 0.25 |
| 1 | Records linked only by captures taken after `audit_at` were dropped entirely, losing the subject's records | Ownership is time-invariant: link records using every capture, but read state only from captures at or before `audit_at` | 0.877 | 0.25 |
| 2 | 262 of 518 evaluation cases had no linkable account. The activity stream shows each case's deletion job: the profile submission 44-75 min before `reported_at`, whose result mirrors `reported_status` (269/271 proven pairs), and later actions on the same job clock | Job attribution (`code/audit/jobs.py`): batch cases by `reported_at`, fix identifier-proven links, eliminate, then match unlabelled actions by the job's second-offset fingerprint | 0.956 | 0.75 |
| 3 | Two cases in the same batch matched one action; a person with two requests months apart got both requests' records; deleted-account evidence for hand-written samples cited nothing | Pre/post-report fingerprints, case scoping by capture time for repeat requesters, snapshot-absence fallback citation | 0.974 | 0.875 |
| 4 | Legacy contact-index rows (support replica) were treated as support messages to redact | Classified as `SECONDARY_IDENTIFIER` (delete) | 0.990 | 0.906 |
| 5 | Connector selectors stored inside SQL JSON rows were not used for linking; primary/replica rows captured together picked an arbitrary copy; adjacent batch cases one second apart collided | Selector keys for every record shape; replica ties resolved toward the copy that still exposes data (replication drift); nearest-clock attribution with fitted slot centres (unattributed job actions: 6 -> 2) | 0.990 | 0.906 |
| 6 | Custom cases C5/C6 failed: the offline rules saw the deletion verb and missed the refusal | Multilingual keep/refusal patterns for pt/es/fr/ja/ko; Claude classifies intent first when available, with the rules as fallback | 0.990 | 0.906 |

Checks made on the 518 evaluation cases without labels (by reading `code/tools/explain_case.py` output for
one or more cases of every evidence family: handover note, replica SQL, inventory SQL + lookup trace,
snapshot bundles, observability logs, record histories, screenshots, PDFs, tombstones, non-deletion
requests): activity checks (`CHECK_DATA_FLOW`, `CHECK_RESTORE_ACCESS`, connector and restore
repopulation) never contradict newer file evidence; every `COMPLETE` verification cites evidence; no cited
file was captured after its case's `audit_at`.

## Known limits

- Records that appear in no participant file (case_18, case_20) cannot be listed.
- When two cases in one batch have identical job fingerprints, the action stays unattributed rather than
  guessed (2 actions in the whole stream, one of them a hand-written sample).
- Screenshot text comes from local OCR; each screenshot's focus record is identified through the data
  catalog link, so OCR noise does not reach the answer.
