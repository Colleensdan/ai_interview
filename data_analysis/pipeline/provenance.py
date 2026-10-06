"""What produced a run: code version, input fingerprints, behaviour settings.

A coding run is only reproducible if everything that shapes a model's request,
or how its reply is read, can be shown to be the same. Two parts:

* ``must_match`` -- anything that changes the requests sent or how replies are
  parsed. A resumed run must match its first invocation on every one of these,
  or one run would silently mix two experiments. Resuming used to keep the
  run's recorded ``definitions`` label without checking that the invocation
  actually used the same text, so a resume could code new models against a
  different codebook with no warning.
* ``info`` -- recorded for disclosure (code version, library versions, retry
  budgets for infrastructure faults), but allowed to differ between
  invocations because it does not change what a model is asked or how its
  answer is read.

All hashes are SHA-256 over a canonical encoding, so the same inputs always
give the same fingerprint on any machine.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from importlib import metadata
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8"))


def sha256_file(path) -> str | None:
    p = Path(path)
    return sha256_bytes(p.read_bytes()) if p.is_file() else None


def prompt_sha256(system: str, user: str) -> str:
    """Fingerprint of the exact text of one request."""
    return sha256_bytes(f"{system}\n\x00\n{user}".encode("utf-8"))


def git_state(path: Path = ROOT) -> tuple[str | None, bool | None]:
    """(HEAD commit, whether tracked files under *path* differ from it).

    Untracked files are ignored: they are not part of the pipeline unless
    something imports them, and an import of an untracked module would fail on
    a clean checkout anyway.
    """
    try:
        commit = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain",
             "--untracked-files=no", "--", "."], check=True,
            capture_output=True, text=True).stdout.strip() != ""
        return commit, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def _adapter_spec(adapter) -> dict:
    spec = getattr(adapter, "spec", None)
    d = {"name": adapter.name,
         "model_id": getattr(adapter, "model_id", None)
         or getattr(adapter, "deployment", None)}
    if spec is not None:
        d.update(route=spec.route, gateway=spec.gateway,
                 supports_temperature=spec.supports_temperature,
                 supports_json_mode=spec.supports_json_mode)
    return d


def build(*, codes, sample, adapters, definitions: str, definitions_src,
          definitions_max_version) -> dict:
    """The provenance record for one invocation."""
    from models.prompting import PARSER_VERSION, SYSTEM_PROMPT, _INTERVIEWER_SCOPE
    from pipeline.matrices import VOTE_RULE

    must_match = {
        "parser_version": PARSER_VERSION,
        "prompt_template_sha256": sha256_json({
            "system": SYSTEM_PROMPT, "interviewer_scope": _INTERVIEWER_SCOPE,
            "order": config.PROMPT_ORDER}),
        "prompt_order": config.PROMPT_ORDER,
        "definitions": definitions,
        # The text actually sent, not the label: two invocations can both say
        # "current" and still read different definitions databases.
        "definitions_sha256": sha256_json([[c.name, c.definition] for c in codes]),
        "codes": [c.name for c in codes],
        # Order matters: batches are consecutive slices of the sample.
        "sample_sha256": sha256_json(
            [[iv.title, sha256_bytes(iv.text.encode("utf-8"))] for iv in sample]),
        "n_documents": len(sample),
        "docs_per_call": config.DOCS_PER_CALL,
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
        "malformed_retry_attempts": config.MALFORMED_RETRY_ATTEMPTS,
        "excluded_codes": sorted(config.EXCLUDED_CODES),
        "interviewer_only_codes": sorted(config.INTERVIEWER_ONLY_CODES),
        "sample_fraction": config.SAMPLE_FRACTION,
        "random_seed": config.RANDOM_SEED,
        "anthropic_version": config.ANTHROPIC_VERSION,
    }
    commit, dirty = git_state()
    info = {
        "git_commit": commit,
        "git_dirty": dirty,
        "definitions_src": str(definitions_src) if definitions_src else None,
        "definitions_max_version": definitions_max_version,
        "codebook_sha256": sha256_file(config.CODEBOOK_PATH),
        "ground_truth_counts_sha256": sha256_file(config.GROUND_TRUTH_COUNTS_PATH),
        "ground_truth_quotes_sha256": sha256_file(config.GROUND_TRUTH_QUOTES_PATH),
        "gt_code_aliases": dict(sorted(config.GT_CODE_ALIASES.items())),
        "procedural_codes": sorted(config.PROCEDURAL_CODES),
        "vote_rule": VOTE_RULE,
        "transient_retry_attempts": config.TRANSIENT_RETRY_ATTEMPTS,
        "max_concurrent_calls": config.MAX_CONCURRENT_CALLS,
        "models": [_adapter_spec(a) for a in adapters],
        "python": platform.python_version(),
        "packages": {p: _version(p) for p in ("openai", "requests", "openpyxl")},
    }
    return {"must_match": must_match, "info": info}


def mismatches(first: dict, current: dict) -> list[str]:
    """Human-readable differences in ``must_match`` between two records."""
    a, b = first.get("must_match", {}), current.get("must_match", {})
    out = []
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            va, vb = a.get(key), b.get(key)
            if isinstance(va, (list, dict)) or isinstance(vb, (list, dict)):
                out.append(f"{key}: differs")
            else:
                out.append(f"{key}: {va!r} -> {vb!r}")
    return out
