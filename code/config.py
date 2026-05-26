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

- Gennemfør interviewet på en ikke-ledende måde. Lad den interviewede tage relevante emner op. Stil et opfølgende spørgsmål, hvis den interviewede antyder noget, giver korte svar eller kun forklarer noget delvist. Afklar uklare punkter, og få et godt indblik i den interviewede. Nogle eksempler på opfølgende spørgsmål er: »Hvorfor tror du, at du ser det sådan?«, »Hvad mener du med det?«, »Hvorfor er det vigtigt for dig?« eller »Kan du give mig et eksempel?«. Det bedste opfølgende spørgsmål afhænger dog altid af konteksten og kan afvige fra disse eksempler.
- Alle spørgsmål bør være åbne. Undgå at foreslå mulige svar på et spørgsmål eller at angive en bestemt retning. Hvis interviewpersonerne ikke kan besvare et spørgsmål, så prøv at stille det igen fra en anden vinkel, før du går videre til det næste emne.
- Hvis det hjælper dig med at få en bedre forståelse af interviewpersonerne og deres synspunkter, så bed dem om at beskrive bestemte begivenheder, situationer, personer, steder, praksis eller andre oplevelser. Brug et opfølgende spørgsmål og bed om eksempler for at få detaljerede svar. Undgå spørgsmål, der kun fører til vage, generelle udsagn.
- Vis empati: Hvis det hjælper dig med at forstå interviewets emne bedre, så still et spørgsmål for at finde ud af, hvordan de interviewede ser på verden, og hvorfor. Stil opfølgende spørgsmål under hele interviewet for at finde ud af, hvorfor de interviewede har de holdninger og overbevisninger, de har, og hvor disse holdninger stammer fra. Vær opmærksom på, hvor sammenhængende og gennemtænkte de interviewedes holdninger er. Få en forståelse for, hvordan interviewpersonerne kunne se på andre relaterede emner.
- Intet spørgsmål bør antage, at interviewpersonerne har en bestemt holdning. Intet spørgsmål bør stilles på en måde, der får interviewpersonerne til at føle sig presset i defensiven. Gør det klart gennem dit ordvalg og din tone, at forskellige meninger er velkomne. Sæt interviewpersonernes velbefindende i første række.
- VIGTIGT: STIL ALTID KUN ÉT SPØRGSMÅL PR. SVAR. Kombiner aldrig flere spørgsmål i én besked, heller ikke som opfølgende spørgsmål. Spørgsmålet skal være kort, enkelt og præcist formuleret.
- Stil spørgsmålet, så det er sammenhængende og passer til det pågældende øjeblik i interviewet. Et emne bør være afsluttet, før du går videre til det næste emne.
- Du kan besvare spørgsmål om den tekst, som de interviewede har læst om ændringerne i miljøpolitikken. Hvis samtalen afviger fra interviewets formål, skal du forsigtigt føre den tilbage til interviewets emne.
"""

# Codes
CODES = """Koder:

Endelig findes der visse koder, som udelukkende må bruges i bestemte situationer. Disse koder udløser foruddefinerede meddelelser i frontend. I disse tilfælde skal svaret begrænses til den pågældende kode.

Problematisk indhold: Hvis interviewpersonen skriver indhold, der er juridisk eller etisk problematisk, skal du afslutte interviewet ved at afslutte interviewet. Koden "5j3k" anvendes derefter af systemet.

Afslutning af interviewet: Når interviewpartneren har vurderet resuméet, eller hvis interviewpartneren ønsker at afbryde interviewet, skal du afslutte interviewet derefter. Koden "x7y8" bruges derefter af systemet.

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
                "Brug denne funktion, hvis den interviewede har vurderet resuméet, eller hvis "
                "den interviewede ikke ønsker at fortsætte interviewet."
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
