"""Central configuration for the LLM qualitative-coding pipeline (Task 1).

Everything that is "subject to change" per the spec is a knob here and is
overridable via environment variables, so paths can move (e.g. when the real
SharePoint directory is known) without touching code.

Secrets live in ``data_analysis/.env`` and are loaded here; values are never
printed.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent

# Load .env from this folder (data_analysis/.env), per the spec.
load_dotenv(dotenv_path=HERE / ".env")


def _path_env(name: str, default: Path) -> Path:
    """Return a path from env var ``name`` if set, else ``default``."""
    v = os.getenv(name)
    return Path(v).expanduser() if v else default


def _resolve_data_dir() -> Path:
    """Base directory for everything that must persist across restarts.

    On Render a persistent disk is mounted at /var/data; locally that path
    doesn't exist, so fall back to this folder (preserving the existing
    ``data_analysis/outputs`` layout). Override explicitly with DATA_DIR.
    """
    explicit = os.getenv("DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    render_disk = Path("/var/data")
    if render_disk.is_dir() and os.access(render_disk, os.W_OK):
        return render_disk
    return HERE


# Persistent base dir (Render disk in prod, this folder locally).
DATA_DIR = _resolve_data_dir()

# --- Data locations (all flagged as TBD / subject to change in the spec) -----
# Root of the input data set (interviews + codebook + ground truth). On Render
# this is populated from SharePoint at startup (set AICODE_DATA_ROOT=/var/data/input).
# Defaults to the real study data; the original template set is still on disk at
# "Documents AI coding examples" and can be selected with AICODE_DATA_ROOT.
TEMPLATE_ROOT = _path_env("AICODE_DATA_ROOT", HERE / "data")

# Interview transcripts. The real export nests them one level deeper
# ("All chats/All chats"); the directory is searched recursively either way.
INTERVIEWS_DIR = _path_env(
    "AICODE_INTERVIEWS_DIR", TEMPLATE_ROOT / "All chats" / "All chats"
)

# Transcript file types to load. The template set is .docx (python-docx); the
# real chat export is plain .txt with "assistant:"/"user:" turn prefixes.
TRANSCRIPT_EXTS = tuple(
    s.strip().lower() for s in
    os.getenv("AICODE_TRANSCRIPT_EXTS", ".txt,.docx").split(",")
    if s.strip()
)

# SharePoint source directory — configurable, NOT hardcoded (spec 1). The app
# downloads this folder's contents into TEMPLATE_ROOT at startup.
SHAREPOINT_DIR = os.getenv("AICODE_SHAREPOINT_DIR", "Test Data")
# Re-download from SharePoint on every boot even if data is already cached.
SHAREPOINT_REFRESH = str(os.getenv("AICODE_SP_REFRESH", "")).lower() in {"1", "true", "yes"}

# --- RAM-only / SharePoint-authoritative state (data-privacy: minimise on Render) --
# When AICODE_MEMORY_DB is set, the results DB lives ONLY in process memory
# (shared-cache SQLite) and the sole durable copy is in SharePoint. Nothing is
# written to the container filesystem. See app/state_sync.py.
MEMORY_DB = str(os.getenv("AICODE_MEMORY_DB", "")).lower() in {"1", "true", "yes"}
# Shared-cache in-memory URI: every db.connect() in this process reaches the same
# database, and an anchor connection keeps it alive (see db.py).
MEMORY_DB_URI = "file:aicode_state?mode=memory&cache=shared"

# SharePoint layout for the authoritative operational state + readable mirror.
SHAREPOINT_STATE_DIR = os.getenv("AICODE_SHAREPOINT_STATE_DIR", f"{SHAREPOINT_DIR}/state")
SHAREPOINT_DB_NAME = os.getenv("AICODE_SHAREPOINT_DB_NAME", "coding.sqlite")
SHAREPOINT_CSV_SUBDIR = os.getenv("AICODE_SHAREPOINT_CSV_SUBDIR", "csv")
# Legacy seed DB (the pre-RAM-only analysis DB). On the first memory-mode boot,
# if state/coding.sqlite doesn't exist yet we hydrate from this so existing
# analysis results are carried forward (then state/coding.sqlite is created).
SHAREPOINT_SEED_NAME = os.getenv("AICODE_SHAREPOINT_SEED_NAME", "coding_seed.sqlite")

# Codebook. One row per code; the definition column is headed "Definition" in
# the template export and "Comment" in the ATLAS.ti one (both accepted).
CODEBOOK_PATH = _path_env("AICODE_CODEBOOK", TEMPLATE_ROOT / "Codebook.xlsx")

# Human ground-truth code x document counts (ATLAS.ti export, the "Code
# Transcript Table" format the spec references). Named CountData.xlsx in the
# template export, Counts.xlsx in the real one.
GROUND_TRUTH_COUNTS_PATH = _path_env(
    "AICODE_GROUND_TRUTH", TEMPLATE_ROOT / "Counts.xlsx"
)
GROUND_TRUTH_COUNTS_SHEET = os.getenv("AICODE_GT_SHEET", "CodeDocumentTable")

# Human ground-truth quotes (one sheet per code) — used by the Task 2 app to
# highlight human-coded passages inside transcripts. "Ground Truth.xlsx" in the
# template export, "Quotations.xlsx" in the real one.
GROUND_TRUTH_QUOTES_PATH = _path_env(
    "AICODE_GROUND_TRUTH_QUOTES", TEMPLATE_ROOT / "Quotations.xlsx"
)

# Sheets in the quotes workbook that are not codes (ATLAS.ti adds a metadata
# sheet to every export).
NON_CODE_SHEETS = {
    s.strip().lower() for s in
    os.getenv("AICODE_NON_CODE_SHEETS", "Info").split(",")
    if s.strip()
}

# Codes to exclude from LLM coding and from kappa. These describe the chatbot's
# own behaviour rather than anything the participant said, so they are not a
# meaningful test of the codebook: "finished" marks the assistant's closing turn
# (every one of its ground-truth quotes is an assistant turn, which the
# interviewee-only rule below forbids the model from coding, so it can never be
# found), "summary" marks the assistant's summary, and "summary: N" records the
# participant's 1-5 rating of it.
EXCLUDED_CODES = [
    s.strip() for s in os.getenv(
        "AICODE_EXCLUDED_CODES",
        "finished,summary,summary: 1,summary: 2,summary: 3,summary: 4",
    ).split(",")
    if s.strip()
]

# Outputs / persistent store (live under DATA_DIR so they survive restarts).
OUTPUT_DIR = _path_env("AICODE_OUTPUT_DIR", DATA_DIR / "outputs")
# SQLite path: DATABASE_PATH is the canonical Render knob; AICODE_DB kept for
# back-compat. Defaults to <DATA_DIR>/coding.sqlite. In memory mode the "path"
# is the shared-cache in-memory URI (a str, never a filesystem path).
if MEMORY_DB:
    DB_PATH = MEMORY_DB_URI
else:
    DB_PATH = _path_env(
        "DATABASE_PATH", _path_env("AICODE_DB", DATA_DIR / "coding.sqlite")
    )
# Per-connection SQLite busy timeout (ms) for safe concurrent access.
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("AICODE_SQLITE_BUSY_TIMEOUT_MS", "10000"))

# Files to always ignore when scanning directories.
IGNORE_SUFFIXES = (":Zone.Identifier", ".Identifier")

# --- Speaker roles (patch 2: code the interviewee only) ----------------------
# EXPLICIT role labels that mark the interviewee (coded) vs the interviewer
# (never coded). Lowercased substring match against the turn label. Role-coding
# is applied ONLY to transcripts that use these explicit labels (the real data,
# e.g. "user"/"assistant"). Positional labels ("Speaker 1/2", names) are NOT
# role-restricted — all speakers are coded — because their numbering is not a
# reliable interviewer/interviewee signal in the test data.
INTERVIEWEE_LABELS = [
    s.strip().lower() for s in
    os.getenv("AICODE_INTERVIEWEE_LABELS", "user,interviewee").split(",")
    if s.strip()
]
INTERVIEWER_LABELS = [
    s.strip().lower() for s in
    os.getenv("AICODE_INTERVIEWER_LABELS", "assistant,interviewer").split(",")
    if s.strip()
]

# --- Sampling ----------------------------------------------------------------
# Fraction of ground-truth transcripts to code, and a fixed seed so a run is
# reproducible (the same subset is selected each time unless the seed changes).
# Spec 4.1 says 50%, which existed to limit spend on the 14 long template
# interviews. The real chats are short — the whole corpus is ~65k tokens — so we
# code all of them, which roughly halves the standard error on every kappa.
SAMPLE_FRACTION = float(os.getenv("AICODE_SAMPLE_FRACTION", "1.0"))
RANDOM_SEED = int(os.getenv("AICODE_SEED", "42"))

# Abort rather than quietly scoring kappa on a truncated document set: if more
# than this fraction of sampled transcripts fail to join to a ground-truth
# column, something is wrong with the inputs, not with the data.
MAX_DOC_LOSS_FRACTION = float(os.getenv("AICODE_MAX_DOC_LOSS", "0.05"))

# --- Azure OpenAI (live model) ----------------------------------------------
# Mirrors code/interview.py and code/bench_llm.py exactly.
AZURE_API_KEY = os.getenv("CJBS_API_KEY")
AZURE_ENDPOINT = os.getenv("CJBS_API_ENDPOINT")
AZURE_API_VERSION = os.getenv("CJBS_API_VERSION", "2023-05-15")
AZURE_DEPLOYMENT = os.getenv("CJBS_DEPLOYMENT_NAME")

# Generous cap: reasoning models (e.g. gpt-5-mini) spend tokens on reasoning
# before emitting the JSON, and a code can have many occurrences.
MAX_OUTPUT_TOKENS = int(os.getenv("AICODE_MAX_OUTPUT_TOKENS", "16384"))

# Per-request timeout (seconds) so a single hung call can't stall the run.
REQUEST_TIMEOUT = float(os.getenv("AICODE_REQUEST_TIMEOUT", "180"))

# --- Call batching -----------------------------------------------------------
# Documents per model call. MAX_OUTPUT_TOKENS is shared with the model's
# reasoning tokens, so a frequent code across a large corpus will truncate if
# every document goes in one prompt — and a truncated response is
# indistinguishable from "code not present". Batching bounds the answer size.
DOCS_PER_CALL = int(os.getenv("AICODE_DOCS_PER_CALL", "25"))
# Batches within a single code may run concurrently; codes stay sequential
# (spec 4.2: "process one code at a time").
MAX_CONCURRENT_CALLS = int(os.getenv("AICODE_MAX_CONCURRENCY", "4"))

# Cohen's kappa target (spec 4.8).
KAPPA_TARGET = float(os.getenv("AICODE_KAPPA_TARGET", "0.80"))

# --- Auth (Task 2 app) -------------------------------------------------------
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
# Cookie-signing secret. Falls back to a dev default locally; MUST be set in prod.
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-change-me")


def ensure_output_dir() -> Path:
    # In memory mode nothing is written to disk: no output dir, no DB parent
    # (DB_PATH is an in-memory URI, not a filesystem path).
    if MEMORY_DB:
        return OUTPUT_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR
