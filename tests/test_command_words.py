"""Every command the voice can refuse has words to refuse it with.

A refusal says "This vacuum can't <words>." A command without words would fail
with a KeyError instead of an answer, so the words and the commands must match.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from vacuum_mcp import voice
from vacuum_mcp.capabilities import COMMAND_WORDS, COMMANDS

#: The calls that name a command, and the one function allowed to pass its own
#: `command` parameter on instead of a name written out.
CHECKS = {"_able", "_simple"}
FORWARDER = "_simple"


def _commands_checked(source: str) -> set[str]:
    """The command names passed to the checks; a name not written out fails, except the forwarder's own."""
    names: set[str] = set()

    def visit(node: ast.AST, inside: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            inside = (*inside, node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CHECKS:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
            elif not _forwards_own_command(first, inside):
                raise AssertionError(f"line {node.lineno}: {node.func.attr} must name its command as a string")
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(ast.parse(source), ())
    return names


def _forwards_own_command(first: ast.expr | None, inside: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]) -> bool:
    forwarder = next((f for f in inside if f.name == FORWARDER), None)
    return (
        forwarder is not None
        and isinstance(first, ast.Name)
        and first.id == "command"
        and "command" in {a.arg for a in forwarder.args.args}
    )


def test_every_command_has_words():
    assert set(COMMAND_WORDS) == COMMANDS


def test_every_command_the_voice_checks_is_a_known_command():
    checked = _commands_checked(Path(voice.__file__).read_text(encoding="utf-8"))
    assert checked == COMMANDS, "each command is checked by name, and only known ones"


def test_a_command_passed_on_by_another_function_fails():
    snippet = """
class V:
    async def go(self, what):
        await self._able(what, 1)
"""
    with pytest.raises(AssertionError, match="must name its command"):
        _commands_checked(snippet)


def test_only_the_forwarders_own_command_may_be_passed_on():
    forwarded = """
class V:
    async def _simple(self, command):
        async def act():
            await self._able(command, 1)
"""
    assert _commands_checked(forwarded) == set()
    other_name = forwarded.replace("_able(command", "_able(other")
    with pytest.raises(AssertionError, match="must name its command"):
        _commands_checked(other_name)


def test_the_words_follow_cant():
    for words in COMMAND_WORDS.values():
        assert words == words.strip() and words[:1].islower() and not words.endswith("."), words
