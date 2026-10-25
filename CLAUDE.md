@AGENTS.md

# CLAUDE.md — Ghost Data Still Exists (HackerRank Orchestrate, Oct 2026)

AGENTS.md above is the official contract (log.txt, output format, allowed operations, evidence rules). This file is our team playbook on top of it. If they ever disagree on the output contract, AGENTS.md wins.

## How we work in this repo

- **Author:** every commit is by `Anar Tuvshinbayar <anaridk56@gmail.com>` (repo-local git config, already set). No AI co-author lines, no "Generated with" footers, no AI credit anywhere: commits, code comments, README, reports. The `tool=` / `model=` lines in log.txt are required fields, not credit, keep them accurate.
- **Nothing goes live without "go":** commit locally as much as you like, never push, open a PR or submit until Anar says "go".
- **Log every turn** to `log.txt` exactly as AGENTS.md §5 says. It is the `chat_transcript` submission and judges read it.
- **Reply short.** Anar reads on a phone. A few lines of plain text, then this block at the very end of every reply:

  ```text
  STATUS
  did: what changed, one line
  numbers: samples status x/32 · step F1 0.xxx · exact x/32 | output: N INCOMPLETE / N COMPLETE / N NOT_APPLICABLE
  changed: files touched
  sketchy: anything uncertain, any assumption you made
  q: one question, or "none"
  ```

- **Don't ask, decide.** For local work make the sensible call, state it under `sketchy`, move on. Ask only when the answer changes the result, and ask one thing.
- **No secrets** in code, logs, reports, commits. API key only via env var or the uncommitted `.env`.
- **No cheating.** Predictions use only files under `dataset/`. Never hardcode a case answer. `sample_output.csv` is the scorer, never a lookup table. Never edit `dataset/`.

## Where things stand (2026-10-25)

The engine exists and works. Do not rebuild it, improve it.

| Thing | State |
| --- | --- |
| Sample score (32 labelled cases) | status 32/32 · step F1 0.990 · exact rows 29/32 · sequence 1.000 |
| `output.csv` | 518 rows, passes the structural checks, 355 INCOMPLETE / 153 COMPLETE / 10 NOT_APPLICABLE, 3,270 steps, run ≈ 2.5 s |
| LLM usage in the last run | gemini-3.5-flash-lite, all from cache: intent 20 calls (398 texts, rules agree on all 550 requests) + screenshots/PDFs 15 calls; 47,128 input / 25,503 output / 0 thinking tokens, $0.0779 at list price, $0 on the free tier |
| Reports | `code/evaluation/evaluation_report.md` and `usage_report.md` exist and are current |
| Account-link tiers (`risk_map.py`) | 238 identifier · 152 one job candidate · 90 fingerprint · 16 guess · 12 no job · 10 not deletion |
| Deadline | 2026-10-25 18:00 IST (12:30 UTC) |
| Ship check | clean clone and unzipped `code.zip` next to `dataset/`, both in `python:3.10-slim` with `--network none` and no `.env`: output.csv byte-identical, evaluator, custom cases and structural checks pass; `code.zip` 43 entries (37 files), no dataset/.venv/.env/archives/keys; final run 2026-10-25 03:36 UTC, output.csv unchanged |
| Remote | 5 commits on `origin/main`; later work is local until "go" |

The 3 sample misses (case_04, case_18, case_20) need record IDs that appear in no participant file. Don't chase them.

## Repo layout

```text
code/main.py                  python3 code/main.py  -> output.csv + usage_report.md   (--sample -> sample_predictions.csv)
code/audit/evidence.py        parsers: SQL snapshots (primary + replicas), 6 log formats, JSON bundles, record histories, handover txt -> observations
code/audit/media.py           screenshots + PDFs via cached Gemini readings in code/cache/media/<id>.json, focus record found through data_catalog links, state from the screen type
code/audit/identity.py        current + historical identifiers (user_identities.csv + user_audit_log.csv), lowercase emails, digit-only phones
code/audit/jobs.py            ties deletion-job activity to cases when records carry no identifier (timing fingerprints, see Risks)
code/audit/engine.py          state at audit_at -> policy (deletion_rules.csv) -> ordered steps
code/audit/intent.py, llm.py  request intent: Claude if ANTHROPIC_API_KEY, else Gemini (GEMINI_MODEL, default gemini-3.5-flash-lite; REST, urllib) if GEMINI_API_KEY, else rules; 20 texts per call, disk cache, tokens incl. thinking
code/evaluation/main.py       scorer vs dataset/sample_output.csv (--diff shows per-case misses)
code/evaluation/custom_cases.py   our own 6 cases
code/evaluation/structure.py  structural checks on any predictions file; main.py and the evaluator print them every run
code/evaluation/usage.py      writes usage_report.md from the run's token counters
code/tools/explain_case.py    everything the engine knows about one case; use it before guessing
code/tools/risk_map.py        tags every case by how its account was linked to a deletion job (confidence tiers)
code/tools/extract_media.py   reads each screenshot/PDF with Gemini (GEMINI_VISION_MODEL, default gemini-3.8-flash; committed readings by gemini-3.5-flash-lite) into code/cache/media/<id>.json; prints the screen-type vs model state cross-check
```

Stdlib-only Python 3.10+. `anthropic` is optional (`.venv/` exists); the Gemini path needs no package. Full run must stay offline-capable, deterministic, and fast.

## Facts about the data we have verified

- All 518 request emails resolve: 418 to a current email, 100 to a historical one in `user_audit_log.csv`. 850 users, 306 audit rows.
- `reported_status` mix: 254 COMPLETE, 152 PARTIALLY_COMPLETE, 112 NOT_STARTED. Claims only.
- Exactly 10 non-deletion requests (case_533–542, plain English: correction, access, export, marketing opt-out, login help). They match the 10 `CLASSIFY_REQUEST` rows in the activity stream. The 62 non-English requests (pt/es/fr/ja/ko, 5 templates) are all deletion requests.
- 94 evidence files: 24 SQL, 26 logs, 29 JSON/txt records, 11 PNG, 4 PDF. Sample answers cite only `evidence_*` file IDs, never activity or audit IDs. Our output cites 65 of the 94 files; most uncited ones are sample-only files.
- SQL snapshots hold `customer_profiles`, `customer_search_documents`, `customer_analytics_profiles`, `support_records`, `customer_exports`, `connector_subscriptions`, `backup_restore_entries` with `customer_reference` (= user_id), email, phone, email_hash. Primary and replica captures can disagree (replication drift).
- `deletion_worker` activity rows name only resource IDs, never the person. Ownership comes from evidence contents, 12 `data_catalog` LINK_RESOURCE_IDENTITY rows, or job timing.
- Decoys: near-identical emails (`g.silva76@` vs `g.silva76.jr@`), uppercase variants. Match exactly after lowercasing, never fuzzy.
- Ownership is time-invariant (a capture after `audit_at` may prove who owns a record) but state comes only from captures at or before `audit_at`.

## Risks, in priority order

1. **Job attribution (`jobs.py`)** links records to cases by second-level timing fingerprints of the deletion job. It is the cleverest and the shakiest part. `python3 code/tools/risk_map.py` tags every case by how its account was linked (counts above). 16 cases in 8 batches are still a guess: no evidence file names their account and no proven job activity separates the candidates, so it is a coin flip worth one or two steps each. No sample case reaches the guess path. 4 incomplete cases (case_52, 53, 61, 63) have no account step because nothing names one.
2. **The models confirm more than they decide.** gemini-3.5-flash-lite classifies intent (the rules agree on every request) and read all 15 screenshots/PDFs (observations identical to the old OCR ones). Its own state for a screen's focus record differs from our screen-type rule on 3 of 14: evidence_18 (customer statement read as an order/finance record), evidence_42 (retained reference read as PRESENT/OTHER, rule says REDACTED tombstone), evidence_58 (support_001 read as an identifiable support message, rule says attachment). The rule still decides; those 3 are worth a look before trusting the model's state. Free tier: 20 requests a day per model, failed attempts count; gemini-3.8-flash timed out or returned 503 on most calls on 2026-10-24.
3. **Portability.** Judges run `python3 code/main.py` on Linux. Nothing may depend on macOS, `swift`, `pdftotext` or network at run time. Since the switch to Gemini readings nothing does; the swift OCR tool and the OCR text are gone.
4. **Explainability.** Anar explains this live in the AI interview. Keep modules small, names plain, docstrings honest. Prefer a simpler rule over a cleverer one at equal score.
5. **Reports** must match the final run exactly (numbers, status mix, token counts).

## Hard rules for the engine

- Findings and COMPLETE verifications cite the newest capture at or before `audit_at` that proves the state. Remediation and INCOMPLETE verifications use `""`.
- Never cite a file captured after the case's `audit_at`.
- COMPLETE only when every record has its required treatment, retained records are present, no flow or restore path is active.
- Order: phase → operation order from AGENTS.md §6.4 → `system_id` → `resource_id` (numeric-aware). Renumber from 1.
- Every run must pass the structural checks (header, 518 sorted rows, valid JSON, consecutive step_no, allowed operation/state pairs, evidence time rule). They live in `code/evaluation/structure.py`; `main.py` and the evaluator print them and exit non-zero on failure.
- After every engine change: `python3 code/evaluation/main.py --diff`, then `python3 code/evaluation/custom_cases.py`, then `python3 code/main.py`. Add a row to the "Failures found and improvements made" table in `evaluation_report.md` for every real fix.

## Commands

```bash
python3 code/main.py                        # 518 rows -> output.csv, rewrites usage_report.md
python3 code/evaluation/main.py --diff      # sample score + per-case misses
python3 code/evaluation/custom_cases.py     # our own cases
python3 code/tools/explain_case.py case_35  # one case, all evidence
python3 code/tools/risk_map.py              # account-link tier per case, list of guessed cases
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

## Submission checklist (only when Anar says "go")

- `output.csv`: header `case_id,deletion_status,steps_json`, 518 rows sorted by case_id, valid JSON, consecutive step_no.
- `code.zip`: code, prompts, README, both reports in `code/evaluation/`, and both model caches: `code/cache/llm_cache.json` (intent answers) and `code/cache/media/*.json` (screenshot/PDF readings), because offline runs replay the model's answers from them. No dataset, no `.venv`, no other caches, no nested archives, no secrets.
- `log.txt` uploaded as `chat_transcript`; `agent-trace.zip` built per `agent_trace_instructions.md`.
- Submission link: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
