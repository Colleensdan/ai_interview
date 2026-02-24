# Code for "Conversations at Scale: Robust AI-led Interviews with a Simple Open-Source Platform"

There are two options to explore the AI-led interviews discussed in the paper.

## Option 1: Online notebook

To try own ideas for interviews within minutes and without the need to install Python, see https://colab.research.google.com/drive/1sYl2BMiZACrOMlyASuT-bghCwS5FxHSZ (requires to obtain an API key)

## Option 2: Full platform

To install Python and set up the full interview platform locally (takes around 1h from scratch), see the following steps.

The interview platform is built using the library `streamlit` and the APIs of OpenAI and Anthropic.

- Download miniconda from https://docs.anaconda.com/miniconda/miniconda-install/ and install it (skip if `conda` is already installed)
- Obtain an API key from https://platform.openai.com/ or https://www.anthropic.com/api. In case of the OpenAI API, choose a "project" key
- Download this repository
- In the repository folder on your computer, paste your API key into the file `/code/.streamlit/secrets.toml` (requires to make hidden folders visible)
- In the config.py, select a language model and adjust the interview outline
- In Terminal (Mac) or Anaconda Prompt (Windows), navigate to the folder `code` with `cd` (if unclear, briefly look up basic Linux command line syntax for navigating to folders)
- Once in the `code` folder, create the environment from the .yml file by writing `conda env create -f interviewsenv.yml` and confirming with enter (this installs Python and all libraries necessary to run the platform; only needs to be done once)
- Activate the environment with `conda activate interviews`
- Start the platform with `streamlit run interview.py`

## Variant URLs (nondescript tokens)

The app selects interview variants using a nondescript query token instead of a descriptive name.

Mapping (edit in `code/config.py`):

```
combustion     -> /?q=T5wp7
deforestation  -> /?q=D9k2m
```

## Security hardening

Recent updates add guardrails so the model stays aligned with the study protocol:

- API keys can now be supplied via Streamlit secrets, environment variables, or a local `.streamlit/secrets.toml`, making misconfiguration less likely.
- Every respondent message is flagged as untrusted before it reaches the model, and the upstream system prompt is always inserted first to keep higher authority than user inputs.
- A prompt-injection detector blocks common jailbreak attempts (e.g., “ignore previous instructions”, “show your chain of thought”, “unaligned”) and responds with a generic refusal before continuing the interview.
- The chat input blocks paste events to ensure respondents type responses directly rather than pasting prepared text.

## Interview data pseudonymisation

Interview transcripts are now sanitised immediately before they touch disk. The pipeline uses spaCy NER plus light rule logic to replace directly identifying entities with placeholders (e.g. `<name>`, `<organisation>`, `<place>`, `<group>`, `<date-2023>`). Named facilities are generalised to their type (`<hospital>`, `<airport>`), while generic mentions such as “a hospital” are left untouched so analytics remain meaningful. If governance teams need reversible audits, pass a `mapping_handler` to `save_interview_data` to persist the optional mapping table outside the stored transcript. Install the dependency stack by running `pip install -r code/requirements.txt` followed by `python -m spacy download en_core_web_sm` when setting up a new environment.

### Mapping export (beta)

There is an experimental (beta) CLI flow for persisting pseudonymisation mapping tables to disk for governance review:

- Enable mappings with `streamlit run interview.py -- --enable-mapping`. This attempts to write JSON mapping files to `secure/audit/mapping/` alongside the repo.
- Override the destination via `--mapping-dir /path/to/folder` or the environment variable `AI_INTERVIEW_MAPPING_DIR`. You can also flip the feature on via `AI_INTERVIEW_ENABLE_MAPPING=1` instead of the CLI flag.
- Each interview save writes `{username}_{timestamp}.json` files containing the reversible mappings observed during that run.

**Known issue (incomplete):** the current implementation does not yet reliably create the default `secure/audit/mapping/` directory or persist JSON files there. Treat this feature as beta-only until the underlying path-resolution bug is fixed.


## Qualtrics integration (participant ID handoff)

The app receives a unique Qualtrics ResponseID from the survey platform and uses it as the filename for all stored interview data. This links each transcript unambiguously to a Qualtrics response record without storing any personally identifiable information.

### How participant IDs are assigned

| Source | Stored as |
|---|---|
| Qualtrics `?pid=<ResponseID>` in iframe URL | `<ResponseID>.txt` (e.g. `R_1a2b3c4d.txt`) |
| Direct URL visit (no `pid`) | `non-qualtrics-participant-YYYYMMDD_HHMMSS.txt` |

The ID is locked in for the entire session the moment the page first loads. It cannot change mid-interview.

### Required Qualtrics JS (recommended approach)

The most reliable method is to append `pid` directly to the iframe `src` URL before the frame loads. Streamlit reads it from `st.query_params` on the very first Python execution — no JavaScript timing dependency.

Replace your existing Qualtrics JS with:

```javascript
Qualtrics.SurveyEngine.addOnReady(function() {
    var resID = "${e://Field/ResponseID}";
    var iframe = document.getElementById('ai-interview-frame');

    // Primary: append pid to the iframe URL so Streamlit reads it server-side
    // on first load — reliable regardless of JS timing.
    var src = iframe.src || iframe.getAttribute('src') || '';
    if (src && resID) {
        var sep = src.indexOf('?') !== -1 ? '&' : '?';
        iframe.src = src + sep + 'pid=' + encodeURIComponent(resID);
    }

    // Fallback: also send a postMessage after load (caught by the Streamlit
    // listener if it has had time to register, causing a redirect with ?pid=).
    iframe.onload = function() {
        iframe.contentWindow.postMessage(
            { type: 'QUALTRICS_ID', participantId: resID },
            'https://ai-interview-en11.onrender.com'
        );
    };
});
```

### Why two mechanisms?

The Streamlit app also contains a JavaScript `postMessage` listener (`_register_qualtrics_listener` in `interview.py`). If the listener receives a `QUALTRICS_ID` message and the URL does not yet contain `?pid=`, it performs a `location.replace` redirect that appends the ID and restarts the session with the correct filename.

However, Streamlit's component iframes render *after* the page's `load` event fires, so the listener may not be active in time to catch the very first postMessage. The URL parameter approach has no such timing dependency and should always be used as the primary method. The postMessage listener is belt-and-suspenders.

## SharePoint storage (Render deployment)

When hosted on Render, interview data is written to **both** the server's local disk and a SharePoint Online document library via the Microsoft Graph API. Local disk on Render is ephemeral — files are lost on any restart or redeploy — so SharePoint is the only durable copy.

### How it works

`save_interview_data` is called at multiple points during the interview:

| When | What is saved |
|---|---|
| Immediately after the first interviewer message | Backup transcript + times → `incoming/backups/` |
| After **every** subsequent assistant message | Backup transcript + times → `incoming/backups/` |
| Interview end (closing code or Quit) | Final transcript → `incoming/transcripts/`, times → `incoming/times/` |

The folder layout in SharePoint mirrors the local `data/` directory:

```
InterviewData/
└── incoming/
    ├── transcripts/   ← final transcripts only
    ├── times/         ← final time files only
    └── backups/       ← incremental backups after every message
```

The upload layer (`code/sharepoint.py`) retries up to three times with exponential backoff before giving up, so transient network blips recover automatically. All failures are written to stderr and appear in Render's log dashboard.

### Setting environment variables on Render

> **This is the most common reason SharePoint works locally but not on Render.**
> The `code/.env` file is listed in `.gitignore` and is never deployed. You must add each variable individually in the Render dashboard.

1. Open your service on [render.com](https://render.com)
2. Go to **Environment** → **Environment Variables**
3. Add each of the following:

| Variable | Value (from your `code/.env`) |
|---|---|
| `TENANT_ID` | Azure AD tenant ID |
| `CLIENT_ID` | App registration client ID |
| `CLIENT_SECRET` | App registration client secret |
| `SP_HOSTNAME` | SharePoint hostname (e.g. `yourorg.sharepoint.com`) |
| `SP_SITE_PATH` | Site-relative path (e.g. `/sites/MySite`) |
| `SP_LIBRARY_NAME` | Document library name (e.g. `InterviewData`) |
| `SP_TARGET_FOLDER` | Target folder within the library (e.g. `incoming`) |

You also need the Azure OpenAI variables on Render for the same reason:

| Variable | Description |
|---|---|
| `CJBS_API_KEY` | Azure OpenAI API key |
| `CJBS_API_ENDPOINT` | Azure OpenAI endpoint URL |
| `CJBS_API_VERSION` | API version (e.g. `2024-10-21`) |
| `CJBS_DEPLOYMENT_NAME` | Deployment name (e.g. `gpt-4.1-nano`) |

4. Click **Save** — Render redeploys automatically.

The app registration must have the `Sites.Selected` application permission with write access granted to the specific site.

### Startup health check

On the first page load of each session the app verifies SharePoint connectivity (token acquisition + drive resolution) before any interview data is collected. If the check fails, a prominent red banner is shown with the error detail. The banner persists for the entire session, so a misconfigured or broken connection cannot go unnoticed.

If SharePoint is unavailable, the interview continues and data is saved locally, but the banner remains visible and all errors are logged to Render.

## Paper and citation

The paper is available at https://ssrn.com/abstract=4974382 and can be cited with the following bibtex entry:

```
@article{geieckejaravel2024,
  title={Conversations at Scale: Robust AI-led Interviews with a Simple Open-Source Platform},
  author={Geiecke, Friedrich and Jaravel, Xavier},
  url={https://ssrn.com/abstract=4974382},
  year={2024}
}
```
