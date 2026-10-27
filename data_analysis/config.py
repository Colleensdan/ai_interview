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

# Codes RENAMED between ground-truth exports: "codebook name=ground-truth name".
# Fuzzy matching (pipeline.agreement.match_codes) handles spelling drift, not
# renames — a changed word puts the pair below any cutoff that is safe against
# false pairings — so a rename must be asserted explicitly and reviewably here,
# or the code silently loses its human row and its kappa becomes meaningless.
#
# 2026-09 export ("Counts 3"): "information seeking/increased awareness" was
# renamed to "...increased attention". Same construct — the two rows share 11 of
# their 12 documents. NOTE this is deliberately NOT used to paper over the same
# export's removal of "worry": that code was genuinely dropped by the human
# coders (3 quotations across 3 documents) with no successor, so it is reported
# as unscored rather than aliased onto some other code.
GT_CODE_ALIASES = {
    k.strip(): v.strip()
    for k, v in (
        pair.split("=", 1)
        for pair in os.getenv(
            "AICODE_GT_CODE_ALIASES",
            "information seeking/increased awareness"
            "=information seeking/increased attention",
        ).split(";")
        if pair.strip() and "=" in pair
    )
}

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

# Where the code name and definition sit relative to the transcripts.
#
#   code_first       (default) — the order every run to date has used. The code
#                    name and definition lead, so no two calls share a prefix
#                    beyond the 311-token system prompt and prompt caching never
#                    engages: the corpus is re-sent at full price once per code.
#   documents_first  — transcripts and titles lead, code name and definition
#                    trail. Everything ahead of the code is identical across all
#                    codes in a batch, so one call writes the cache and the rest
#                    read it at about a tenth of the price: ~85% off billed input.
#
# Kept switchable because moving the definition to the end changes what the
# model reads last before answering, which can move kappa. Default stays on the
# order the reported results were produced with until an A/B says otherwise.
PROMPT_ORDER = os.getenv("AICODE_PROMPT_ORDER", "code_first")

# Codes excluded from LLM coding and from kappa. Two codes remain, for two
# different reasons; the chatbot-behaviour codes that were once listed here were
# reinstated on 2026-09-14 (see PROCEDURAL_CODES below).
#
# 1. "summary" — not a code at all. The researchers confirmed it is a parent
#    category ATLAS.ti generates automatically over "summary: 1".."summary: 4",
#    which is why its definition cell is empty and it has no quotation sheet.
#    Its 57 documents are exactly the union of its four children, so coding it
#    would measure nothing the children do not already measure.
#
# 2. "worry" — withdrawn by the human coders. The 2026-09 ground-truth export
#    drops the code entirely; in the previous export it carried 3 quotations
#    across 3 documents, and no other code absorbed them. With no human row
#    there is nothing to agree with, so asking the model to code it would
#    produce a kappa that only looks like a measurement.
EXCLUDED_CODES = [
    s.strip() for s in os.getenv(
        "AICODE_EXCLUDED_CODES",
        "summary,worry",
    ).split(",")
    if s.strip()
]

# Codes whose referent is the interviewer's own behaviour rather than anything
# the participant said. The default scope codes interviewee turns only, which
# keeps the model from coding the interviewer's questions as though the
# participant had said them; "finished" marks the assistant's closing turn, and
# all 63 of its ground-truth quotations are assistant turns, so under that
# default it can never be found. Scope is ours to set rather than the
# researchers': the ATLAS.ti codebook carries no scope column.
INTERVIEWER_ONLY_CODES = frozenset(
    s.strip() for s in os.getenv(
        "AICODE_INTERVIEWER_ONLY_CODES", "finished"
    ).split(",")
    if s.strip()
)

# Codes that are coded and scored, but held OUT of the cross-model comparison.
# These describe the interview procedure (did the chatbot close the chat; how
# did the participant rate its summary) rather than the substance of what was
# said, so they test instruction-following rather than semantic judgement.
# Folding them in would move each model's mean kappa by more than the gaps that
# currently separate the models, and could reorder them for reasons unrelated to
# coding performance. Reported separately instead, on the researchers' request.
PROCEDURAL_CODES = frozenset(
    s.strip() for s in os.getenv(
        "AICODE_PROCEDURAL_CODES",
        "finished,summary: 1,summary: 2,summary: 3,summary: 4",
    ).split(",")
    if s.strip()
)

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

# --- provider switch ----------------------------------------------------------
# Same switch as code/interview.py: USE_AZURE=true (the default) codes through the
# CJBS Azure deployment; USE_AZURE=false sends the OpenAI models straight to
# api.openai.com on a personal OPENAI_API_KEY. CJBS_DEPLOYMENT_NAME names the
# model either way.
USE_AZURE = str(os.getenv("USE_AZURE", "true")).strip().lower() in {"1", "true", "yes", "on"}

# --- plain OpenAI (used when USE_AZURE=false) ----------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"

# --- Azure OpenAI (live model) ----------------------------------------------
# Mirrors code/interview.py and code/bench_llm.py exactly.
AZURE_API_KEY = os.getenv("CJBS_API_KEY")
AZURE_ENDPOINT = os.getenv("CJBS_API_ENDPOINT")
AZURE_API_VERSION = os.getenv("CJBS_API_VERSION", "2023-05-15")
AZURE_DEPLOYMENT = os.getenv("CJBS_DEPLOYMENT_NAME")

# The CJBS endpoint is an APIM gateway fronting three wire protocols on the same
# key (see models/catalog.py). These are the two non-deployment routes; both are
# derived from CJBS_API_ENDPOINT so nothing new is needed in .env.
def _gateway_base(suffix: str) -> str:
    base = (AZURE_ENDPOINT or "").rstrip("/")
    return f"{base}{suffix}" if base else ""


# OpenAI-compatible route: model named in the request body, no api-version.
GATEWAY_OPENAI_BASE = os.getenv("CJBS_OPENAI_V1_BASE") or _gateway_base("/openai/v1")
# Anthropic Messages route: authenticates with x-api-key, not api-key.
GATEWAY_ANTHROPIC_BASE = os.getenv("CJBS_ANTHROPIC_BASE") or _gateway_base("/anthropic")
ANTHROPIC_VERSION = os.getenv("CJBS_ANTHROPIC_VERSION", "2023-06-01")

# --- second gateway (n1ai) ---------------------------------------------------
# A separate APIM subscription with its own key. It exposes the SAME two routes
# as CJBS (/openai/v1/chat/completions and /anthropic/v1/messages, both verified
# live), so no new adapter is needed — only different credentials. Entitlement,
# not routing, is what differs: the newer multi-vendor models answer here and
# 403 on CJBS. Each catalog entry names its gateway; see models/catalog.py.
N1AI_ENDPOINT = os.getenv("N1AI_API_ENDPOINT")
N1AI_API_KEY = os.getenv("N1AI_API_KEY")


def _n1ai_base(suffix: str) -> str:
    base = (N1AI_ENDPOINT or "").rstrip("/")
    return f"{base}{suffix}" if base else ""


#: gateway name -> (openai-compatible base, anthropic base, api key).
#: Adapters resolve credentials through this rather than reading module globals,
#: so adding a third gateway is one entry here plus one field in the catalog.
GATEWAYS: dict[str, tuple[str, str, str | None]] = {
    "cjbs": (GATEWAY_OPENAI_BASE, GATEWAY_ANTHROPIC_BASE, AZURE_API_KEY),
    "n1ai": (_n1ai_base("/openai/v1"), _n1ai_base("/anthropic"), N1AI_API_KEY),
    # Not a gateway: OpenAI itself, on a personal key. No Anthropic route.
    "openai": (OPENAI_BASE_URL, "", OPENAI_API_KEY),
}


def gateway_credentials(name: str) -> tuple[str, str, str | None]:
    """(openai_base, anthropic_base, api_key) for a named gateway.

    An unknown name falls back to the CJBS gateway rather than raising: a model
    row naming a gateway nobody configured should report itself unavailable
    through the adapter's normal ``is_available()`` path, not crash the import.
    """
    return GATEWAYS.get(name, GATEWAYS["cjbs"])

# Which models the pipeline actually runs. UNSET (the default) means "just the
# CJBS_DEPLOYMENT_NAME deployment", i.e. exactly the single-model behaviour the
# pipeline has always had — registering more models in the catalog does not put
# them into a run. Set e.g. AICODE_MODELS=gpt-5-mini,claude-sonnet-4-6 to opt in.
SELECTED_MODELS = [
    s.strip() for s in os.getenv("AICODE_MODELS", "").split(",") if s.strip()
]

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
# How many times to retry a call that failed for infrastructure reasons (shared
# backend circuit breaker, 429, 5xx). The alternative to retrying is dropping the
# code from the run, which puts a hole in the model x code matrix and quietly
# changes what a model's mean kappa is an average over. See pipeline.coding.
TRANSIENT_RETRY_ATTEMPTS = int(os.getenv("AICODE_TRANSIENT_RETRIES", "6"))
# Quota exhaustion refills on the provider's clock, so it gets a longer, flatter
# backoff than a circuit breaker. These defaults ride out a short per-minute
# window; a spent daily allowance will still exhaust the attempts and drop the
# code, which is correct — waiting hours inside a run helps nobody, and
# score_run.py + --resume-run-id make finishing later cheap.
QUOTA_RETRY_BASE_SECONDS = float(os.getenv("AICODE_QUOTA_RETRY_BASE", "45"))
QUOTA_RETRY_MAX_SECONDS = float(os.getenv("AICODE_QUOTA_RETRY_MAX", "180"))
# Total attempts for one batch when the model's reply cannot be read as an
# answer (no ``occurrences`` list, an unknown document title, ...). Such a reply
# used to be scored as "absent in every document of the batch". It is now
# re-asked, unchanged, up to this many times in all, and if no valid reply
# arrives the code is recorded as FAILED. Every attempt's raw reply is stored
# (raw_responses), so the re-asks are disclosed, not hidden.
MALFORMED_RETRY_ATTEMPTS = int(os.getenv("AICODE_MALFORMED_RETRIES", "3"))

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
