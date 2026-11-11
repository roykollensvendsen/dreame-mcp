"""Every failure says what is wrong and what to do next (the owner's rule, 2026-11-11).

A failure read aloud that only says what went wrong leaves the owner, often
driving, with nothing to do. So the last sentence of every answer that is not
a success starts with something to do, and a bare question ("Which rooms?")
is not a next step. The failures are read from the voice server's source, so
a new one cannot slip past: every ``self._answer(False, ...)`` and every
``raise Unavailable(...)``, following names to what they were set to and
helpers to what they return. Copied from tv-mcp's tests/test_next_step.py and
fitted to this server's shapes.
"""

from __future__ import annotations

import ast
import pathlib
import re

from dreame_mcp import voice
from dreame_mcp.simulator import SimulatedVacuum
from dreame_mcp.voice import Link, Vacuum

SOURCE = pathlib.Path(voice.__file__)
#: The start of a sentence that says what to do next.
NEXT = (
    "Ask",
    "Say",
    "Try",
    "Open",
    "Pick",
    "Check",
    "Choose",
    "Use",
    "Put",
    "Stop",
    "Wait",
    "Fix",
)
#: Parts passed on from elsewhere, with where their own words are checked.
PASSED_ON = {
    "e.then": "every raise Unavailable(...) is checked where it is raised",
}


def _last_sentence(text: str) -> str:
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", text.strip()) if s]
    return sentences[-1] if sentences else ""


def _text(node: ast.AST) -> str | None:
    """The words of a text, with each {placeholder} kept as X."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
    return None


def _module(tree: ast.Module):
    """The module's constant texts, and its functions by name."""
    constants = {
        t.id: text
        for n in tree.body
        if isinstance(n, ast.Assign) and (text := _text(n.value)) is not None
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return constants, functions


def _assigned(name: str, scope: ast.AST):
    """What `name` is set to anywhere in `scope`, with its place when it is unpacked from a tuple."""
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == name:
                    yield node.value, None
                if isinstance(target, ast.Tuple):
                    for i, element in enumerate(target.elts):
                        if getattr(element, "id", None) == name:
                            yield node.value, i
        if isinstance(node, ast.AnnAssign) and node.value is not None and getattr(node.target, "id", None) == name:
            yield node.value, None


def _returned(helper: ast.AST):
    for node in ast.walk(helper):
        if isinstance(node, ast.Return) and node.value is not None:
            yield node.value


def _all_texts(node: ast.AST, scope: ast.AST | None, module, index: int | None = None):
    """Every text `node` can end a spoken failure with, and its source."""
    constants, functions = module
    if index is not None and isinstance(node, ast.Tuple):
        yield from _all_texts(node.elts[index], scope, module)
    elif isinstance(node, ast.Tuple):
        # Unavailable(what is wrong, what to do next): the last part is said last.
        yield from _all_texts(node.elts[-1], scope, module)
    elif isinstance(node, ast.Starred):
        yield from _all_texts(node.value, scope, module)
    elif isinstance(node, ast.BinOp):
        yield from _all_texts(node.right, scope, module)
    elif isinstance(node, ast.IfExp):
        yield from _all_texts(node.body, scope, module)
        yield from _all_texts(node.orelse, scope, module)
    elif isinstance(node, ast.Constant) and node.value is None:
        return  # no failure on this path
    elif ast.unparse(node) in PASSED_ON:
        return
    elif isinstance(node, ast.Name) and scope is not None and (values := list(_assigned(node.id, scope))):
        for value, at in values:
            yield from _all_texts(value, scope, module, at)
    elif isinstance(node, ast.Name) and node.id in constants:
        yield constants[node.id], node.id
    elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in functions:
        helper = functions[node.func.id]
        for value in _returned(helper):
            yield from _all_texts(value, helper, module, index)
    else:
        yield _text(node), ast.unparse(node)


def _said(node: ast.AST):
    """What `node` reads aloud as a failure, if it is one."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_answer"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value is False
    ):
        yield node.args[1]
    if (
        isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and getattr(node.exc.func, "id", None) == "Unavailable"
        and node.exc.args
    ):
        args = node.exc.args
        yield args[0] if len(args) == 1 and isinstance(args[0], ast.Starred) else args[-1]


def _failures():
    """Every failure text in the voice server, with where it is."""
    tree = ast.parse(SOURCE.read_text())
    module = _module(tree)
    # The innermost function owns a node: functions come outer first, so inner ones overwrite.
    owner = {id(child): function for function in module[1].values() for child in ast.walk(function)}
    for node in ast.walk(tree):
        for arg in _said(node):
            for text, raw in _all_texts(arg, owner.get(id(node)), module):
                yield f"{SOURCE.name}:{arg.lineno}", text, raw


def test_every_failure_says_what_to_do_next():
    missing = []
    for where, text, raw in _failures():
        if text is None:
            missing.append(f"{where}: cannot read {raw}")
        elif not _last_sentence(text).startswith(NEXT):
            missing.append(f"{where}: {text!r}")
    assert not missing, "\n".join(sorted(set(missing)))


def test_the_failures_are_found_at_all():
    assert sum(1 for _ in _failures()) >= 18


# -- the same, as the owner hears it -----------------------------------------


def _vacuum(client) -> Vacuum:
    return Vacuum(Link(client), settle=0)


async def test_an_unreachable_cloud_says_to_try_again():
    a = await _vacuum(SimulatedVacuum(offline=True)).status()
    assert not a["success"]
    assert _last_sentence(a["say"]).startswith("Try again")


async def test_a_command_not_sent_says_so_and_then_what_to_do():
    a = await _vacuum(SimulatedVacuum(offline=True)).start()
    assert not a["success"]
    assert "The vacuum wasn't told anything." in a["say"]
    assert _last_sentence(a["say"]).startswith(NEXT)


async def test_a_missing_login_says_where_to_put_it():
    a = await _vacuum(None).status()
    assert not a["success"]
    assert _last_sentence(a["say"]).startswith(NEXT)
    assert "restart the vacuum plugin" in a["say"]


async def test_an_unknown_room_says_which_rooms_there_are():
    a = await _vacuum(SimulatedVacuum()).clean_rooms(["Bathroom"])
    assert not a["success"]
    assert _last_sentence(a["say"]).startswith(NEXT)
    assert "Kitchen" in _last_sentence(a["say"])
