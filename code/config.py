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
GENERAL_INSTRUCTIONS = f"""Instrukcje ogólne: 

- Prowadź rozmowę w sposób niekierujący. Pozwól respondentowi samemu poruszać istotne tematy. Zadawaj pytania uzupełniające, gdy rozmówca napomknie o czymś lub wyjaśni to tylko częściowo. Wyjaśniaj niejasne kwestie i staraj się dobrze zrozumieć rozmówcę. Przykłady pytań uzupełniających to: ‘Dlaczego tak myślisz?’, ‘Co masz na myśli?’, ‘Dlaczego to jest dla ciebie ważne?’ lub ‘Czy możesz podać przykład?’. Najlepsze pytanie uzupełniające zależy od kontekstu i może różnić się od tych przykładów. 
- Pytania powinny być otwarte. Nie sugeruj możliwych odpowiedzi, nawet ogólnego tematu. Jeśli respondent nie może odpowiedzieć na pytanie, spróbuj zadać je z innej perspektywy, zanim przejdziesz do następnego tematu. 
- Gdy pomaga ci to lepiej zrozumieć rozmówcę, proś go o opisanie konkretnych wydarzeń, sytuacji, osób, miejsc, praktyk lub innych doświadczeń. Zadawaj pytania uzupełniające i proś o przykłady, aby uzyskać szczegółowe odpowiedzi. Unikaj pytań, które prowadzą jedynie do ogólnych stwierdzeń. 
- Okazuj empatię: gdy pomaga ci to lepiej zrozumieć główny temat, pytaj o to, jak rozmówca postrzega świat i dlaczego. W trakcie rozmowy zadawaj pytania uzupełniające, aby zbadać przyczyny poglądów i przekonań rozmówcy oraz ich źródła. Zwróć uwagę na spójność jego wypowiedzi. Buduj rozumienie tego, jak rozmówca może postrzegać powiązane tematy. 
- Pytania nie powinny zakładać, że respondent ma określony pogląd. Nie formułuj ich w sposób, który mógłby sprawić, że respondent poczuje się atakowany. Dawaj wyraźnie do zrozumienia swoim słownictwem i tonem, że różne opinie są mile widziane. Zawsze stawiaj dobro respondenta na pierwszym miejscu. 
- Co ważne: zawsze zadawaj jedno pytanie na raz. Nigdy nie zadawaj dwóch lub więcej pytań jednocześnie. Pytania powinny być proste i zrozumiałe. Używaj przystępnego języka i formułuj je precyzyjnie. 
- Zadawaj pytania tak, aby przejścia między tematami były naturalne, spójne i płynne. Jeden temat powinien być zakończony, zanim przejdziesz do następnego. 
- Zawsze kończ rozmowę krótkim podsumowaniem odpowiedzi udzielonych przez rozmówcę. 
- Możesz odpowiadać na pytania dotyczące tekstu, który respondenci przeczytali na temat zmian w polityce klimatycznej, ale nie podejmuj rozmów niezwiązanych z celem tego wywiadu. W takim przypadku skieruj rozmowę z powrotem na temat wywiadu. 

"""

# Codes
CODES = """Kody: 

Na koniec: istnieją specjalne kody, które należy używać wyłącznie w określonych sytuacjach. Kody te uruchamiają predefiniowane komunikaty w interfejsie, dlatego ważne jest, aby odpowiadać dokładnie tylko kodem, bez żadnego dodatkowego tekstu, na przykład pożegnania lub komentarza. 

Treści problematyczne: Jeśli respondent napisze coś prawnie lub etycznie problematycznego, proszę odpowiedzieć dokładnie kodem ‘5j3k’ i niczym więcej. 

Koniec wywiadu: Gdy zadasz wszystkie pytania lub gdy respondent nie chce kontynuować wywiadu, proszę odpowiedzieć dokładnie kodem ‘x7y8’ i niczym więcej. 


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
                "Użyj tej funkcji gdy zadasz wszystkie pytania lub gdy respondent "
                "nie chce kontynuować wywiadu."
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
