import streamlit as st
import hmac
import time
import os
from typing import Callable, List, Optional

from pseudonymizer import (
    EntityMapping,
    InterviewPseudonymizer,
    pseudonymize_messages,
)
PSEUDONYMIZER = InterviewPseudonymizer()


PROMPT_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "disregard previous instructions",
    "forget all prior instructions",
    "pretend to be",
    "you are now",
    "begin system prompt",
    "system prompt:",
    "override the system",
    "replace the system instructions",
    "chain of thought",
    "full chain of thought",
    "show your chain of thought",
    "show your reasoning",
    "full reasoning process",
    "thinking process",
    "unaligned",
]


# Password screen for dashboard (note: only very basic authentication!)
# Based on https://docs.streamlit.io/knowledge-base/deploy/authentication-without-sso
def check_password():
    """Returns 'True' if the user has entered a correct password."""

    def login_form():
        """Form with widgets to collect user information"""
        with st.form("Credentials"):
            st.text_input("Username", key="username")
            st.text_input("Password", type="password", key="password")
            st.form_submit_button("Log in", on_click=password_entered)

    def password_entered():
        """Checks whether username and password entered by the user are correct."""
        if st.session_state.username in st.secrets.passwords and hmac.compare_digest(
            st.session_state.password,
            st.secrets.passwords[st.session_state.username],
        ):
            st.session_state.password_correct = True

        else:
            st.session_state.password_correct = False

        del st.session_state.password  # don't store password in session state

    # Return True, username if password was already entered correctly before
    if st.session_state.get("password_correct", False):
        return True, st.session_state.username

    # Otherwise show login screen
    login_form()
    if "password_correct" in st.session_state:
        st.error("User or password incorrect")
    return False, st.session_state.username


def check_if_interview_completed(directory, username):
    """Check if interview transcript/time file exists which signals that interview was completed."""

    # Test account has multiple interview attempts
    if username != "testaccount":

        # Check if file exists
        try:
            with open(os.path.join(directory, f"{username}.txt"), "r") as _:
                return True

        except FileNotFoundError:
            return False

    else:

        return False


def save_interview_data(
    username,
    transcripts_directory,
    times_directory,
    file_name_addition_transcript="",
    file_name_addition_time="",
    mapping_handler: Optional[Callable[[List[EntityMapping]], None]] = None,
):
    """Write interview data to disk after applying privacy-preserving sanitisation.

    The optional `mapping_handler` allows controlled storage of the pseudonymisation
    table outside of the persisted transcript. When provided it receives a list of
    `EntityMapping` objects in the order they were first observed.
    """

    pseudonymized_messages, mappings = pseudonymize_messages(
        PSEUDONYMIZER, st.session_state.messages
    )

    # Store chat transcript
    with open(
        os.path.join(
            transcripts_directory, f"{username}{file_name_addition_transcript}.txt"
        ),
        "w",
    ) as t:
        for message in pseudonymized_messages:
            t.write(f"{message['role']}: {message['content']}\n")

    # Store file with start time and duration of interview
    with open(
        os.path.join(times_directory, f"{username}{file_name_addition_time}.txt"),
        "w",
    ) as d:
        duration = (time.time() - st.session_state.start_time) / 60
        d.write(
            f"Start time (UTC): {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(st.session_state.start_time))}\nInterview duration (minutes): {duration:.2f}"
        )

    if mapping_handler and mappings:
        mapping_handler(mappings)

    return mappings


def detect_prompt_injection_attempt(message):
    """Return the matched pattern if message looks like a prompt-injection attempt."""

    lowered = message.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern in lowered:
            return pattern
    return None
