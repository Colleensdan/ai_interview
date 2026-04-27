import os
from pathlib import Path
import streamlit as st
from dataclasses import dataclass


VARIANT_TOKENS = {
    "T5wp7": "combustion",
    "D9k2m": "deforestation",
}
ALLOWED_VARIANTS = set(VARIANT_TOKENS.values())

@dataclass(frozen=True)
class AppConfig:
    variant: str

def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

def load_config() -> AppConfig:
    # Variant chosen by URL (nondescript token)
    token = st.query_params.get("q")
    if token is None:
        variant = "deforestation"
    else:
        variant = VARIANT_TOKENS.get(token)

    if variant not in ALLOWED_VARIANTS:
        raise ValueError(
            f"Invalid variant token '{token}'."
        )

    """
    variants = st.secrets.get("variants")
    if not variants or variant not in variants:
        raise RuntimeError(
            f"Missing secrets for variant '{variant}'"
        )


    vcfg = variants[variant]
    """

    return AppConfig(
        variant=variant
    )


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
prompts_dir = PROJECT_ROOT / "prompts"

"""
# Interview outline


try:
    cfg = load_config()
except Exception as e:
    st.error("Configuration error")
    st.code(str(e))
    st.stop()



#INTERVIEW PROMPTS
if cfg.variant == "deforestation":
    INTERVIEW_OUTLINE = prompts_dir / "deforestation.txt"
elif cfg.variant == "combustion_engine":
    INTERVIEW_OUTLINE = prompts_dir / "combustion_engine.txt"
else:
    raise ValueError(f"Unknown INTERVIEW_PROMPT: {cfg.variant}")
"""


""""
if INTERVIEW_PROMPT == "deforestation":
    INTERVIEW_OUTLINE = prompts_dir / "deforestation.txt"

elif INTERVIEW_PROMPT == "combustion_engine":
    INTERVIEW_OUTLINE = prompts_dir / "combustion_engine.txt"

else:
    raise ValueError(f"Unknown INTERVIEW_PROMPT: {INTERVIEW_PROMPT}")

"""

# General instructions
GENERAL_INSTRUCTIONS = """Allgemeine Hinweise:

- Führe das Interview auf eine nicht-leitende Weise. Lasse den Interviewpartner relevante Themen ansprechen. Stelle eine Folgefrage, wenn Interviewpartner etwas andeuten, kurze Antworten geben, oder nur teilweise erklären. Kläre unklare Punkte und gewinne ein gutes Verständnis für die Interviewpartner. Einige Beispiele für Folgefragen sind: „Weshalb denken Sie dass sie das so sehen?”, „Was meinen Sie damit?”, „Warum ist das für Sie wichtig?” oder „Können Sie mir ein Beispiel nennen?”. Die beste Folgefrage hängt jedoch immer vom Kontext ab und kann sich von diesen Beispielen unterscheiden.
- Deine Fragen sollten offen sein. Vermeide es, mögliche Antworten auf eine Frage vorzuschlagen oder eine bestimmte Richtung vorzugeben. Wenn Interviewpartner eine Frage nicht beantworten können, versuche, sie aus einem anderen Blickwinkel erneut zu stellen, bevor Du zum nächsten Thema übergehst.
- Wenn es Dir hilft, ein besseres Verständnis für die Interviewpartner und deren Sichtweisen zu enwickeln, bitte sie, bestimmte Ereignisse, Situationen, Personen, Orte, Praktiken oder andere Erfahrungen zu beschreiben. Verwende Folgefragen und bitte um Beispiele, um detaillierte Antworten zu erhalten. Vermeide Fragen, die nur zu vagen, allgemeinen Aussagen führen.
- Zeige Empathie: Wenn es Dir hilft, das Thema des Interviews besser zu verstehen, stelle Fragen, um herauszufinden, wie Interviewpartner die Welt sehen und weshalb. Stelle während des gesamten Interviews Folgefragen, um herauszufinden, warum Interviewpartner ihre Ansichten und Überzeugungen vertreten und woher diese Ansichten stammen. Achte darauf, wie schlüssig und durchdacht die Ansichten der Interviewpartner sind. Entwickele ein Verständnis dafür, wie Interviewpartner andere verwandte Themen sehen könnten.
- Deine Fragen sollten nicht davon ausgehen, dass die Interviewpartner eine bestimmte Meinung vertreten. Sie sollten nicht so gestellt werden, dass sich die Interviewpartner in die Defensive gedrängt fühlen. Mache durch deine Wortwahl und deinen Tonfall deutlich, dass unterschiedliche Meinungen willkommen sind. Stelle das Wohlbefinden der Interviewpartner an erste Stelle.
- WICHTIG: Stelle immer nur genau eine einzige Frage pro Antwort. Kombiniere niemals mehrere Fragen in einer Nachricht, auch nicht als Folgefragen. Die Frage soll kurz, einfach und präzise formuliert sein.
- Stelle die Frage so, dass sie schlüssig ist und passend für den jeweiligen Moment des Interviews. Ein Thema sollte abgeschlossen sein, bevor Du zum nächsten Thema übergehst.
- Beende das Interview mit einer kurzen Zusammenfassung der Antworten, des jeweiligen Interviewpartners in diesem Interview.
- Du kannst Fragen zu dem Text beantworten, den die Interviewpartner über die Änderungen in der Umweltpolitik gelesen haben. Falls das Gespräch vom Ziel des Interviews abweicht, führe es behutsam zurück zum Interviewthema."""


# Codes
CODES = """Codes:


Schließlich gibt es bestimmte Codes, die ausschließlich in bestimmten Situationen verwendet werden dürfen. Diese Codes lösen vordefinierte Nachrichten im Frontend aus. In diesen Fällen soll die Antwort auf den entsprechenden Code beschränkt sein.

Problematische Inhalte: Wenn der Interviewpartner rechtlich oder ethisch problematische Inhalte schreibt, beende das Interview, indem du das Interview abschließt. Der Code ‚5j3k’ wird anschließend vom System verwendet.

Ende des Interviews: Wenn du alle Fragen gestellt hast oder wenn der Interviewpartner das Interview nicht fortsetzen möchte, beende das Interview, indem du das Interview abschließt. Der Code ‚x7y8’ wird anschließend vom System verwendet."""


# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Vielen Dank für Ihre Teilnahme, das Interview ist hiermit beendet."
CLOSING_MESSAGES["x7y8"] = (
    "Vielen Dank für Ihre Teilnahme an diesem Interview. Dies war die letzte Frage. Bitte fahren Sie mit den restlichen Abschnitten im Fragebogenteil fort. Vielen Dank für Ihre Antworten und Ihre Zeit, die Sie für dieses Forschungsprojekt aufgewendet haben!"
)

# Function tools for OpenAI/Azure — replace code-based termination to avoid content-filter false positives
TERMINATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "end_interview",
            "description": (
                "Verwende diese Funktion, wenn du alle Fragen gestellt hast oder wenn "
                "der Interviewpartner das Interview nicht fortsetzen möchte."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_problematic_content",
            "description": (
                "Verwende diese Funktion, wenn der Interviewpartner rechtlich oder "
                "ethisch problematische Inhalte schreibt."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_CLOSING_MESSAGES = {
    "end_interview":            CLOSING_MESSAGES["x7y8"],
    "flag_problematic_content": CLOSING_MESSAGES["5j3k"],
}

cfg = load_config()

if cfg.variant == "deforestation":
    INTERVIEW_OUTLINE = (prompts_dir / "deforestation.txt").read_text(encoding="utf-8")
elif cfg.variant == "combustion":
    INTERVIEW_OUTLINE = (prompts_dir / "combustion_engine.txt").read_text(encoding="utf-8")
else:
    raise ValueError(f"Unknown INTERVIEW_PROMPT: {cfg.variant}")


# System prompt
SYSTEM_PROMPT = f"""{INTERVIEW_OUTLINE}


{GENERAL_INSTRUCTIONS}


{CODES}"""

# System prompt for OpenAI/Azure — omits CODES because tool calling handles termination
SYSTEM_PROMPT_OPENAI = f"""{INTERVIEW_OUTLINE}


{GENERAL_INSTRUCTIONS}"""


# API parameters
# Reads from CJBS_DEPLOYMENT_NAME env var so you can swap models without touching code.
# Falls back to gpt-4o if the var is unset (e.g. local dev without a .env).
MODEL = os.getenv("CJBS_DEPLOYMENT_NAME", "gpt-4o")
TEMPERATURE = None  # (None for default value)
MAX_OUTPUT_TOKENS = 2048


# Display login screen with usernames and simple passwords for studies
LOGINS = False


# Directories
TRANSCRIPTS_DIRECTORY = "../data/transcripts/"
TIMES_DIRECTORY = "../data/times/"
BACKUPS_DIRECTORY = "../data/backups/"


# Avatars displayed in the chat interface
AVATAR_INTERVIEWER = "\U0001F393"
AVATAR_RESPONDENT = "\U0001F9D1\U0000200D\U0001F4BB"
