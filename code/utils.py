import logging
import sys
from pathlib import Path
import streamlit as st
import hmac
import time
import os
from typing import Callable, List, Optional

from pseudonymizer import (
    EntityMapping,
    InterviewPseudonymizer,
    PhrasePseudonymizer,
    pseudonymize_messages,
)
import sharepoint as _sp


def _build_pseudonymizer():
    """Return the active pseudonymizer.

    Default: phrase-blocklist (``PhrasePseudonymizer``).
    Pass ``--spacy-pseudonymization`` on the command line to use the
    spaCy NER-based ``InterviewPseudonymizer`` instead.
    """
    if "--spacy-pseudonymization" in sys.argv:
        return InterviewPseudonymizer()
    return PhrasePseudonymizer()


PSEUDONYMIZER = _build_pseudonymizer()

logger = logging.getLogger("ai_interview.utils")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


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
    variant: str = "",
):
    """Write interview data to disk after applying privacy-preserving sanitisation.

    The optional `mapping_handler` allows controlled storage of the pseudonymisation
    table outside of the persisted transcript. When provided it receives a list of
    `EntityMapping` objects in the order they were first observed.

    If `variant` is provided (e.g. "combustion", "deforestation") files are
    uploaded to SharePoint under incoming/{variant}/{subfolder}/ so data from
    different interview variants is kept separate.
    """

    pseudonymized_messages, mappings = pseudonymize_messages(
        PSEUDONYMIZER, st.session_state.messages
    )

    # Build file content in memory so it can be saved locally and uploaded to SharePoint
    transcript_filename = f"{username}{file_name_addition_transcript}.txt"
    transcript_content = "".join(
        f"{message['role']}: {message['content']}\n"
        for message in pseudonymized_messages
    )

    duration = (time.time() - st.session_state.start_time) / 60
    times_filename = f"{username}{file_name_addition_time}.txt"
    times_content = (
        f"Start time (UTC): {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(st.session_state.start_time))}\n"
        f"Interview duration (minutes): {duration:.2f}"
    )

    # Store chat transcript locally
    with open(os.path.join(transcripts_directory, transcript_filename), "w") as t:
        t.write(transcript_content)

    # Store file with start time and duration of interview locally
    with open(os.path.join(times_directory, times_filename), "w") as d:
        d.write(times_content)

    # Upload to SharePoint (non-fatal: log + flag but do not interrupt the interview).
    # Path: incoming/{variant}/{data-type}/ mirroring the local data/ layout.
    if _sp._sp_configured():
        _type_t = Path(transcripts_directory).name   # e.g. "transcripts" or "backups"
        _type_d = Path(times_directory).name         # e.g. "times" or "backups"
        transcript_subfolder = f"{variant}/{_type_t}" if variant else _type_t
        times_subfolder = f"{variant}/{_type_d}" if variant else _type_d
        try:
            _sp.upload_text(transcript_filename, transcript_content, subfolder=transcript_subfolder)
            _sp.upload_text(times_filename, times_content, subfolder=times_subfolder)
        except Exception as sp_err:
            logger.error(
                "SharePoint upload FAILED for user '%s' (data saved locally): %s",
                username, sp_err,
            )
            # Persist the failure flag so the UI banner stays visible across reruns.
            st.session_state["sp_upload_failed"] = True
            st.session_state["sp_error"] = str(sp_err)
            st.warning(
                f"SharePoint upload failed — interview data is saved locally on "
                f"the server only. Please notify the administrator. Error: {sp_err}"
            )

    if mapping_handler:
        mapping_handler(mappings)

    return mappings


