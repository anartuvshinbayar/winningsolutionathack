# Interview notes: Ghost Data Still Exists

Plain notes for the 30-minute AI interview. Every number here comes from the code and outputs as of commit
`ca7c712` (`output.csv` sha256 `61a5ea48…`).

## 1. What the agent does, end to end

A deletion workflow says "done", but copies of the person's data may still sit in databases, replicas, logs,
search indexes, exports, backups and support tools. For each of the 518 requests the agent checks what is
really true at the request's `audit_at` time and writes the steps to fix it.

1. **Intent.** Read the request text (English, Portuguese, Spanish, French, Japanese, Korean) and decide if
   it actually asks for deletion. 10 requests do not (access, export, correction, opt-out, login help):
   `NOT_APPLICABLE` with no steps.
2. **Identity.** Turn the request email into a person (`user_id`) and collect every identifier that person
   has ever had: current and old emails, phones, email hashes.
3. **Evidence.** Parse all 94 evidence files into one table of observations. Each row says: this file, at
   this time, saw this record in this system, in this state, with these identifiers.
   24 SQL snapshots (primary and replica tables), 26 logs in six formats, 28 JSON exports, 1 handover note,
   11 screenshots and 4 PDFs.
4. **Link records to the person.** By identifiers on the record, by the data catalog's link rows, and, for
   records that carry no identifier at all, by tying the deletion job's activity to the case.
5. **State at `audit_at`.** For each record, the newest capture taken at or before `audit_at` decides.
6. **Policy.** `deletion_rules.csv` gives the required end state per category: delete, redact, anonymize,
   retain, block restore, disable the data flow.
7. **Steps.** Every gap becomes a finding, a remediation and a verification, in a fixed order. No gap
   means `COMPLETE` with evidence-backed verifications only.

Result: 355 `INCOMPLETE`, 153 `COMPLETE`, 10 `NOT_APPLICABLE`, 3,270 steps, about 2.5 seconds.

The workflow's own claims are often wrong. Of the 254 requests reported `COMPLETE`, 163 are incomplete.
Of the 112 reported `NOT_STARTED`, 26 are in fact complete.

## 2. Why the core is deterministic, and where the model is used

**The core is plain Python on purpose.**

- Judges run `python3 code/main.py` offline on Linux. The output must be the same every time, byte for byte.
- An audit must be explainable. Every finding points to one file, and every decision is a rule I can
  show in the code.
- The evidence is structured (SQL, logs, JSON). Parsers read it exactly; a model can misread an id or
  quietly use a file captured after `audit_at`.

**The model is used only where the input is human language or an image.**

| Use | Model | Calls | Why a model | Guard rail |
| --- | --- | ---: | --- | --- |
| Request intent | `gemini-3.5-flash-lite` | 20 (up to 20 distinct texts each, 398 in all) | Free text in six languages, negations like "do not delete" | Multilingual rules cross-check every verdict; they agree on all 550 requests |
| Screenshots and PDFs | `gemini-3.5-flash-lite` | 15 (one per file, sent inline) | Images and PDFs have no structure to parse | The screen's focus record is found through the data catalog; its state comes from the screen-type rule. The model's own state is only a cross-check |

- Both use a JSON schema, so the answer is structured, not prose.
- Every answer is cached on disk with the token usage of its call (`code/cache/llm_cache.json`,
  `code/cache/media/*.json`). A run without a key or network replays them and gets the same output.
- Without a key and without the cache, the rules decide intent on their own.

**Why that model.** `gemini-2.5-flash` refused new keys (HTTP 404). `gemini-3.8-flash` timed out or
returned 503 on most calls, and its free tier allows only 20 requests a day. `gemini-3.5-flash-lite` did all
35 calls without a single retry. Settings follow Google's Gemini 3 guidance: thinking level low, default
temperature.

**One real fix from the model.** gemini-3.8-flash called case_28 ("Can you verify that my account was
deleted?") not a deletion request. The sample answer says it is. I added one sentence to the prompt:
asking to confirm a deletion is a deletion request.

## 3. Identity resolution with the audit log

- `user_identities.csv` is the current snapshot: 850 people, each with `user_id`, name, email, phone and
  email hash.
- `user_audit_log.csv` has 306 past changes (107 email, 107 email hash, 92 phone). For each change I add
  both the old and the new value to that person's identifiers.
- The request email can be an old one: 418 requests use a current email, 100 a historical one. All 518
  resolve to a person.
- Matching is exact after normalising: emails and hashes lower-cased, phones reduced to digits. No fuzzy
  matching, because the data has decoys like `g.silva76@gmail.com` next to `g.silva76.jr@gmail.com`.
  No email belongs to two different people over time.
- A record belongs to a person when any identifier on it matches any of theirs. Ownership does not depend
  on time, so a capture taken after `audit_at` may still prove whose record it is. It can never prove the
  record's state.
- A person with two requests: each record goes to the first request audited after the capture that
  linked it.

## 4. Job attribution, and why it is needed

**The problem.** A deleted account leaves nothing to match: the lookup returns 404 with no email. An
anonymized analytics row has no identifiers by definition. The deletion worker's activity rows name only
resource ids, never the person. In 258 of the 508 deletion cases the account is linked only through the
deletion job, so without this step those cases would miss their account, and often more.

**What the activity stream shows.**

- Every deletion job submits the person's primary profile (`deletion_worker DELETE_RECORD` on
  `account_service`) 44 to 75 minutes before the workflow reports its status.
- That submission's result mirrors the reported status: COMPLETE -> SUCCESS, PARTIALLY_COMPLETE -> PARTIAL,
  NOT_STARTED -> FAILED.
- Every later action of the same job runs on the job's clock: its offset from the submission is a whole
  number of half-minutes plus 10 or 21 seconds (within 1 second). After the status report, the callbacks
  also keep their position: the k-th one sits (51.9 + 29.5(k-1)) mod 60 seconds after the submission.
  Both formulas are fitted on pairs that identifiers already prove, never on the labels.

**How it decides.** Cases with the same `reported_at` form one batch (1 to 5 cases). For each batch:

1. A submission on an account an identifier already proves is fixed for that case.
2. Otherwise the candidates are unowned submissions in the time window whose result matches the
   reported status. If one is left, or eliminating the fixed ones leaves one, that is the account.
3. If several are left, compare each candidate's clock with the person's proven job activity. The one
   that explains most of it wins: the half-minute clock first, the callback position as the tie-break.
4. If still tied, take the submission closest to the usual 59-minute lead (3540 s). This is a guess.

Job actions on records with no identifier (anonymize, redact, connector and restore checks) then go to
the case whose submission clock they share: 127 actions. Two exact ties stay unattributed.

**How much rests on it** (`python3 code/tools/risk_map.py`):

| Tier | How the account is linked | Cases | Samples exact |
| --- | --- | ---: | ---: |
| IDENTIFIER | the job submitted an account that evidence ties to the person | 238 | 14/14 |
| SINGLE | time window, status and elimination leave one submission | 152 | 12/14 (2 misses unrelated) |
| FINGERPRINT | the job clock picks between several | 90 | 3/3 |
| GUESS | still tied, closest to 59 minutes | 16 | no sample |
| NONE | no deletion job in the stream, identifiers only | 12 | 0/1 (case_04) |
| NON_DELETION | not a deletion request | 10 | none |

Check with the answer hidden: on 50 proven cases with more than one candidate, the clock picks the right
account 48 times, ties twice, and is never wrong.

## 5. State at `audit_at`, and the evidence citation rule

**Which captures count.** An observation counts only if its file was captured at or before `audit_at`
and the observation itself is not later. The newest such observation decides the record's state. Copies
captured together (primary and replica tables in one snapshot) can disagree; the copy that still exposes
data wins, because replication drift means the data still exists somewhere.

**Records seen only later.** A record that only captures after `audit_at` know about produces no
finding: there is no admissible evidence of a violation. Three exceptions, each backed by the samples:

- records the deletion job touched (the job proves they belong to the case),
- records policy says to retain (they must be verified present),
- the person's own account in an incomplete case: it still gets `VERIFY_RECORD_ABSENT` with empty
  evidence, because all 22 incomplete sample answers verify the account.

**Citation rule.**

- A finding cites the newest capture at or before `audit_at` that shows the problem.
- A verification in a `COMPLETE` case cites the newest capture that proves the compliant state. If a
  record has no capture of its own, it cites the case's most-cited file, or the latest snapshot of that
  system before `audit_at` that no longer contains it.
- Remediation steps, and verifications in an `INCOMPLETE` case, are future work, so `evidence_id` is `""`.
- No file captured after `audit_at` is ever cited. The structural checks enforce this on every run.

## 6. How policy maps to steps, and the order

`deletion_rules.csv` (15 categories) gives each category a required end state:

| Required end state | Finding | Remediation | Verification |
| --- | --- | --- | --- |
| deleted: profile, secondary identifier, search entry, cache, attachment, export, marketing profile | `REPORT_REMAINING_RECORD` | `DELETE_RECORD` | `VERIFY_RECORD_ABSENT` |
| redacted: support message, email delivery log, account tombstone | `REPORT_EXPOSED_PII` | `REDACT_RECORD` | `VERIFY_RECORD_REDACTED` |
| anonymized: analytics profile, product telemetry | `REPORT_EXPOSED_PII` | `ANONYMIZE_RECORD` | `VERIFY_RECORD_ANONYMIZED` |
| backup: policy keeps it, so its restore path must be blocked | `REPORT_RESTORE_RISK` | `BLOCK_RESTORE` | `VERIFY_RESTORE_BLOCKED` |
| data flow: not in the policy file; the engine adds it because an active sync recreates data | `REPORT_ACTIVE_DATA_FLOW` | `DISABLE_DATA_FLOW` | `VERIFY_DATA_FLOW_DISABLED` |
| retained: security audit log, legal hold | none | none | `VERIFY_RECORD_PRESENT` |

- A record already in its required state gets only its verification. An account tombstone keeps the
  stable `user_id` and must have no name, email, phone or hash: that is "redacted".
- `COMPLETE` only when every record is compliant, retained records are present, and no data flow or
  restore path is active. Then the case has verification steps only, each citing evidence.
- Order: phase (findings, then remediation, then verification); within a phase the operation order from
  the contract; then `system_id`; then `resource_id` with numbers compared as numbers; renumbered from 1.
  So remediation stops data flows and blocks restores before deleting or redacting anything, otherwise a
  sync or restore could bring the data straight back.

## 7. How we evaluated

**Sample metrics** (32 labelled cases, `python3 code/evaluation/main.py --diff`):

| Metric | Score |
| --- | --- |
| Status accuracy | 1.000 (32/32) |
| Exact rows | 0.906 (29/32) |
| Step precision / recall / F1 | 1.000 / 0.979 / 0.990 |
| Field accuracy | 0.967 |
| Sequence accuracy | 1.000 (29/29) |

The failures table in the evaluation report has 10 iterations, taking step F1 from 0.871 to 0.990.

**Custom cases** (`python3 code/evaluation/custom_cases.py`, 6/6 pass): an old email from the audit log;
a mixed-case email; evidence captured after `audit_at` is ignored; the same case audited later, when that
evidence becomes admissible; a Portuguese correction that mentions deletion; a Spanish export request.

**Structural checks**, printed by `main.py` and the evaluator on every run, non-zero exit on failure:
exact header, one row per request sorted by `case_id`, valid JSON, consecutive `step_no`, phases in order,
only allowed operation/state pairs, `COMPLETE` cases hold only verifications, cited evidence exists and
predates `audit_at`, future steps have empty evidence.

**Confidence tiers**: the table in section 4, so it is clear which answers rest on identifiers and which
on timing.

**Model vs rule on screenshots**: they agree on 11 of 14 focus records. The rule holds on the other 3:
the tombstone screen shows only `user_17`, so it is redacted; `support_001` is the attachment that sample
case_07 deletes; `export_1842` is linked in `export_service`, so it is a user export.

**Clean-room run**: a fresh clone in Docker `python:3.10-slim` with `--network none` and no `.env`:
`output.csv` byte-identical to the committed one, and the evaluator, custom cases and structural checks
all pass. The same again from `code.zip` unzipped next to a copy of `dataset/`, the way the judges run it.

## 8. Tokens and cost

| Use | Calls | Input | Output | Thinking | Total | List price |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Request intent | 20 | 25,640 | 16,727 | 0 | 42,367 | $0.0495 |
| Screenshots and PDFs | 15 | 21,488 | 8,776 | 0 | 30,264 | $0.0284 |
| **Total** | **35** | **47,128** | **25,503** | **0** | **72,631** | **$0.0779** |

- About 140 tokens and $0.00015 per request at list price ($0.30 in / $2.50 out per million tokens).
- Billed: $0, on the Gemini API free tier.
- A normal run costs nothing, because it replays the cache.

## 9. Known limits

- **16 guessed accounts** (8 batches of 2). No evidence file names their account, and no proven job
  activity separates the two candidates. It is a coin flip worth one or two steps per case. No sample case
  reaches this path, so it has never been scored.
- **5 cases with no account step**: case_45 (`COMPLETE`) and case_52, case_53, case_61, case_63
  (`INCOMPLETE`). No capture at any time and no deletion job names an account for them.
- **14 late-seen search rows.** Search entries seen only in captures after `audit_at` (11 in incomplete
  cases, 3 in complete ones) are left out. The samples back adding the late-seen account, not search rows.
  If graders expect them, it costs a verification step each.
- **3 sample misses.** case_04 needs `account_004` and `analytics_901`, which no file links to the person;
  case_18 and case_20 need audit records that appear in no participant file. Getting them would need
  hard-coded labels.

## 10. Likely judge questions

**Why not let an LLM do the whole audit?**
The answer must be reproducible, offline and traceable to a file, and the evidence is structured. Parsers
are exact and cost nothing. The model is used where language or images need it, and its answers are
cached and cross-checked.

**How do you know a reported COMPLETE is wrong?**
Reported statuses and activity results are treated as claims. State comes only from captures at or before
`audit_at`. 163 of 254 "COMPLETE" claims turn out to be incomplete.

**How do you handle old emails and phones?**
Every old and new value in `user_audit_log.csv` becomes one of the person's identifiers. 100 requests use
a historical email and all resolve. Matching is exact after normalising, which defeats near-identical decoys.

**Isn't job attribution overfitting to how the data was generated?**
The timing rules are fitted only on identifier-proven pairs, never on labels, and tested by hiding the answer:
48 of 50 right, 0 wrong. `risk_map.py` shows exactly which cases depend on it and how.

**How sure are you about the 16 guessed cases?**
Not sure: each is a coin flip between two accounts, and I say so in the report. The cost is one or two steps
per case, and the status rarely changes, because the job's actions move with its submission.

**How do you make sure nothing after `audit_at` is used?**
Every observation carries its file's capture time. Only captures at or before `audit_at` decide state, and
the structural checks fail the run if any cited file is later. Custom cases C3 and C4 test exactly this.

**What if a replica disagrees with the primary?**
Copies captured together are compared, and the one that still exposes data wins. If any copy still has the
data, deletion is not complete.

**What is an account tombstone and how do you treat it?**
A kept record with only the stable internal `user_id`, so retained records stay linked. Policy says redact:
name, email, phone and hash must be gone. If any remain, it is `REPORT_EXPOSED_PII` and `REDACT_RECORD`.

**Why that remediation order?**
An active sync or an open restore path can recreate data you just deleted. So data flows are disabled and
restores blocked first, then records are deleted, redacted or anonymized, then everything is verified.

**What happens without an API key or network?**
The same output: model answers replay from `code/cache/`. With no cache either, the rules decide intent,
and they agree with the model on all 550 requests.

**What does it cost?**
35 model calls, 72,631 tokens, $0.0779 at list price and $0 on the free tier. Re-runs cost nothing.

**Why gemini-3.5-flash-lite and not a bigger model?**
2.5 Flash refused new keys, and 3.8 Flash was overloaded and capped at 20 requests a day. Flash-lite did all
35 calls with no retries, and its answers agree with the rules and the samples.

**How do you trust the screenshot readings?**
The model only lists what is visible. The focus record is found through the data catalog, and its state
comes from the screen type. Observations are identical to the old OCR-based ones on all 15 files.

**How did you avoid cheating?**
Only `dataset/` is read. `sample_output.csv` is used only to score, and no case id or answer is in the code.
Every rule is stated in the code and the report.

**What would you do with more time?**
Find evidence that separates the 16 ties. Ask the organisers whether late-seen search rows should be
verified. Let the model's screen state decide once it agrees with the rule on all 14 records.
