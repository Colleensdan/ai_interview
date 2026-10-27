# Task 1 — LLM Qualitative Coding Pipeline

Applies a human-built codebook to interview transcripts with LLMs and measures
agreement against human "ground truth" coding (Cohen's kappa, target > 0.80).

## Layout

```
config.py            All paths/knobs (codebook, interviews, ground truth,
                     SharePoint dir, sample fraction, seed, Azure creds).
                     Everything "subject to change" is overridable via env vars.
models/              Pluggable model adapters.
  base.py            ModelAdapter interface + CodeHit / CodingRequest.
  azure_openai.py    Live Azure OpenAI adapter (mirrors code/interview.py).
  stubs.py           Claude / Gemini / DeepSeek / Mistral — auto-disabled
                     until their API key env var is set.
  __init__.py        Registry: available_adapters() = those with credentials.
pipeline/
  names.py           Canonical code-name handling (bullets, Gr=, ATLAS escapes).
  codebook.py        Read Code + Definition/Comment from Codebook.xlsx.
  interviews.py      Read .txt/.docx transcripts (recursive), sample, merge.
  ground_truth.py    Read human code x document counts (Counts.xlsx).
  quote_sheets.py    Resolve the quotes workbook: sheet -> code, quote -> doc.
  coding.py          Run one code over batched documents; halve on truncation.
  matrices.py        Per-model count matrices + cross-model majority vote.
  agreement.py       Per-code Cohen's kappa (binary present/absent).
  storage.py         CSV writers + SQLite schema/inserts.
  validate_inputs.py Cross-check the four inputs before spending anything.
run_mini.py          Tiny REAL Azure call (1-2 codes x 1-2 docs) for validation.
run_pipeline.py      Full end-to-end run.
outputs/             Generated CSVs + coding.db (SQLite).
```

## Input data

Four inputs, all under `AICODE_DATA_ROOT` (default `data/`):

| | file | shape |
|---|---|---|
| transcripts | `All chats/All chats/*.txt` | `assistant:` / `user:` turn prefixes; a line with no prefix continues the previous turn |
| codebook | `Codebook.xlsx` | sheet with `Code` + `Comment` (or `Definition`) |
| human counts | `Counts.xlsx` | ATLAS.ti `CodeDocumentTable`; `Totals` row/column dropped |
| human quotes | `Quotations.xlsx` | one sheet per code, `ID` + `Quotation Content` |

Two details are load-bearing. Sheet names in the quotes workbook are truncated
to 31 characters and stripped of `/`, so distinct codes can collide; they are
told apart using ATLAS.ti's own `Gr=` quotation counts, never by fuzzy name
matching. And the quotation `ID` is `<document number>:<quotation number>`,
where the document numbering is the column order of the count matrix — that is
what attributes a quote to one transcript rather than to every transcript
containing the same words.

The earlier template data set (`.docx` interviews, `CountData.xlsx`,
`Ground Truth.xlsx`) still loads; point `AICODE_DATA_ROOT` at it and set
`AICODE_GROUND_TRUTH` / `AICODE_GROUND_TRUTH_QUOTES` to those filenames.

## Configuration

Secrets and the SharePoint config live in `data_analysis/.env` (already present).
Azure uses `CJBS_API_KEY`, `CJBS_API_ENDPOINT`, `CJBS_API_VERSION`,
`CJBS_DEPLOYMENT_NAME` — identical to `code/`.

To code with a personal OpenAI key instead, set `USE_AZURE=false` and
`OPENAI_API_KEY` (same switch as `code/`). `CJBS_DEPLOYMENT_NAME` then names the
OpenAI model, and the Azure-deployment catalog rows (`gpt-5-mini`,
`gpt-4.1-nano`) plus any uncatalogued non-Claude id in `AICODE_MODELS` go to
api.openai.com. Results are labelled `openai-<model>` rather than
`azure_openai-<model>`, so the two are never mixed in one model column. The n1ai
models and Claude are unaffected. `OPENAI_BASE_URL` overrides the endpoint.

Paths and behaviour are configurable; defaults point at the real study data:

| env var | default | |
|---|---|---|
| `AICODE_DATA_ROOT` | `data/` | root of the four inputs |
| `AICODE_INTERVIEWS_DIR` | `<root>/All chats/All chats` | searched recursively |
| `AICODE_CODEBOOK` | `<root>/Codebook.xlsx` | |
| `AICODE_GROUND_TRUTH` | `<root>/Counts.xlsx` | + `AICODE_GT_SHEET` |
| `AICODE_GROUND_TRUTH_QUOTES` | `<root>/Quotations.xlsx` | |
| `AICODE_TRANSCRIPT_EXTS` | `.txt,.docx` | |
| `AICODE_SAMPLE_FRACTION` | `1.0` | spec §4.1 says 0.5; see below |
| `AICODE_EXCLUDED_CODES` | 6 admin codes + `worry` | not coded, not scored |
| `AICODE_GT_CODE_ALIASES` | one rename (see below) | `codebook name=ground-truth name`, `;`-separated |
| `AICODE_DOCS_PER_CALL` | `25` | documents per model call |
| `AICODE_MAX_CONCURRENCY` | `4` | batches in flight per code |
| `AICODE_KAPPA_TARGET` | `0.80` | |
| `AICODE_MAX_DOC_LOSS` | `0.05` | abort if more documents fail to join |
| `AICODE_MODELS` | *(unset)* | opt in to extra models; see below |

### Ground-truth code drift

Ground-truth exports are re-issued as the human coders revise their work, and
the code set moves between issues. Two kinds of drift need handling, because
both corrupt kappa silently:

* **Renamed code.** Fuzzy matching (`pipeline.agreement.match_codes`) absorbs
  spelling drift, but not a changed word — no cutoff both catches
  `increased awareness` -> `increased attention` and refuses to pair genuinely
  different codes. Assert the rename in `AICODE_GT_CODE_ALIASES` so it is
  visible and reviewable. An alias whose target is absent from the export is
  ignored, so a stale entry cannot resurrect a dropped code.
* **Dropped code.** A codebook code with no ground-truth row is reported as
  **unscored** (`KappaResult.no_ground_truth`) and excluded from the mean —
  never scored against an all-absent human row. That path looks conservative
  but is not: if the model also never applies the code, both vectors are
  identical constants and Cohen's kappa returns **1.0**, a perfect score for a
  code nobody could score, which then inflates the headline average.

`run_pipeline.py --validate-only` reports both before any model is called.


## Models

The CJBS endpoint is an Azure API Management gateway, and it fronts three wire
protocols on the *same* credentials — so a second or third model needs no new
key, only the right route:

| route | models | auth header |
|---|---|---|
| `/openai/deployments/{name}/chat/completions` | `gpt-5-mini`, `gpt-4.1-nano` | `api-key` |
| `/openai/v1/chat/completions` | `gpt-5.2`, `gpt-5.2-chat`, `gpt-5.6-luna`, `gpt-5.6-sol`, `Mistral-Large-3`, `mistral-small-2503` | `Authorization: Bearer` |
| `/anthropic/v1/messages` | `claude-fable-5`, `claude-sonnet-4-6` | `x-api-key` |

`models/catalog.py` is the whole registry — a new model is one row, and
`models.adapter_for()` picks the transport from it. Every model is given the
same prompt and its reply is parsed by the same rules (`models/prompting.py`),
so a kappa comparison measures the model rather than the adapter.

**Registering a model does not put it into a run.** With `AICODE_MODELS` unset —
the default, and what Render deploys — the pipeline codes with exactly one
model, the `CJBS_DEPLOYMENT_NAME` deployment (`gpt-5-mini`), as it always has.
Setting `AICODE_MODELS=gpt-5-mini,claude-sonnet-4-6` replaces that set, and each
named model is coded and scored separately.

Check what the key can actually reach:

```
python check_models.py                       # every catalog model
python check_models.py gpt-5.2 claude-fable-5
```

It calls `adapter.code_one()` on a small synthetic transcript, so a model only
passes if it authenticates, accepts our parameters, obeys the role-tag rule and
returns parseable JSON. **As of the last run only `gpt-5-mini` and
`gpt-4.1-nano` pass**; the other eight return `403 ModelNotAllowed`, meaning the
subscription has not been granted them. That is an entitlement setting on the
CJBS key, not something the code can work around — the routes and the auth
headers are already correct, and each model starts working the moment access is
added, with no code change.

Two defaults are judgement calls rather than mechanics. **Sample fraction** is
1.0: spec §4.1 asks for 50%, which existed to limit spend on 14 long template
interviews, but the real chats are short (the whole corpus is ~65k tokens) and
coding all of them roughly halves the standard error on every kappa.
**Excluded codes** are `finished`, `summary` and `summary: 1`–`4`, which
describe the chatbot's behaviour rather than the participant's answers — every
ground-truth quote for `finished` is an assistant turn, which the
interviewee-only rule forbids the model from coding, so it could never be found.
`worry` is excluded for a different reason: the 2026-09 ground-truth export
withdrew it, so there is no human row left to agree with.

**Average kappa.** `pipeline.agreement.mean_kappa` reports the *unweighted*
mean across codes, shown as the headline tile on the app's home page. Each code
is one construct in the codebook and counts once, regardless of how often it
occurs; a pooled kappa over all code x document cells would answer a different
question and let the commonest codes outvote the rest. Undefined kappas are
excluded rather than read as zero, and the tile states how many codes the mean
covers. The median is shown beside it as a skew check.

## Run

```bash
pip install -r requirements.txt
python run_pipeline.py --validate-only   # cross-check inputs, no API calls
python run_mini.py                       # cheap real Azure call
python run_pipeline.py                   # full run (original codebook definitions)

# code with the reviewers' CURRENT edited definitions instead:
python run_pipeline.py --definitions=current
python run_pipeline.py --definitions=current --definitions-db /path/to/coding.sqlite
```

**Which definitions a run used matters, and is easy to get wrong.** By default
`run_pipeline.py` codes with the original `Codebook.xlsx` text, while the Task 2
app's re-analyse (`app/jobs.py`) codes with each code's *current* version from
`code_definitions`. Those answer different questions, so kappas from the two are
not comparable. `--definitions=current` makes the batch run match the app.
`--definitions-db` points at a specific store and is opened read-only (via an
in-memory copy — `DefinitionStore` would otherwise `CREATE TABLE IF NOT EXISTS`
on the file just to read it), so an archived database is safe to use.

`run_pipeline.py` validates its inputs first and refuses to start if they do not
line up (`--skip-validate` overrides). Validation is where an input mismatch
should surface: every one of these joins used to fail silently, scoring kappa
against an all-absent human row or dropping documents without a word.

## Outputs

- `results_<model>.csv` — Document title, Code name, Quote, Reason.
- `countmatrix_<model>.csv` — code x transcript counts (one per model).
- `countmatrix_majority.csv` — cross-model majority-vote counts.
- `ground_truth_matrix.csv` — human counts (also in DB as its own table).
- `kappa.csv` — per-code Cohen's kappa per model + majority vote.
- `coding.db` — unified SQLite: `coding_results`, `count_matrix`,
  `majority_vote_matrix`, `ground_truth_matrix`, `kappa` (PK + model column).

## Task 2 — Codebook Improvement App

A FastAPI backend + single-page web UI (in `app/`) that reads the Task 1 outputs
and lets researchers review LLM-vs-human coding, edit code definitions (versioned),
and re-run the Task 1 pipeline on the edits.

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000      # run from data_analysis/
# open http://localhost:8000
```

Screens (spec §5):
- **Overview (5.2):** counts + % of codes above/below κ 0.80, successful vs
  unsuccessful code lists, multi-select (tick) / "All unsuccessful", model-data
  picker, Review.
- **Comparison (5.4):** AI (left) vs human ground truth (right), whole scrollable
  interviews with per-code colour highlights (hover = definition), outer code
  margins (collapsible via ✕, restore from the ☰ menu), `n/N` counter + Next.
- **Reasons (5.5):** right-hand 1/3 pane listing the AI's reasons; the two panels
  squish to 2/3; margins auto-hidden.
- **Failure modes (5.3):** per code, false positives (found where it shouldn't be)
  and false negatives (missed where it should be), with reasons.
- **Finish → edit (5.7):** green Finish (top-right); edit definitions (previous
  versions archived, viewable with their κ via the ⤵ arrow).
- **Re-Analyse (5.8/5.9):** re-runs Task 1 for the selected codes using the new
  definitions (default = OpenAI, or all models); loading screen; then previous
  vs new κ; Continue returns to the overview to iterate.

App data layer:
- `app/data_access.py` — reads coding.db + transcripts + Ground Truth.xlsx;
  locates quotes in transcripts for highlighting (cached, whitespace-insensitive).
- `app/definitions.py` — versioned definitions table (archive, never overwrite).
- `app/reanalyze.py` — background re-run of the pipeline for selected codes.
- `app/static/` — `index.html`, `styles.css`, `app.js` (vanilla SPA).

## Adding a model

Fill in `code_one` on the relevant stub in `models/stubs.py` and set its key
env var. The registry picks it up automatically; majority voting counts it as
another voter (works for N=1 today, N>1 later).
```
