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

def upload_bytes(remote_path: str, content: bytes) -> None:
    """Upload *content* to drive-root-relative *remote_path* (folders auto-created)."""
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    url = f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}:/content"
    r = requests.put(
        url,
        headers={**headers, "Content-Type": "application/octet-stream"},
        data=content,
        timeout=120,
    )
    if r.status_code >= 400:
        raise SharePointError(f"Upload of '{remote_path}' failed ({r.status_code}): {r.text}")


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


def exists(remote_path: str) -> bool:
    drive_id, headers = _headers()
    path = remote_path.strip("/")
    r = requests.get(f"{GRAPH_ROOT}/drives/{drive_id}/root:/{path}", headers=headers, timeout=30)
    return r.status_code < 400
