"""SharePoint read/write via Microsoft Graph (app-only client credentials).

Auth mirrors code/sharepoint.py exactly (same TENANT_ID / CLIENT_ID /
CLIENT_SECRET / SP_HOSTNAME / SP_SITE_PATH / SP_LIBRARY_NAME env vars) but adds
the read operations (list / download) the Task 2 app needs to pull the
configured "Test Data" folder at startup. Kept self-contained so the deployed
app has no runtime dependency on the sibling ``code/`` package.

Upload requires Files.ReadWrite.All / Sites.ReadWrite.All on the app
registration; download requires the read equivalents. A 403 means the granted
scope doesn't permit that operation.
"""

from __future__ import annotations

import os
import time

import requests

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

_token_cache: dict = {"token": None, "expires_at": 0.0}
_drive_id_cache: dict = {"drive_id": None}


class SharePointError(RuntimeError):
    pass


class PreconditionFailed(SharePointError):
    """Upload rejected because the remote item changed since we read it.

    Raised on HTTP 412 when an ``If-Match`` ETag no longer matches — i.e. another
    writer/instance updated the blob. Callers should re-hydrate and retry rather
    than blindly overwrite (which would clobber the other writer's changes).
    """


def _must_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise SharePointError(f"Missing environment variable: {name}")
    return v


def configured() -> bool:
    required = ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
                "SP_HOSTNAME", "SP_SITE_PATH", "SP_LIBRARY_NAME")
    return all(os.getenv(k) for k in required)


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    tenant_id = _must_env("TENANT_ID")
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": _must_env("CLIENT_ID"),
            "client_secret": _must_env("CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise SharePointError(f"Token request failed ({r.status_code}): {r.text}")
    payload = r.json()
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = now + payload.get("expires_in", 3600) - 60
    return _token_cache["token"]


def _get_drive_id(token: str) -> str:
    if _drive_id_cache["drive_id"]:
        return _drive_id_cache["drive_id"]
    headers = {"Authorization": f"Bearer {token}"}
    sp_hostname = _must_env("SP_HOSTNAME")
    sp_site_path = _must_env("SP_SITE_PATH")
    library_name = _must_env("SP_LIBRARY_NAME")

    r = requests.get(f"{GRAPH_ROOT}/sites/{sp_hostname}:{sp_site_path}", headers=headers, timeout=30)
    if r.status_code >= 400:
        raise SharePointError(f"Site lookup failed ({r.status_code}): {r.text}")
    site_id = r.json()["id"]

    r = requests.get(f"{GRAPH_ROOT}/sites/{site_id}/drives", headers=headers, timeout=30)
    if r.status_code >= 400:
        raise SharePointError(f"Drives lookup failed ({r.status_code}): {r.text}")
    drives = r.json().get("value", [])
    drive_id = next((d["id"] for d in drives if d.get("name") == library_name), None)
    if not drive_id:
        raise SharePointError(
            f"Library '{library_name}' not found. Available: {[d.get('name') for d in drives]}"
        )
    _drive_id_cache["drive_id"] = drive_id
    return drive_id


def _headers() -> tuple[str, dict]:
    token = _get_token()
    return _get_drive_id(token), {"Authorization": f"Bearer {token}"}


# --- write ------------------------------------------------------------------

#: Above this size a single PUT stops being reliable — Microsoft's own guidance
#: is to use an upload session past ~4 MB, and in practice a ~19 MB body over a
#: slow link times out mid-write. The failure is not silent (the item is left
#: untouched), but it does mean the sync never lands.
LARGE_UPLOAD_THRESHOLD = 4 * 1024 * 1024
#: Chunk size for session uploads. Graph requires a multiple of 320 KiB.
UPLOAD_CHUNK = 5 * 320 * 1024


def _upload_session(remote_path: str, content: bytes) -> str | None:
    """Upload via a resumable session, for payloads too big for one PUT.

    Note the weaker concurrency guarantee: an upload session takes a conflict
    behaviour, not an ``If-Match`` ETag, so this cannot fail cleanly on a
    concurrent writer the way :func:`upload_bytes` does. Callers that care
    (``sync_to_live.py``) re-check the ETag immediately before starting the
    session, take a backup first, and verify the content afterwards.
    """
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    r = requests.post(
        f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}:/createUploadSession",
        headers={**headers, "Content-Type": "application/json"},
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        timeout=60,
    )
    if r.status_code >= 400:
        raise SharePointError(
            f"Could not open an upload session for '{remote_path}' "
            f"({r.status_code}): {r.text[:300]}")
    url = r.json()["uploadUrl"]

    total = len(content)
    sent = 0
    try:
        while sent < total:
            chunk = content[sent:sent + UPLOAD_CHUNK]
            end = sent + len(chunk) - 1
            last_exc = None
            for attempt in range(4):        # chunks are individually retryable
                try:
                    cr = requests.put(
                        url, data=chunk, timeout=180,
                        headers={"Content-Length": str(len(chunk)),
                                 "Content-Range": f"bytes {sent}-{end}/{total}"})
                except requests.RequestException as exc:
                    last_exc = exc
                    time.sleep(2 ** attempt)
                    continue
                if cr.status_code in (200, 201, 202):
                    if cr.status_code in (200, 201):
                        try:
                            return cr.json().get("eTag")
                        except ValueError:
                            return None
                    break
                if cr.status_code in (408, 429, 500, 502, 503, 504):
                    last_exc = SharePointError(
                        f"chunk {sent}-{end} HTTP {cr.status_code}")
                    time.sleep(2 ** attempt)
                    continue
                raise SharePointError(
                    f"Upload of '{remote_path}' failed on bytes {sent}-{end} "
                    f"({cr.status_code}): {cr.text[:300]}")
            else:
                raise SharePointError(
                    f"Upload of '{remote_path}' gave up on bytes {sent}-{end}: "
                    f"{last_exc}")
            sent += len(chunk)
    except Exception:
        # Abandon the session so a failed run does not leave one open against
        # the item; best-effort, the service expires them anyway.
        try:
            requests.delete(url, timeout=30)
        except requests.RequestException:
            pass
        raise
    return etag(remote_path)


def upload_bytes(remote_path: str, content: bytes, if_match: str | None = None) -> str | None:
    """Upload *content* to drive-root-relative *remote_path* (folders auto-created).

    If ``if_match`` (an ETag) is given, the PUT carries an ``If-Match`` header so
    the write only succeeds when the remote item is unchanged since that ETag;
    otherwise Graph returns 412 and we raise :class:`PreconditionFailed`. Passing
    ``if_match="*"`` requires the item to already exist. Returns the new ETag of
    the uploaded item (or ``None`` if Graph didn't report one).

    Payloads over :data:`LARGE_UPLOAD_THRESHOLD` are routed to a resumable
    upload session instead, which is the only thing that works at that size.
    Sessions cannot carry ``If-Match``, so the ETag is verified immediately
    before the session opens rather than enforced by the service — see
    :func:`_upload_session`.
    """
    if len(content) > LARGE_UPLOAD_THRESHOLD:
        if if_match:
            current = etag(remote_path)
            if current is not None and current != if_match:
                raise PreconditionFailed(
                    f"Upload of '{remote_path}' precondition failed: remote is at "
                    f"ETag {current!r}, expected {if_match!r}")
        return _upload_session(remote_path, content)

    drive_id, headers = _headers()
    path = remote_path.strip("/")
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}:/content"
    put_headers = {**headers, "Content-Type": "application/octet-stream"}
    if if_match:
        put_headers["If-Match"] = if_match
    r = requests.put(url, headers=put_headers, data=content, timeout=120)
    if r.status_code == 412:
        raise PreconditionFailed(
            f"Upload of '{remote_path}' precondition failed (412): remote changed "
            f"since ETag {if_match!r}")
    if r.status_code >= 400:
        raise SharePointError(f"Upload of '{remote_path}' failed ({r.status_code}): {r.text}")
    try:
        return r.json().get("eTag")
    except ValueError:
        return None


# --- read -------------------------------------------------------------------

def list_folder(remote_path: str) -> list[dict]:
    """List immediate children of a folder. Returns [] if the folder is missing.

    Each item: {name, is_folder, path} where path is drive-root-relative.
    """
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}:/children"
    items: list[dict] = []
    while url:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise SharePointError(f"List of '{remote_path}' failed ({r.status_code}): {r.text}")
        body = r.json()
        for it in body.get("value", []):
            items.append({
                "name": it["name"],
                "is_folder": "folder" in it,
                "path": f"{path}/{it['name']}",
            })
        url = body.get("@odata.nextLink")
    return items


def download_bytes(remote_path: str) -> bytes:
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}:/content"
    r = requests.get(url, headers=headers, timeout=120)
    if r.status_code >= 400:
        raise SharePointError(f"Download of '{remote_path}' failed ({r.status_code}): {r.text}")
    return r.content


def etag(remote_path: str) -> str | None:
    """Current ETag of a drive item, or None if it doesn't exist.

    Used for optimistic-concurrency writes: capture the ETag when we read the
    blob, then pass it as ``if_match`` on the next upload.
    """
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    r = requests.get(f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}", headers=headers, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise SharePointError(f"Stat of '{remote_path}' failed ({r.status_code}): {r.text}")
    return r.json().get("eTag")


def download_with_etag(remote_path: str) -> tuple[bytes, str | None]:
    """Download bytes and the item's current ETag together.

    Returns ``(content, etag)``. The ETag is read from item metadata so it
    matches what Graph checks on an ``If-Match`` upload.
    """
    tag = etag(remote_path)
    return download_bytes(remote_path), tag


def exists(remote_path: str) -> bool:
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    r = requests.get(f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}", headers=headers, timeout=30)
    return r.status_code < 400


def delete(remote_path: str) -> None:
    """Delete a drive item (file or folder). No-op if it doesn't exist."""
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    r = requests.delete(f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}", headers=headers, timeout=30)
    if r.status_code not in (204, 404):
        raise SharePointError(f"Delete of '{remote_path}' failed ({r.status_code}): {r.text}")
