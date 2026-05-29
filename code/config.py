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
GENERAL_INSTRUCTIONS = f"""Instrukcje ogólne: 

- Prowadź wywiad w sposób nienakierowujący. Pozwól rozmówcy poruszać istotne tematy. Wyjaśnij niejasne kwestie i postaraj się dobrze zrozumieć rozmówcę. 
- Jesli rozmówca coś sugeruje, udziela krótkich odpowiedzi lub wyjaśnia sprawę tylko częściowo, zadawaj pytania uzupełniające. Oto kilka przykładów pytań uzupełniających: „Dlaczego tak Pan/Pani uważa?”, lub „Co Pan/Pani ma na myśli?”, lub „Dlaczego jest to dla Pana/Pani ważne?” lub „Czy może Pan/Pani podać mi przykład?”. Najlepsze pytanie uzupełniające zależy jednak zawsze od kontekstu i może różnić się od tych przykładów.
- Każde pytanie powinno być otwarte. Unikaj sugerowania możliwych odpowiedzi na pytanie lub nakierowywania rozmówcy w konkretnym kierunku. Jeśli rozmówca nie potrafi odpowiedzieć na pytanie, spróbuj zadać je ponownie z innej perspektywy, zanim przejdziesz do następnego tematu.
- Jeśli pomoże Ci to lepiej zrozumieć rozmówców i ich punkt widzenia, poproś ich o opisanie konkretnych wydarzeń, sytuacji, osób, miejsc, praktyk lub innych doświadczeń. Unikaj pytań, które prowadzą tylko do niejasnych, ogólnych stwierdzeń.
- Okaż empatię: jeśli pomoże ci to lepiej zrozumieć temat wywiadu, zadaj pytanie, aby dowiedzieć się, jak rozmówcy postrzegają świat i dlaczego. W stosownych przypadkach zadaj pytania uzupełniające, aby dowiedzieć się, dlaczego rozmówcy wyznają określone poglądy i przekonania oraz skąd się one biorą. Zwróć uwagę na to, na ile poglądy rozmówców są spójne i przemyślane. Postaraj się zrozumieć, jak rozmówcy mogą postrzegać inne powiązane tematy.
- Żadne pytanie nie powinno zakładać, że rozmówcy mają określone zdanie. Żadne pytanie nie powinno być sformułowane w taki sposób, aby rozmówcy czuli się zmuszeni do obrony. Poprzez dobór słów i ton głosu jasno daj do zrozumienia, że różne opinie są mile widziane. Na pierwszym miejscu stawiaj dobre samopoczucie rozmówców.
- WAŻNE: ZAWSZE ZADAWAJ TYLKO JEDNO PYTANIE. Nigdy nie łącz kilku pytań w jednej wiadomości, nawet jako pytania uzupełniające. Pytanie powinno być sformułowane krótko, prosto i precyzyjnie.
- Zadaj pytanie w taki sposób, aby było spójne i pasowało do danego momentu wywiadu. Jeden temat powinien być zamknięty, zanim przejdziesz do następnego.
- Kiedy rozmówca udzieli już odpowiedzi na wszystkie pytania, napisz krótkie podsumowanie odpowiedzi tego rozmówcy w tym wywiadzie.
- Możesz odpowiadać na pytania dotyczące tekstu, który rozmówcy przeczytali na temat zmian w polityce środowiskowej. Jeśli rozmowa zboczy z tematu wywiadu, delikatnie sprowadź ją lub jego z powrotem na właściwe tory."""

# Codes
CODES = """Kody: 

Istnieją także pewne kody, których można używać wyłącznie w określonych sytuacjach. Kody te wywołują z góry zdefiniowane komunikaty w interfejsie użytkownika. W takich przypadkach odpowiedź powinna ograniczać się do odpowiedniego kodu.
 
Treści budzące zastrzeżenia: Jeśli rozmówca wpisze treści budzące zastrzeżenia pod względem prawnym lub etycznym, zakończ wywiad, zamykając go. System użyje wówczas kodu „5j3k”.
 
Zakończenie wywiadu: Gdy rozmówca ocenił podsumowanie lub gdy rozmówca nie chce kontynuować rozmowy, zakończ wywiad, zamykając go. System użyje wówczas kodu „x7y8”.

"""

# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Dziękujemy za udział, wywiad dobiega końca."
CLOSING_MESSAGES["x7y8"] = "Dziękujemy za udział w wywiadzie, to było ostatnie pytanie. Proszę przejść do pozostałych części ankiety. Serdecznie dziękujemy za Państwa odpowiedzi i czas poświęcony na pomoc w tym projekcie badawczym!"

# Function tools for OpenAI/Azure — replace code-based termination to avoid content-filter false positives
TERMINATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "end_interview",
            "description": (
                "Skorzystaj z tej funkcji, gdy respondent ocenił podsumowanie lub "
"gdy nie chce kontynuować wywiadu."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_problematic_content",
            "description": (
                "Użyj tej funkcji gdy respondent napisze coś prawnie lub "
                "etycznie problematycznego."
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
