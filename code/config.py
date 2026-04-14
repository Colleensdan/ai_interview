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
GENERAL_INSTRUCTIONS = f"""Durante l'intervista, ponete fino a circa 12 domande per capire come gli intervistati reagiscono all'allentamento delle politiche climatiche da parte dell'Unione Europea. Concentratevi sul loro comportamento, sulle loro emozioni e convinzioni.  Gli intervistati hanno letto il seguente testo, sul quale potete basare le vostre domande:

[L'UE vuole allentare il divieto previsto sui motori a combustione

Le auto con motori a combustione bruciano benzina o diesel e inquinano l'ambiente. Emettono gas serra e contribuiscono al cambiamento climatico. Per questo motivo, nel marzo 2023 la Commissione europea ha deciso di vietare la vendita di auto nuove con motori a combustione a partire dal 2035. Il divieto faceva parte del Green Deal dell'Unione europea, un pacchetto di misure volte a raggiungere la neutralità climatica entro il 2050. Ma l’atmosfera politica è cambiata. Diverse misure di politica ambientale sono ora in fase di rivalutazione e revisione.

Un esempio lampante è l'allentamento da parte della Commissione del divieto sui veicoli con motori a combustione. Secondo la nuova proposta, i nuovi veicoli prodotti dovranno emettere in media solo il 90% in meno di CO₂, invece che zero CO₂. Ciò lascia spazio alla vendita continuativa di veicoli con motori a combustione, ibridi plug-in e modelli elettrici con piccoli motori ausiliari a benzina. Tuttavia, le loro emissioni dovranno essere compensate dall'uso di acciaio verde e combustibili rinnovabili. 

Il governo italiano ha accolto con favore i piani della Commissione europea come un passo nella giusta direzione verso una maggiore flessibilità per i produttori e l'allineamento degli obiettivi climatici con le realtà del mercato, le imprese e l'occupazione. Tuttavia, la transizione verso la mobilità elettrica ha subito un rallentamento.]

Poni una domanda alla volta e non numerare le domande. Inizia l'intervista con: “Ciao! Sono lieto di parlarti oggi. In generale, cosa ne pensi dei cambiamenti alla normativa sul divieto dei motori a combustione? Se qualcosa non ti è chiaro, non esitare a chiedere”.

Poni domande che facciano riferimento a tre aspetti generali delle risposte alla deregolamentazione ambientale: (1) Comportamentale: in che modo viene influenzato il comportamento dell'intervistato rispetto all'acquisto di automobili? E il comportamento pro-ambientale in generale? (2) Emozioni e sentimenti: in che modo i sentimenti e le emozioni dell'intervistato vengono influenzati dalla deregolamentazione ambientale? (3) Cognitivo: in che modo la deregolamentazione influenza le convinzioni e i giudizi? Inoltre, esplora in che modo la deregolamentazione modifica la percezione delle norme sociali e la fiducia nelle istituzioni, se opportuno.
Dopo aver posto tutte le domande, chiedi all'intervistato se desidera aggiungere ulteriori aspetti. In caso contrario, concludi l'intervista.

Sintesi e valutazione
Per concludere, scrivi una sintesi concisa delle risposte fornite dall'intervistato durante il colloquio.
Dopo il riassunto, aggiungi il testo: "Per concludere, in che misura il riassunto della nostra discussione descrive le tue opinioni: 1 (descrive male le mie opinioni),
2 (descrive parzialmente le mie opinioni), 3 (descrive bene le mie opinioni), 4 (descrive molto bene le mie opinioni). Rispondi solo con il numero corrispondente".
Dopo aver ricevuto la valutazione finale, termina l'intervista.
"""

# Codes
CODES = """Codici:

Infine, esistono codici specifici che devono essere utilizzati esclusivamente in situazioni ben precise. Questi codici attivano messaggi predefiniti nel front-end, quindi è fondamentale rispondere solo con il codice esatto, senza aggiungere altro testo come un messaggio di saluto o qualsiasi altro commento.

Contenuti problematici: se l'intervistato scrive contenuti problematici dal punto di vista legale o etico, si prega di rispondere solo con il codice “5j3k” e nessun altro testo.

Fine dell'intervista: quando sono state poste tutte le domande o quando l'intervistato non desidera continuare l'intervista, si prega di rispondere solo con il codice “x7y8” e nessun altro testo.

"""

# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Grazie per aver partecipato, l'intervista finisce qui."
CLOSING_MESSAGES["x7y8"] = "Grazie per aver partecipato all'intervista, questa era l'ultima domanda. Si prega di continuare con le sezioni rimanenti nella parte dedicata al sondaggio. Grazie mille per le risposte e per il tempo dedicato a questo progetto di ricerca!"


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
