"""FastAPI backend for the Task 2 codebook-improvement app.

Run from data_analysis/:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
A login gate (shared account from AUTH_USERNAME/AUTH_PASSWORD) protects every UI
and API route via a signed session cookie.
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import config
from . import jobs, startup_sync
from .data_access import DataStore
from .definitions import DefinitionStore

logging.basicConfig(level=logging.INFO)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

# Paths reachable without a session.
_PUBLIC_PREFIXES = ("/login", "/logout", "/static", "/healthz", "/favicon")

app = FastAPI(title="Codebook Improvement App")

_data: DataStore | None = None
_defs: DefinitionStore | None = None


def data() -> DataStore:
    assert _data is not None
    return _data


def defs() -> DefinitionStore:
    assert _defs is not None
    return _defs


# --- auth -------------------------------------------------------------------

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/" or not path.startswith(_PUBLIC_PREFIXES):
            if not request.session.get("user"):
                if path.startswith("/api"):
                    return JSONResponse({"detail": "Not authenticated"}, status_code=401)
                return RedirectResponse(f"/login?next={path}", status_code=302)
        return await call_next(request)


# Order matters: SessionMiddleware must wrap (run before) AuthMiddleware so the
# session is populated when auth checks it. Last-added middleware is outermost.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="lax",
    https_only=False,  # Render terminates TLS; cookie still works over the proxy
    max_age=60 * 60 * 12,
)


@app.on_event("startup")
def _startup() -> None:
    global _data, _defs
    config.ensure_output_dir()
    startup_sync.sync()  # pull input data + seed DB from SharePoint (graceful)
    _data = DataStore()
    _defs = DefinitionStore(config.DB_PATH)
    baseline_model = _data.default_model()
    _defs.seed(
        [(c.name, c.definition) for c in _data.codebook()],
        _data.latest_kappa(baseline_model),
        baseline_model,
    )
    if not (config.AUTH_USERNAME and config.AUTH_PASSWORD):
        logging.getLogger("app").warning(
            "AUTH_USERNAME/AUTH_PASSWORD not set — login will reject all users.")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- login ------------------------------------------------------------------

@app.get("/login")
def login_page(next: str = "/"):
    return FileResponse(STATIC / "login.html")


@app.post("/login")
def login_submit(request: Request, username: str = Form(...),
                 password: str = Form(...), next: str = Form("/")):
    expected_u = config.AUTH_USERNAME or ""
    expected_p = config.AUTH_PASSWORD or ""
    ok = (expected_u and expected_p
          and hmac.compare_digest(username, expected_u)
          and hmac.compare_digest(password, expected_p))
    if not ok:
        return RedirectResponse("/login?error=1", status_code=302)
    request.session["user"] = username
    target = next if next.startswith("/") else "/"
    return RedirectResponse(target, status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# --- helpers ----------------------------------------------------------------

def _parse_codes(codes: str | None) -> list[str]:
    if not codes:
        return []
    return [c for c in (s.strip() for s in codes.split("||")) if c]


# --- API --------------------------------------------------------------------

@app.get("/api/models")
def api_models():
    return {"models": data().models(), "default": data().default_model()}


@app.get("/api/overview")
def api_overview(model: str | None = None):
    return data().overview(model or data().default_model())


@app.get("/api/docs")
def api_docs():
    return {"documents": data().sampled_docs()}


@app.get("/api/interview")
def api_interview(doc: str, model: str | None = None, codes: str | None = None):
    if doc not in data().sampled_docs():
        raise HTTPException(404, f"Unknown document: {doc}")
    model = model or data().default_model()
    current_defs = {k: v["definition"] for k, v in defs().all_current().items()}
    return data().interview_view(doc, model, _parse_codes(codes) or None, current_defs)


@app.get("/api/failures")
def api_failures(model: str | None = None, codes: str | None = None):
    code_list = _parse_codes(codes)
    if not code_list:
        raise HTTPException(400, "No codes selected.")
    return {"failures": data().failures(model or data().default_model(), code_list)}


@app.get("/api/definition")
def api_definition(code: str):
    history = defs().history(code)
    if not history:
        raise HTTPException(404, f"Unknown code: {code}")
    current = next((h for h in history if h["is_current"]), history[0])
    archived = [h for h in history if not h["is_current"]]
    initial = min(history, key=lambda h: h["version"])  # v1 = human starting point
    return {"code": code, "current": current, "archived": archived, "initial": initial}


@app.get("/api/context")
def api_context(doc: str, quote: str, start: int | None = None, end: int | None = None):
    if doc not in data().sampled_docs():
        raise HTTPException(404, f"Unknown document: {doc}")
    return data().context(doc, quote, start, end)


@app.get("/context")
def context_page():
    return FileResponse(STATIC / "context.html")


class DefinitionUpdate(BaseModel):
    code: str
    definition: str


@app.post("/api/definition")
def api_save_definition(body: DefinitionUpdate):
    if not body.definition.strip():
        raise HTTPException(400, "Definition cannot be empty.")
    return defs().save_new(body.code, body.definition.strip())


class ReanalyzeRequest(BaseModel):
    codes: list[str]
    scope: str = "one"  # "one" (default, OpenAI) | "all"


@app.post("/api/reanalyze")
def api_reanalyze(body: ReanalyzeRequest):
    if not body.codes:
        raise HTTPException(400, "No codes selected.")
    job_id = jobs.start_reanalyze(defs(), body.codes, body.scope)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    status = jobs.status(job_id)
    if status is None:
        raise HTTPException(404, "Unknown job.")
    return status


# --- static SPA -------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
