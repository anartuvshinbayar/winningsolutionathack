# Ghost Data Still Exists: deletion-audit agent

Solution for HackerRank Orchestrate (October 2026). For every request in `dataset/deletion_requests.csv`
the agent decides whether deletion was really requested, reconstructs who the person is (current and past
identifiers), finds every copy of their data across 94 evidence files and the activity stream, works out the
true state of each record at `audit_at`, applies `deletion_rules.csv`, and writes the ordered findings,
remediation and verification steps to `output.csv`.

## Run

From the folder that holds `code/` and `dataset/` (for example `code.zip` unzipped next to `dataset/`):

```bash
python3 code/main.py
```

That writes `output.csv` (518 rows) next to `code/` in about 3 seconds. It needs Python 3.10 or newer and
nothing else: no packages, no API key, no network. Model answers (request intent, screenshot and PDF readings)
are replayed from `code/cache/`. Verified on Python 3.10 (`python:3.10-slim` in Docker with `--network none`
and no `.env`, output byte-identical) and on Python 3.14.

### With a model key (optional)

With a key, a model classifies request intent; the multilingual rules then cross-check every model verdict and
the run prints any disagreement. Without a key the rules decide, or cached answers are reused.

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

`GEMINI_MODEL` and `AUDIT_MODEL` override the default models (`gemini-3.5-flash-lite`, `claude-opus-5-5`), and
`AUDIT_OFFLINE=1` forces the rules. Requests go 20 distinct texts per call; a 429, a 5xx or a timeout is
retried with backoff (the server's suggested delay when it gives one). Answers are cached in
`code/cache/llm_cache.json`, so a re-run, or a later run without a key, reuses them and spends nothing.

### Other commands

```bash
python3 code/main.py                        # writes output.csv (518 rows) and code/evaluation/usage_report.md
python3 code/evaluation/main.py --diff      # scores the agent on the 32 labelled samples
python3 code/evaluation/custom_cases.py     # our own evaluation cases
python3 code/evaluation/structure.py        # structural checks on output.csv (main.py and the evaluator print them too)
python3 code/tools/explain_case.py case_34  # everything the engine knows about one case
python3 code/tools/risk_map.py              # how each case's account was linked, per confidence tier
```

Screenshots and PDFs are read once by Gemini (file sent inline, JSON schema) with
`python3 code/tools/extract_media.py` (needs `GEMINI_API_KEY`; `--force` reads every file again), which writes
`code/cache/media/<evidence_id>.json` with each call's token usage. The committed readings of all 15 files
were made with `GEMINI_VISION_MODEL=gemini-3.5-flash-lite`; the default `gemini-3.8-flash` kept timing out on
the free tier. Nothing in the pipeline needs macOS or a local OCR tool.

## How it works

```text
evidence files ──► parsers ──► one observation table (who / what / state / when / which file)
  (screenshots and PDFs: cached Gemini readings)
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
| `code/audit/media.py` | Screenshots and PDFs: reads Gemini's cached JSON reading of each file (every visible record with ids, identifiers, state), identifies the screen's focus record through the data catalog's link activity, and takes its state from the screen type; the model's own state is a cross-check |
| `code/audit/identity.py` | Current and historical identifiers per user from `user_identities.csv` + `user_audit_log.csv`; case-insensitive emails, digit-normalised phones |
| `code/audit/jobs.py` | Ties deletion-job activity to cases: the profile submission sits 44-75 min before `reported_at` and its result mirrors `reported_status`; later actions of the same job share its clock (fixed second offsets). Solves each batch of cases jointly |
| `code/audit/engine.py` | Resolves each record's state from the newest admissible capture (replica copies that still expose data win ties), applies the policy, and plans findings → remediation → verification in the required order |
| `code/audit/intent.py`, `code/audit/llm.py` | Request intent: Claude (`claude-opus-5-5`) or Gemini (`gemini-3.5-flash-lite`, REST via `urllib`, thinking level low, default temperature), 20 texts per JSON-schema call, disk-cached, tokens incl. thinking and cost tracked; multilingual rules as fallback and cross-check |

Rules the engine follows:

- Reported statuses and activity results are claims; state comes from captures at or before `audit_at`.
  Captures taken later never change the answer (they may still prove who owns a record).
- `COMPLETE` only when every record has its required treatment, retained records are present, and no data
  flow or restore path is active. Records with no admissible capture produce no finding (an incomplete case
  still verifies the person's account).
- Remediation disables flows and blocks restores before changing records. Findings and `COMPLETE`
  verifications cite the newest capture proving the state; future steps use an empty `evidence_id`.

## Evaluation

On the 32 labelled samples: status accuracy 1.000, step F1 0.990, exact rows 29/32, sequence accuracy
1.000. The three misses need records that appear in no participant file. Details, confidence tiers, custom
cases, the model-vs-rule cross-check and the iteration log are in [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md);
token usage and cost in [`code/evaluation/usage_report.md`](code/evaluation/usage_report.md).

## Package

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

`code.zip` holds this README and `code/`: the engine, prompts, tools, both reports in `code/evaluation/`, and
both model caches (`code/cache/llm_cache.json`, `code/cache/media/*.json`), which offline runs need. It holds
no dataset, no `.env` or other secrets, no virtual environment and no nested archives.
