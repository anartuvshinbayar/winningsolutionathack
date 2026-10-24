# Ghost Data Still Exists: deletion-audit agent

Solution for HackerRank Orchestrate (October 2026). For every request in `dataset/deletion_requests.csv`
the agent decides whether deletion was really requested, reconstructs who the person is (current and past
identifiers), finds every copy of their data across 94 evidence files and the activity stream, works out the
true state of each record at `audit_at`, applies `deletion_rules.csv`, and writes the ordered findings,
remediation and verification steps to `output.csv`.

## Run

Requirements: Python 3.10+ (standard library only). Optional: let a model classify request intent. Without
a key the multilingual rules make the same decision offline; with one they cross-check every model verdict and
the run prints any disagreement.

Gemini (a free key from Google AI Studio works; plain REST through `urllib`, no extra packages):

```bash
echo "GEMINI_API_KEY=..." >> .env      # uncommitted; read at startup, never overrides the environment
python3 code/main.py
```

Claude takes priority when `ANTHROPIC_API_KEY` is set, and needs the SDK:

```bash
python3 -m venv .venv && .venv/bin/pip install anthropic
echo "ANTHROPIC_API_KEY=..." >> .env
.venv/bin/python code/main.py
```

`GEMINI_MODEL` and `AUDIT_MODEL` override the default models (`gemini-2.5-flash`, `claude-opus-5-5`), and
`AUDIT_OFFLINE=1` forces the rules. Requests go 20 distinct texts per call, and a 429 is retried with the
server's suggested delay. Answers are cached in `code/cache/llm_cache.json`, so a re-run, or a later run
without a key, reuses them and spends nothing.

```bash
python3 code/main.py                        # writes output.csv (518 rows) and code/evaluation/usage_report.md
python3 code/evaluation/main.py --diff      # scores the agent on the 32 labelled samples
python3 code/evaluation/custom_cases.py     # our own evaluation cases
python3 code/evaluation/structure.py        # structural checks on output.csv (main.py and the evaluator print them too)
python3 code/tools/explain_case.py case_34  # everything the engine knows about one case
python3 code/tools/risk_map.py              # how each case's account was linked, per confidence tier
```

A full run takes about 3 seconds. Screenshot and PDF text is cached in `code/cache/media/`; to rebuild it
run `python3 code/tools/extract_media.py --force` (macOS Vision OCR via `swift`, and `pdftotext`).

## How it works

```text
evidence files ──► parsers ──► one observation table (who / what / state / when / which file)
                                        │
request ──► intent (model + rules) ──► identity (current + historical emails, phones, hashes, user_id)
                                        │
activity stream ──► job attribution ────┤   (links records that no longer carry any identifier)
                                        ▼
                  state at audit_at ──► policy (deletion_rules.csv) ──► ordered steps ──► output.csv
```

| Module | Job |
| --- | --- |
| `code/audit/evidence.py` | Parses SQLite snapshots (incl. primary vs replica tables), six log formats (joined by trace id so lookup keys reach their result lines), snapshot bundles, service exports, record histories and the handover note into observations |
| `code/audit/media.py` | Screenshots and PDFs: reads the cached text and identifies each screen's focus record through the data catalog's link activity |
| `code/audit/identity.py` | Current and historical identifiers per user from `user_identities.csv` + `user_audit_log.csv`; case-insensitive emails, digit-normalised phones |
| `code/audit/jobs.py` | Ties deletion-job activity to cases: the profile submission sits 44-75 min before `reported_at` and its result mirrors `reported_status`; later actions of the same job share its clock (fixed second offsets). Solves each batch of cases jointly |
| `code/audit/engine.py` | Resolves each record's state from the newest admissible capture (replica copies that still expose data win ties), applies the policy, and plans findings → remediation → verification in the required order |
| `code/audit/intent.py`, `code/audit/llm.py` | Request intent: Claude (`claude-opus-5-5`) or Gemini (`gemini-2.5-flash`, REST via `urllib`), 20 texts per JSON-schema call, disk-cached, tokens incl. thinking and cost tracked; multilingual rules as fallback and cross-check |

Rules the engine follows:

- Reported statuses and activity results are claims; state comes from captures at or before `audit_at`.
  Captures taken later never change the answer (they may still prove who owns a record).
- `COMPLETE` only when every record has its required treatment, retained records are present, and no data
  flow or restore path is active. Records with no admissible capture produce no finding.
- Remediation disables flows and blocks restores before changing records. Findings and `COMPLETE`
  verifications cite the newest capture proving the state; future steps use an empty `evidence_id`.

## Evaluation

On the 32 labelled samples: status accuracy 1.000, step F1 0.990, exact rows 29/32, sequence accuracy
1.000. The three misses need records that appear in no participant file. Details, custom cases and the
iteration log are in [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md);
token usage and cost in [`code/evaluation/usage_report.md`](code/evaluation/usage_report.md).

## Package

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```
