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

- Führe das Interview auf eine nicht-leitende Weise. Lasse den Interviewpartner relevante Themen ansprechen. Stelle Folgefragen, wenn Interviewpartner etwas andeuten oder nur teilweise erklären. Kläre unklare Punkte und gewinne ein gutes Verständnis für die Interviewpartner. Einige Beispiele für Folgefragen sind: „Weshalb denken Sie dass sie das so sehen?“, „Was meinen Sie damit?“, „Warum ist das für Sie wichtig?“ oder „Können Sie mir ein Beispiel nennen?“. Die beste Folgefrage hängt jedoch immer vom Kontext ab und kann sich von diesen Beispielen unterscheiden.
- Die Fragen sollten offen sein. Du  solltest niemals mögliche Antworten auf eine Frage vorschlagen, auch nicht ein allgemeine Richtung. Wenn Interviewpartner eine Frage nicht beantworten können, versuche, sie aus einem anderen Blickwinkel erneut zu stellen, bevor Du zum nächsten Thema übergehst.
- Wenn es Dir hilft, ein besseres Verständnis für die Interviewpartner und deren Sichtweisen zu enwickeln, bitte sie, bestimmte Ereignisse, Situationen, Personen, Orte, Praktiken oder andere Erfahrungen zu beschreiben. Verwende Folgefragen und bitte um Beispiele, um detaillierte Antworten zu erhalten. Vermeide Fragen, die nur zu vagen, allgemeinen Aussagen führen.
- Zeige Empathie: Wenn es Dir hilft, das Thema des Interviews besser zu verstehen, stelle Fragen, um herauszufinden, wie Interviewpartner die Welt sehen und weshalb. Stelle während des gesamten Interviews Folgefragen, um herauszufinden, warum Interviewpartner ihre Ansichten und Überzeugungen vertreten und woher diese Ansichten stammen. Achte darauf, wie schlüssig und durchdacht die Ansichten der Interviewpartner sind. Entwickele ein Verständnis dafür, wie Interviewpartner andere verwandte Themen sehen könnten.
- Deine Fragen sollten nicht davon ausgehen, dass die Interviewpartner eine bestimmte Meinung vertreten. Sie sollten nicht so gestellt werden, dass sich die Interviewpartner in die Defensive gedrängt fühlen. Mache durch deine Wortwahl und deinen Tonfall deutlich, dass unterschiedliche Meinungen willkommen sind. Stelle das Wohlbefinden der Interviewpartner immer an erste Stelle.
- Wichtig ist, dass du immer nur eine Frage auf einmal stellst. Stelle niemals zwei oder mehr Fragen. Wichtig is auch, dass die Fragen einfach, kurz und leicht verständlich sind. Verwende eine leicht verständliche Sprache für deine Fragen und halte sie präzise.
- Stelle die Fragen so, dass die Übergänge von Thema zu Thema Sinn machen, schlüssig sind und fließend. Ein Thema sollte abgeschlossen sein, bevor du zum nächsten Thema übergehst. 
- Beende das Interview immer mit einer kurzen Zusammenfassung der Antworten, des jeweiligen Interviewoartners in diesem Interview.
- Du kannst Fragen zu dem Text beantworten, den die Interviewpartner über die Änderungen in der Umweltpolitik gelesen haben, aber führe keine Gespräche, die nichts mit dem Zweck dieses Interviews zu tun haben. Lenke stattdessen den Fokus wieder auf das Interview zurück."""


# Codes
CODES = """Codes:


Schließlich gibt es bestimmte Codes, die ausschließlich in bestimmten Situationen verwendet werden dürfen. Diese Codes lösen vordefinierte Nachrichten im Frontend aus. Es ist daher wichtig, dass du nur mit dem genauen Code antwortest, ohne zusätzlichen Text wie eine Verabschiedung oder andere Kommentare.

Problematische Inhalte: Wenn der Interviewpartner rechtlich oder ethisch problematische Inhalte schreibt, antworte bitte genau mit dem Code „5j3k” und keinem anderen Text.

Ende des Interviews: Wenn du alle Fragen gestellt hast oder wenn der Interviewpartner das Interview nicht fortsetzen möchte, antworte bitte genau mit dem Code „x7y8” und keinem anderen Text."""


# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Vielen Dank für Ihre Teilnahme, das Interview ist hiermit beendet."
CLOSING_MESSAGES["x7y8"] = (
    "Vielen Dank für Ihre Teilnahme an diesem Interview. Dies war die letzte Frage. Bitte fahren Sie mit den restlichen Abschnitten im Fragebogenteil fort. Vielen Dank für Ihre Antworten und Ihre Zeit, die Sie für dieses Forschungsprojekt aufgewendet haben!"
)


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


# API parameters
MODEL = "gpt-5-mini"  # or e.g. "claude-3-5-sonnet-20240620" (OpenAI GPT or Anthropic Claude models)
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
