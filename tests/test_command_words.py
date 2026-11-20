"""Every command the voice can refuse has words to refuse it with.

A refusal says "This vacuum can't <words>." A command without words would fail
with a KeyError instead of an answer, so the words and the commands must match.
"""

from __future__ import annotations

import ast
from pathlib import Path

from vacuum_mcp import voice
from vacuum_mcp.capabilities import COMMAND_WORDS, COMMANDS


def _commands_voice_checks() -> set[str]:
    tree = ast.parse(Path(voice.__file__).read_text(encoding="utf-8"))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"_able", "_simple"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_every_command_has_words():
    assert set(COMMAND_WORDS) == COMMANDS


def test_every_command_the_voice_checks_is_a_known_command():
    checked = _commands_voice_checks()
    assert checked == COMMANDS, "each command is checked by name, and only known ones"


def test_the_words_follow_cant():
    for words in COMMAND_WORDS.values():
        assert words == words.strip() and words[:1].islower() and not words.endswith("."), words
