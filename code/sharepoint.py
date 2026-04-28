"""SharePoint Online upload utility using Microsoft Graph API (app-only, client credentials)."""

import logging
import os
import sys
import time

import requests

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

# Module-level caches shared across Streamlit reruns within the same process
_token_cache: dict = {"token": None, "expires_at": 0.0}
_drive_id_cache: dict = {"drive_id": None}

# Dedicated logger with an explicit stderr handler so messages always appear in
# Render's log stream regardless of how Streamlit configures the root logger.
logger = logging.getLogger("ai_interview.sharepoint")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# Timing logger — same name used in interview.py so SP_TIMING and TURN_TIMING
# appear in the same stream. Configured separately in case sharepoint is
# imported standalone (e.g. from bench scripts) before interview.py runs.
timing_logger = logging.getLogger("ai_interview.timing")
if not timing_logger.handlers:
    _t_handler = logging.StreamHandler(sys.stderr)
    _t_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    timing_logger.addHandler(_t_handler)
    timing_logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _must_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def _sp_configured() -> bool:
    """Return True only if all required SharePoint env vars are present."""
    required = (
        "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
        "SP_HOSTNAME", "SP_SITE_PATH", "SP_LIBRARY_NAME", "SP_TARGET_FOLDER",
    )
    return all(os.getenv(k) for k in required)


def _get_token() -> str:
    """Return a valid app-only access token, refreshing when near expiry."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        timing_logger.info("SP_TIMING op=token elapsed_ms=0.0 cached=true")
        return _token_cache["token"]

    tenant_id = _must_env("TENANT_ID")
    client_id = _must_env("CLIENT_ID")
    client_secret = _must_env("CLIENT_SECRET")

    _t = time.perf_counter()
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    _elapsed_ms = (time.perf_counter() - _t) * 1000
    if r.status_code != 200:
        _token_cache["token"] = None  # invalidate on failure
        timing_logger.warning(
            "SP_TIMING op=token elapsed_ms=%.1f cached=false status=fail http=%d",
            _elapsed_ms, r.status_code,
        )
        raise RuntimeError(f"Token request failed ({r.status_code}): {r.text}")

    payload = r.json()
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = now + payload.get("expires_in", 3600) - 60
    timing_logger.info(
        "SP_TIMING op=token elapsed_ms=%.1f cached=false status=ok", _elapsed_ms,
    )
    return _token_cache["token"]


def _get_drive_id(token: str) -> str:
    """Resolve and cache the SharePoint drive ID for the configured library."""
    if _drive_id_cache["drive_id"]:
        timing_logger.info("SP_TIMING op=drive_id elapsed_ms=0.0 cached=true")
        return _drive_id_cache["drive_id"]

    sp_hostname = _must_env("SP_HOSTNAME")
    sp_site_path = _must_env("SP_SITE_PATH")
    library_name = _must_env("SP_LIBRARY_NAME")

    headers = {"Authorization": f"Bearer {token}"}

    _t = time.perf_counter()
    r = requests.get(
        f"{GRAPH_ROOT}/sites/{sp_hostname}:{sp_site_path}",
        headers=headers,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Site lookup failed ({r.status_code}): {r.text}")
    site_id = r.json()["id"]

    r = requests.get(
        f"{GRAPH_ROOT}/sites/{site_id}/drives",
        headers=headers,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Drives lookup failed ({r.status_code}): {r.text}")

    drives = r.json().get("value", [])
    drive_id = next((d["id"] for d in drives if d.get("name") == library_name), None)
    if not drive_id:
        available = [d.get("name") for d in drives]
        raise RuntimeError(
            f"Library '{library_name}' not found. Available: {available}"
        )

    _drive_id_cache["drive_id"] = drive_id
    timing_logger.info(
        "SP_TIMING op=drive_id elapsed_ms=%.1f cached=false status=ok",
        (time.perf_counter() - _t) * 1000,
    )
    return drive_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_connectivity() -> None:
    """Acquire a token and resolve the drive ID.

    Raises RuntimeError with a human-readable message on any failure.
    Call once at app startup (guarded by st.session_state) to catch
    misconfiguration before any interview data is at risk.
    """
    token = _get_token()
    _get_drive_id(token)
    logger.info("SharePoint connectivity verified OK.")


def upload_bytes(filename: str, content: bytes, subfolder: str = "", _retries: int = 3) -> None:
    """Upload *content* as *filename* into the configured SharePoint folder.

    If *subfolder* is provided (e.g. "transcripts", "backups") the file is
    placed at SP_TARGET_FOLDER/subfolder/filename, mirroring the local
    data/ directory layout. The Graph API creates missing intermediate
    folders automatically.

    Retries up to *_retries* times with exponential backoff (1 s, 2 s, …)
    before re-raising the last exception.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _retries + 1):
        _t_attempt = time.perf_counter()
        try:
            # Always fetch a (possibly cached) token; it self-refreshes on expiry.
            token = _get_token()
            drive_id = _get_drive_id(token)
            folder_path = _must_env("SP_TARGET_FOLDER").strip("/")

            sp_path = f"{folder_path}/{subfolder}/{filename}" if subfolder else f"{folder_path}/{filename}"
            upload_url = f"{GRAPH_ROOT}/drives/{drive_id}/root:/{sp_path}:/content"
            _t_put = time.perf_counter()
            r = requests.put(
                upload_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                },
                data=content,
                timeout=60,
            )
            _put_ms = (time.perf_counter() - _t_put) * 1000
            if r.status_code >= 400:
                # 401 means the cached token may be stale — clear it so the
                # next attempt re-authenticates.
                if r.status_code == 401:
                    _token_cache["token"] = None
                raise RuntimeError(
                    f"Upload of '{filename}' failed ({r.status_code}): {r.text}"
                )

            timing_logger.info(
                "SP_TIMING op=upload elapsed_ms=%.1f put_ms=%.1f attempt=%d/%d "
                "status=ok bytes=%d folder='%s' filename='%s'",
                (time.perf_counter() - _t_attempt) * 1000, _put_ms, attempt, _retries,
                len(content), subfolder or ".", filename,
            )
            logger.info("SharePoint upload OK: %s/%s (attempt %d)", subfolder or ".", filename, attempt)
            return

        except Exception as exc:
            last_exc = exc
            timing_logger.warning(
                "SP_TIMING op=upload elapsed_ms=%.1f attempt=%d/%d status=retry "
                "bytes=%d folder='%s' filename='%s' err='%s'",
                (time.perf_counter() - _t_attempt) * 1000, attempt, _retries,
                len(content), subfolder or ".", filename, str(exc)[:120],
            )
            logger.warning(
                "SharePoint upload attempt %d/%d failed for '%s': %s",
                attempt, _retries, filename, exc,
            )
            if attempt < _retries:
                time.sleep(2 ** (attempt - 1))  # 1 s, 2 s before attempts 2, 3

    timing_logger.error(
        "SP_TIMING op=upload status=fail attempts=%d filename='%s' folder='%s' err='%s'",
        _retries, filename, subfolder or ".", str(last_exc)[:120],
    )
    logger.error(
        "SharePoint upload FAILED after %d attempts for '%s': %s",
        _retries, filename, last_exc,
    )
    raise last_exc


def upload_text(filename: str, text: str, subfolder: str = "", encoding: str = "utf-8") -> None:
    """Convenience wrapper: encode *text* and upload as *filename*."""
    upload_bytes(filename, text.encode(encoding), subfolder=subfolder)
