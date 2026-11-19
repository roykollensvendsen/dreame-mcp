"""A mutation row whose replacement is not valid code proves nothing.

Such a mutant stops the module importing, so every test in its files errors and the run
would count the rule as caught. The runner compiles each mutant first and reports a row that
does not compile as BROKEN, without running the suite, and fails the run. The idea comes from
easee-mcp #33 (bf1dd19, GPL-3.0-or-later, Roy's own fork), adapted to this runner.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mutate_broken_rows", ROOT / "scripts" / "mutate.py")
assert _spec is not None and _spec.loader is not None
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)


def _project(tmp_path, monkeypatch, replace: str) -> tuple[Path, list[str]]:
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    source = root / "pkg" / "core.py"
    source.write_text("def answer():\n    return 42\n")
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "TABLE", root / "mutations.toml")
    monkeypatch.setattr(mutate, "RECOVERY", root / "recovery.json")
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")
    monkeypatch.setattr(
        mutate, "rules", lambda: [{"name": "a rule", "file": "pkg/core.py", "find": "return 42", "replace": replace}]
    )
    suites: list[str] = []

    def failing_tests() -> list[str]:
        suites.append(source.read_text())
        return ["test_a_rule"]

    monkeypatch.setattr(mutate, "failing_tests", failing_tests)
    return source, suites


def test_a_row_that_breaks_the_code_is_reported_as_broken(tmp_path, monkeypatch, capsys):
    source, suites = _project(tmp_path, monkeypatch, "return (")
    assert mutate._run_rules() == 1
    out = capsys.readouterr().out
    assert "BROKEN   a rule: the replacement is not valid code (line 2:" in out
    assert "KILLED" not in out
    # Nothing is said to be guarded or to have survived: a broken row proves nothing either way.
    assert "0/1 rules failed the test named after them" in out
    assert "SURVIVED" not in out and "None survived" not in out
    assert "broken rows, so nothing was tested; make each a whole valid line: a rule" in out
    assert suites == []  # the suite never ran on code that cannot import
    assert source.read_text() == "def answer():\n    return 42\n"


def test_a_row_that_compiles_still_runs_the_suite(tmp_path, monkeypatch, capsys):
    _, suites = _project(tmp_path, monkeypatch, "return 0")
    assert mutate._run_rules() == 0
    assert "KILLED   a rule" in capsys.readouterr().out
    assert suites == ["def answer():\n    return 0\n"]
