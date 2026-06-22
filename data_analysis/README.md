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
  codebook.py        Read Code/Definition from Codebook.xlsx (per dimension).
  interviews.py      Read .docx transcripts, 50% sample, merge into one doc.
  ground_truth.py    Read human code x document counts (CountData.xlsx).
  matrices.py        Per-model count matrices + cross-model majority vote.
  agreement.py       Per-code Cohen's kappa (binary present/absent).
  storage.py         CSV writers + SQLite schema/inserts.
run_mini.py          Tiny REAL Azure call (1-2 codes x 1-2 docs) for validation.
run_pipeline.py      Full end-to-end run.
outputs/             Generated CSVs + coding.db (SQLite).
```

## Configuration

Secrets and the SharePoint config live in `data_analysis/.env` (already present).
Azure uses `CJBS_API_KEY`, `CJBS_API_ENDPOINT`, `CJBS_API_VERSION`,
`CJBS_DEPLOYMENT_NAME` — identical to `code/`.

Paths are configurable (defaults point at the template data):
`AICODE_CODEBOOK`, `AICODE_INTERVIEWS_DIR`, `AICODE_GROUND_TRUTH`,
`AICODE_SHAREPOINT_DIR`, `AICODE_SAMPLE_FRACTION`, `AICODE_SEED`,
`AICODE_OUTPUT_DIR`.

## Run

```bash
pip install -r requirements.txt
python run_mini.py          # cheap validation first
python run_pipeline.py      # full 50% across all codes and available models
```

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
