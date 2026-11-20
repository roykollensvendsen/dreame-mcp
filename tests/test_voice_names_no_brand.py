"""The voice server takes everything about the brand from its backend: the cloud's and the
app's names in what it says, the login and its wait, and the client. So the server's own
source names no brand, and moving it into the neutral layer changes nothing it says."""

import subprocess
import sys
from pathlib import Path

from vacuum_mcp import voice
from vacuum_mcp.backends import default, dreame

SERVER_NAME = "vacuum-voice"  # the plugin's identity, named after its command


def test_the_voice_server_names_no_brand():
    text = Path(voice.__file__).read_text(encoding="utf-8").replace(SERVER_NAME, "")
    assert "dreame" not in text.casefold()


def test_the_default_backend_is_dreame():
    assert default() is dreame


def test_a_name_at_the_start_of_a_sentence_keeps_its_own_capitals():
    assert voice.sentence(dreame.APP) == "The DreameHome app"
    assert voice.sentence(dreame.CLOUD) == "The Dreame cloud"


def test_the_backends_load_without_the_cloud_client():
    code = (
        "import sys, vacuum_mcp.backend, vacuum_mcp.backends.dreame; "
        "sys.exit(1 if 'dreame_mcp.client' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], timeout=120).returncode == 0
