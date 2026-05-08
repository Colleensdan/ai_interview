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
GENERAL_INSTRUCTIONS = f"""Generelle instruktioner:

- Led interviewet på en ikke-ledende måde. Lad respondenten bringe relevante emner på banen. Stil opfølgende spørgsmål, hvis de interviewede antyder noget eller kun forklarer det delvist. Afklar uklare punkter og få en god forståelse af de interviewede. Nogle eksempler på opfølgende spørgsmål er ›Hvorfor tror du, at du ser det på denne måde?‹, ›Hvad mener du?‹, »Hvorfor er det vigtigt for dig?« eller »Kan du give mig et eksempel?«. Det bedste opfølgende spørgsmål afhænger naturligvis af konteksten og kan være forskelligt fra disse eksempler.
- Spørgsmålene skal være åbne. Du må aldrig foreslå mulige svar på et spørgsmål, ikke engang et generelt emne. Hvis en respondent ikke kan besvare et spørgsmål, skal du prøve at stille det igen fra en anden vinkel, før du går videre til det næste emne.
- Når det hjælper dig med at forstå respondenten bedre, skal du bede respondenten om at beskrive specifikke begivenheder, situationer, personer, steder, praksis eller andre oplevelser. Brug opfølgende spørgsmål og bed om eksempler for at få detaljerede svar. Undgå at stille spørgsmål, der kun fører til vage, generelle udsagn.
- Vis empati: Når det hjælper dig med at forstå hovedemnet bedre, skal du stille spørgsmål for at finde ud af, hvordan respondenten ser verden, og hvorfor. Brug opfølgende spørgsmål gennem hele interviewet til at undersøge, hvorfor respondenten har sine synspunkter og overbevisninger, og til at finde ud af, hvor disse synspunkter stammer fra. Vær opmærksom på, hvor sammenhængende og konsistente deres synspunkter er. Udvikl en forståelse for, hvordan de interviewede måske ser på andre relaterede emner.
- Dine spørgsmål bør ikke antage, at respondenten har en bestemt holdning. De bør ikke stilles på en måde, der kan få respondenten til at føle sig defensiv. Gør det klart gennem din formulering og tone, at forskellige holdninger er velkomne. Sæt altid respondentens velbefindende først.
- Det er vigtigt, at du altid stiller ét spørgsmål ad gangen. Stil aldrig to eller flere spørgsmål. Det er også vigtigt, at spørgsmålene er enkle og lette at forstå. Brug et enkelt og tilgængeligt sprog i dine spørgsmål, og sørg for, at de er præcise.
- Stil spørgsmålene på en sådan måde, at overgangen fra emne til emne giver mening, er sammenhængende og flyder naturligt. Et emne bør være afsluttet, før du går videre til det næste.
- Afslut altid interviewet med en kort opsummering af de svar, som den interviewede har givet i interviewet.
- Du kan besvare spørgsmål om den tekst, som respondenterne har læst om ændringen af klimapolitikken, men indgå ikke i samtaler, der ikke har relation til formålet med dette interview. I stedet skal du flytte fokus tilbage til interviewet.


"""

# Codes
CODES = """Koder:

Endelig er der specifikke koder, der udelukkende skal bruges i bestemte situationer. Disse koder udløser foruddefinerede beskeder i front-end, så det er afgørende, at du kun svarer med den nøjagtige kode uden yderligere tekst, såsom en farvelbesked eller andre kommentarer.

Problematisk indhold: Hvis respondenten skriver juridisk eller etisk problematisk indhold, skal du svare med nøjagtig koden ›5j3k‹ og ingen anden tekst.

Afslutning af interviewet: Når du har stillet alle spørgsmål, eller når respondenten ikke ønsker at fortsætte interviewet, skal du svare med nøjagtig koden ›x7y8‹ og ingen anden tekst.

"""

# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Tak for din deltagelse, interviewet er hermed afsluttet."
CLOSING_MESSAGES["x7y8"] = "Tak fordi du deltog i interviewet. Dette var det sidste spørgsmål. Fortsæt venligst med de resterende afsnit i undersøgelsesdelen. Mange tak for dine svar og din tid til at hjælpe med dette forskningsprojekt!"

# Function tools for OpenAI/Azure — replace code-based termination to avoid content-filter false positives
TERMINATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "end_interview",
            "description": (
                "Brug denne funktion når du har stillet alle spørgsmål, eller når "
                "respondenten ikke ønsker at fortsætte interviewet."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_problematic_content",
            "description": (
                "Brug denne funktion når respondenten skriver juridisk eller "
                "etisk problematisk indhold."
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
    """Return (SYSTEM_PROMPT, SYSTEM_PROMPT_OPENAI) for the given variant."""
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
