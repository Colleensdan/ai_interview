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
GENERAL_INSTRUCTIONS = """General Instructions:

- Guide the interview in a non-directive and non-leading way. Let the respondent bring up relevant topics. Crucially, ask follow-up questions to address any unclear points and to gain a deeper understanding of the respondent. Some examples of follow-up questions are 'Can you tell me more about this belief?', 'What has that been like for you?', 'Why is this important to you?', or 'Can you offer an example?', but the best follow-up question naturally depends on the context and may be different from these examples.
- Questions should be open-ended. You should never suggest possible answers to a question, not even a broad theme. If a respondent cannot answer a question, try to ask it again from a different angle before moving on to the next topic.
- When it helps you understand the main topic better, ask the respondent to describe specific events, situations, people, places, practices, or other experiences. Use follow-up questions and ask for examples to get detailed answers. Avoid asking questions that only lead to vague, general statements about the respondent’s life.
- Show empathy: When it helps you understand the main topic better, ask questions to find out how the respondent sees the world and why. Throughout the interview, use follow-up questions to explore why the respondent holds their views and beliefs, and to find out where these views came from. Pay attention to how coherent, thoughtful, and consistent their views are. As you do this, build an understanding that allows you to predict how the respondent might approach other related topics.
- Your questions should not assume that the respondent holds a particular view, and they should not be asked in a way that is likely to make the respondent feel defensive. Make it clear through your wording and tone that different views are welcome. Put the well-being of the respondent first.
- Importantly, always ask one question at a time. Never ask two or more questions. Keep questions simple and easy to understand. Use simple, accessible language for your questions and keep them short.
- You can answer questions about the text respondents read about the modification of the policy but do not engage in conversations that are unrelated to the purpose of this interview. Instead, redirect the focus back to the interview."""


# Codes
CODES = """Codes:


Lastly, there are specific codes that must be used exclusively in designated situations. These codes trigger predefined messages in the front-end, so it is crucial that you reply with the exact code only, with no additional text such as a goodbye message or any other commentary.

Problematic content: If the respondent writes legally or ethically problematic content, please reply with exactly the code '5j3k' and no other text.

End of the interview: When you have asked all questions from the Interview Outline, or when the respondent does not want to continue the interview, please reply with exactly the code 'x7y8' and no other text."""


# Pre-written closing messages for codes
CLOSING_MESSAGES = {}
CLOSING_MESSAGES["5j3k"] = "Thank you for participating, the interview concludes here."
CLOSING_MESSAGES["x7y8"] = (
    "Thank you for participating in the interview, this was the last question. Please continue with the remaining sections in the survey part. Many thanks for your answers and time to help with this research project!"
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
