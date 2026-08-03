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
| `AICODE_EXCLUDED_CODES` | the 6 admin codes | not coded, not scored |
| `AICODE_DOCS_PER_CALL` | `25` | documents per model call |
| `AICODE_MAX_CONCURRENCY` | `4` | batches in flight per code |
| `AICODE_KAPPA_TARGET` | `0.80` | |
| `AICODE_MAX_DOC_LOSS` | `0.05` | abort if more documents fail to join |

Two defaults are judgement calls rather than mechanics. **Sample fraction** is
1.0: spec §4.1 asks for 50%, which existed to limit spend on 14 long template
interviews, but the real chats are short (the whole corpus is ~65k tokens) and
coding all of them roughly halves the standard error on every kappa.
**Excluded codes** are `finished`, `summary` and `summary: 1`–`4`, which
describe the chatbot's behaviour rather than the participant's answers — every
ground-truth quote for `finished` is an assistant turn, which the
interviewee-only rule forbids the model from coding, so it could never be found.

## Run

```bash
pip install -r requirements.txt
python run_pipeline.py --validate-only   # cross-check inputs, no API calls
python run_mini.py                       # cheap real Azure call
python run_pipeline.py                   # full run
```

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
