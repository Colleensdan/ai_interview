# Deploying the Codebook Improvement app to Render

## Service settings

- **Root Directory:** `data_analysis`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
  - `python -m` ensures the working dir is importable (`config`, `db`, `app`).
  - `--workers 1` keeps the in-process background-job state consistent.
- **Health check path:** `/healthz`
- **Instance plan:** Starter or higher — **required**, because persistent disks
  are not available on the free plan.
- **Persistent disk:** name `data`, mount path **`/var/data`**, size 1 GB.

`render.yaml` (in `data_analysis/`) encodes all of the above as Infrastructure
as Code; you can also configure it by hand in the dashboard.

## Why no Dockerfile

All dependencies (fastapi, uvicorn, openai, python-docx → lxml, openpyxl,
python-dotenv, requests, itsdangerous, python-multipart) ship prebuilt
manylinux wheels and need **no system packages**. Render's native Python runtime
installs them cleanly, so a Dockerfile would add maintenance cost for no benefit.
(Playwright, used only for local screenshot tests, is **not** a runtime dep.)

## Environment variables to set in the Render dashboard

Non-secret (already in `render.yaml`, override if needed):

| Var | Value |
|-----|-------|
| `DATA_DIR` | `/var/data` |
| `DATABASE_PATH` | `/var/data/coding.sqlite` |
| `AICODE_OUTPUT_DIR` | `/var/data/outputs` |
| `AICODE_DATA_ROOT` | `/var/data/input` |
| `AICODE_SHAREPOINT_DIR` | `Test Data` |
| `CJBS_DEPLOYMENT_NAME` | `gpt-5-mini` (pinned — not a secret; keep it matching the data) |
| `PYTHON_VERSION` | `3.13.4` |

Secrets (set as `sync:false` — **never committed**):

| Var | Purpose |
|-----|---------|
| `CJBS_API_KEY`, `CJBS_API_ENDPOINT`, `CJBS_API_VERSION` | Azure OpenAI (deployment name is pinned in `render.yaml`, not here) |
| `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` | SharePoint (Microsoft Graph) auth |
| `SP_HOSTNAME`, `SP_SITE_PATH`, `SP_LIBRARY_NAME` | SharePoint site / library |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | the shared login (currently `Malte` / `Piotr`) |
| `SESSION_SECRET` | cookie signing — use Render's "Generate" |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY` | future models (optional, add when available) |

> The `.env` file is git-ignored and only used for **local** development; on
> Render everything comes from the dashboard.

## First boot / data population

On startup the app pulls the configured SharePoint folder (`Test Data`) into
`/var/data/input` and, if `/var/data/coding.sqlite` doesn't exist yet, seeds it
from `Test Data/coding_seed.sqlite` (uploaded by `upload_test_data.py`). So a
fresh disk self-populates — no manual copy needed. If you ever want to force a
re-download, set `AICODE_SP_REFRESH=1`.

Alternative manual seed (if you prefer not to route the DB through SharePoint):
open the Render **Shell** and copy a local `coding.sqlite` to
`/var/data/coding.sqlite`.

### Refreshing the data after a re-run (e.g. after the patch-2 coding change)

Changing what gets coded (patch 2: code the interviewee only) requires re-running
the analysis, then shipping the new results to the deployed app:

1. Locally: `python run_pipeline.py` (full re-run, ~30 min, real Azure cost).
2. Re-upload just the refreshed DB as the SharePoint seed:
   `python upload_test_data.py --seed-only`
3. Make Render pick it up — either delete `/var/data/coding.sqlite` in the
   Render **Shell** (it re-seeds from SharePoint on next boot) and redeploy, or
   re-run the pipeline directly in the Render Shell writing to `/var/data`.

## Verifying the persistent disk survives a restart

1. Sign in, run a **Re-Analyse** on a code, and note its new κ on the results
   screen (this writes to `/var/data/coding.sqlite`).
2. In the Render dashboard, **Manual Deploy → Restart** (or **Clear build cache
   & deploy**, which still keeps the disk) the service.
3. After it comes back, sign in again — the κ you just produced is still there
   (the Overview/definition history reflects it). If it reset to the seeded
   baseline, the data did **not** persist.
4. Quick check via the Render Shell:
   ```
   ls -la /var/data /var/data/outputs
   sqlite3 /var/data/coding.sqlite "SELECT COUNT(*) FROM kappa;"
   ```
   The file's modified time should pre-date the restart and the row counts
   should be non-zero and include your re-analysis run.
