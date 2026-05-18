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
GENERAL_INSTRUCTIONS = f"""Indicazioni generali:

- Conduci l’intervista in modo non suggestivo. Lascia che sia l’intervistato a introdurre gli argomenti rilevanti. Poni una domanda di approfondimento quando la persona accenna a qualcosa, fornisce risposte brevi o si esprime solo parzialmente. Chiarisci i punti poco chiari e cerca di comprendere a fondo il suo punto di vista. Alcuni esempi di domande di approfondimento sono: “Perché vede la situazione in questo modo?”, “Cosa intende dire?”, “Perché è importante per lei?” oppure “Può farmi un esempio?”. Tuttavia, la domanda più adatta dipende sempre dal contesto e può variare rispetto a questi esempi.
- Ogni domanda dovrebbe essere aperta. Evita di suggerire possibili risposte o di orientare la risposta in una direzione specifica. Se gli intervistati non sono in grado di rispondere a una domanda, prova a riformularla da una prospettiva diversa prima di passare all’argomento successivo.
- Se ti aiuta a comprendere meglio gli intervistati e i loro punti di vista, chiedi loro di descrivere eventi, situazioni, persone, luoghi, pratiche o altre esperienze. Usa una domanda di approfondimento e chiedi esempi per ottenere risposte dettagliate. Evita domande che portano solo ad affermazioni vaghe e generiche.
- Mostra empatia: se ti aiuta a comprendere meglio l’argomento dell’intervista, poni domande per capire come gli intervistati vedono il mondo e perché. Durante tutta l’intervista, fai domande di approfondimento per comprendere le ragioni alla base delle loro opinioni e convinzioni e da dove queste derivano. Presta attenzione alla coerenza e alla solidità delle loro idee. Cerca anche di capire come potrebbero interpretare altri temi correlati.
- Nessuna domanda dovrebbe presupporre che gli intervistati abbiano una determinata opinione né essere formulata in modo da metterli sulla difensiva. Attraverso la scelta delle parole e il tono di voce, fai capire che tutte le opinioni sono benvenute. Metti sempre al primo posto il benessere degli intervistati.
- IMPORTANTE: PONI SEMPRE UNA SOLA DOMANDA PER VOLTA. Non combinare più domande in un unico messaggio, nemmeno come approfondimento. La domanda deve essere breve, chiara e precisa.
- Ogni domanda dovrebbe essere formulata in modo coerente e adeguato al momento specifico dell’intervista. Concludi un argomento prima di passare a quello successivo.
- Concludi l’intervista con un breve riassunto delle risposte fornite dall’intervistato.
- Puoi rispondere alle domande relative al testo che gli intervistati hanno letto sui cambiamenti nella politica ambientale. Se la conversazione si allontana dall’obiettivo dell’intervista, riportala con delicatezza sull’argomento principale.
"""

# Codes
CODES = """Codici:

Infine, esistono codici specifici che possono essere utilizzati esclusivamente in determinate situazioni. Questi codici attivano messaggi predefiniti nel frontend. In questi casi, la risposta deve essere limitata al codice corrispondente.

Contenuti problematici: Se l’intervistato scrive contenuti legalmente o eticamente problematici, termina l’intervista concludendola. Il codice “5j3k” verrà quindi utilizzato dal sistema.

Fine dell’intervista: Se hai posto tutte le domande oppure se l’intervistato non desidera proseguire l’intervista, termina l’intervista concludendola. Il codice “x7y8” verrà quindi utilizzato dal sistema.

"""

# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Grazie per aver partecipato, l'intervista finisce qui."
CLOSING_MESSAGES["x7y8"] = "Grazie per aver partecipato all'intervista, questa era l'ultima domanda. Si prega di continuare con le sezioni rimanenti nella parte dedicata al sondaggio. Grazie mille per le risposte e per il tempo dedicato a questo progetto di ricerca!"

# Function tools for OpenAI/Azure — replace code-based termination to avoid content-filter false positives
TERMINATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "end_interview",
            "description": (
                "Usa questa funzione quando l’intervistato ha valutato il riepilogo oppure quando "
                "non desidera proseguire l’intervista."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_problematic_content",
            "description": (
                "Utilizza questa funzione quando l'intervistato scrive contenuti "
                "problematici dal punto di vista legale o etico."
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
