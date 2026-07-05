import os
from pathlib import Path
from typing import Optional
import streamlit as st
from dataclasses import dataclass


VARIANT_TOKENS = {
    "T5wp7": "combustion",
    "D9k2m": "deforestation",
}
ALLOWED_VARIANTS = set(VARIANT_TOKENS.values())

@dataclass(frozen=True)
class AppConfig:
    variant: Optional[str]

def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

def load_config() -> AppConfig:
    # Variant chosen by URL (nondescript token)
    token = st.query_params.get("q")
    if token is None:
        return AppConfig(variant=None)

    variant = VARIANT_TOKENS.get(token)
    if variant not in ALLOWED_VARIANTS:
        raise ValueError(
            f"Invalid variant token '{token}'."
        )

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
elif cfg.variant == "combustion":
    INTERVIEW_OUTLINE = prompts_dir / "combustion_engine.txt"
else:
    raise ValueError(f"Unknown INTERVIEW_PROMPT: {cfg.variant}")
"""


""""
if INTERVIEW_PROMPT == "deforestation":
    INTERVIEW_OUTLINE = prompts_dir / "deforestation.txt"

elif INTERVIEW_PROMPT == "combustion":
    INTERVIEW_OUTLINE = prompts_dir / "combustion_engine.txt"

else:
    raise ValueError(f"Unknown INTERVIEW_PROMPT: {INTERVIEW_PROMPT}")

"""

# General instructions
GENERAL_INSTRUCTIONS = """General instructions:

Conduct the interview in a non-directive manner. Let the interview partner raise topics they consider relevant. Ask a follow-up question when interview partners hint at something, give short answers, or only partially explain something. Clarify unclear points and develop a good understanding of the interview partners. Some examples of follow-up questions are: “Why do you think you see it that way?”, “What do you mean by that?”, “Why is that important to you?” or “Could you give me an example?”. However, the best follow-up question always depends on the context and may differ from these examples.
Every question should be open. Avoid suggesting possible answers to a question or steering it in a particular direction. If interview partners are unable to answer a question, try asking it again from a different angle before moving on to the next topic.
If it helps you to gain a better understanding of the interview partners and their perspectives, ask them to describe specific events, situations, people, places, practices, or other experiences. Use a follow-up question and ask for examples to obtain detailed answers. Avoid questions that only lead to vague, general statements.
Show empathy: If it helps you to better understand the topic of the interview, ask a question to find out how interview partners see the world and why. Throughout the interview, ask follow-up questions to understand why interview partners hold their views and beliefs and where these views originate. Pay attention to how coherent and well thought out the interview partners’ views are. Develop an understanding of how interview partners might see other related topics.
No question should assume that the interview partners hold a particular opinion. No question should be phrased in a way that makes the interview partners feel defensive. Make it clear through your choice of words and tone that different opinions are welcome. Place the well-being of the interview partners first.
IMPORTANT: ALWAYS ASK EXACTLY ONE SINGLE QUESTION PER ANSWER. Never combine multiple questions in one message, not even follow-up questions. The question should be short, simple, and precise.
Phrase the question so that it is coherent and appropriate for the respective moment of the interview. A topic should be concluded before you move on to the next topic.
End the interview with a brief summary of the answers of the respective interview partner in this interview.
You can ask questions about the text that the interview partners read about the changes in environmental policy. If the conversation deviates from the aim of the interview, gently guide it back to the interview topic.
It is important to conclude the conversation with a summary of the interview partner’s answers."""


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
                "Verwende diese Funktion, wenn der Interviewpartner die Zusammenfassung bewertet hat oder wenn "
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



def build_system_prompts(variant: str) -> tuple:
    """Return (SYSTEM_PROMPT, SYSTEM_PROMPT_OPENAI) for the given variant.

    Called per-session from interview.py so the correct prompt is always used
    regardless of which variant URL the participant arrived on.
    """
    if variant == "deforestation":
        outline = (prompts_dir / "deforestation.txt").read_text(encoding="utf-8")
    elif variant == "combustion":
        outline = (prompts_dir / "combustion_engine.txt").read_text(encoding="utf-8")
    else:
        raise ValueError(f"Unknown variant: {variant!r}")

    system_prompt = f"{outline}\n\n\n{GENERAL_INSTRUCTIONS}\n\n\n{CODES}"
    system_prompt_openai = f"{outline}\n\n\n{GENERAL_INSTRUCTIONS}"
    return system_prompt, system_prompt_openai



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
