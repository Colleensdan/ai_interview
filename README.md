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
