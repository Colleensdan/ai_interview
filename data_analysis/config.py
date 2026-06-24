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
TEMPLATE_ROOT = _path_env(
    "AICODE_DATA_ROOT", HERE / "Documents AI coding examples"
)

# Interview transcripts (.docx).
INTERVIEWS_DIR = _path_env("AICODE_INTERVIEWS_DIR", TEMPLATE_ROOT / "Interviews")

# SharePoint source directory — configurable, NOT hardcoded (spec 1). The app
# downloads this folder's contents into TEMPLATE_ROOT at startup.
SHAREPOINT_DIR = os.getenv("AICODE_SHAREPOINT_DIR", "Test Data")
# Re-download from SharePoint on every boot even if data is already cached.
SHAREPOINT_REFRESH = str(os.getenv("AICODE_SP_REFRESH", "")).lower() in {"1", "true", "yes"}

# Codebook (Code / Freq / Definition across dimension sheets). Subject to change.
CODEBOOK_PATH = _path_env("AICODE_CODEBOOK", TEMPLATE_ROOT / "Codebook.xlsx")

# Human ground-truth code x document counts (ATLAS.ti export, the "Code
# Transcript Table" format the spec references).
GROUND_TRUTH_COUNTS_PATH = _path_env(
    "AICODE_GROUND_TRUTH", TEMPLATE_ROOT / "CountData.xlsx"
)
GROUND_TRUTH_COUNTS_SHEET = os.getenv("AICODE_GT_SHEET", "CodeDocumentTable")

# Human ground-truth quotes (one sheet per code) — used by the Task 2 app to
# highlight human-coded passages inside transcripts.
GROUND_TRUTH_QUOTES_PATH = _path_env(
    "AICODE_GROUND_TRUTH_QUOTES", TEMPLATE_ROOT / "Ground Truth.xlsx"
)

# Outputs / persistent store (live under DATA_DIR so they survive restarts).
OUTPUT_DIR = _path_env("AICODE_OUTPUT_DIR", DATA_DIR / "outputs")
# SQLite path: DATABASE_PATH is the canonical Render knob; AICODE_DB kept for
# back-compat. Defaults to <DATA_DIR>/coding.sqlite.
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
# reproducible (the same 50% is selected each time unless the seed changes).
SAMPLE_FRACTION = float(os.getenv("AICODE_SAMPLE_FRACTION", "0.5"))
RANDOM_SEED = int(os.getenv("AICODE_SEED", "42"))

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

# Cohen's kappa target (spec 4.8).
KAPPA_TARGET = 0.80

# --- Auth (Task 2 app) -------------------------------------------------------
AUTH_USERNAME = os.getenv("AUTH_USERNAME")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
# Cookie-signing secret. Falls back to a dev default locally; MUST be set in prod.
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-change-me")


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR
