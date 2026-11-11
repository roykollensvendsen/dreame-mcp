"""The quality levels and the ratchet (the plugin standard, rule 16).

scripts/ratchet.py measures the levels the phone page shows (coverage, the
mutation tally, tests, lint and type errors, import cycles, imports that break
the layers, copied code, the most tangled function per module, the suite's time)
and refuses a change that makes one worse than the base, unless the pull request names it in a
`Ratchet-Exception:` line.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts" / "ratchet.py")
assert _spec and _spec.loader
ratchet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ratchet)

BASE = {
    "coverage": 50.0,
    "mutation": {"killed": 14, "total": 14},
    "tests": 100,
    "lint_errors": 0,
    "type_errors": 0,
    "cycles": 0,
    "layer_violations": 0,
    "duplication": 1.0,
    "complexity": {"pkg/a.py": 5},
    "test_seconds": 10.0,
}
BUDGET = {"seconds": 60, "tolerance": 0.25}


def _with(**changes):
    return {**BASE, **changes}


def test_the_same_levels_pass():
    assert ratchet.compare(BASE, BASE, BUDGET, set()) == []


def test_a_level_that_gets_worse_is_refused():
    found = ratchet.compare(BASE, _with(tests=99, lint_errors=1, duplication=1.5), BUDGET, set())
    assert [f.split()[0] for f in found] == ["tests", "lint_errors", "duplication"]


def test_coverage_may_wobble_a_little():
    assert ratchet.compare(BASE, _with(coverage=49.7), BUDGET, set()) == []
    assert ratchet.compare(BASE, _with(coverage=49.0), BUDGET, set())


def test_a_mutation_that_survives_is_worse():
    assert ratchet.compare(BASE, _with(mutation={"killed": 13, "total": 14}), BUDGET, set())
    assert ratchet.compare(BASE, _with(mutation={"killed": 15, "total": 15}), BUDGET, set()) == []


def test_a_ratchet_exception_allows_a_named_step_back():
    text = "Some words.\nRatchet-Exception: tests - two tests were merged into one\n"
    allowed = ratchet.exceptions(text)
    assert allowed == {"tests"}
    assert ratchet.compare(BASE, _with(tests=99), BUDGET, allowed) == []


def test_a_more_tangled_function_is_refused():
    found = ratchet.compare(BASE, _with(complexity={"pkg/a.py": 7}), BUDGET, set())
    assert found and "pkg/a.py" in found[0]


def test_a_new_module_keeps_under_the_ceiling():
    ok = _with(complexity={"pkg/a.py": 5, "pkg/b.py": ratchet.NEW_MODULE_CEILING})
    assert ratchet.compare(BASE, ok, BUDGET, set()) == []
    tangled = _with(complexity={"pkg/a.py": 5, "pkg/b.py": ratchet.NEW_MODULE_CEILING + 1})
    assert ratchet.compare(BASE, tangled, BUDGET, set())


def test_the_suite_keeps_to_its_time_budget():
    assert ratchet.compare(BASE, _with(test_seconds=75.0), BUDGET, set()) == []
    assert ratchet.compare(BASE, _with(test_seconds=76.0), BUDGET, set())


def test_the_mutation_tally_is_read_from_mutate():
    out = "KILLED  a rule (2 failed)\n\n13/14 rules failed the test named after them\n"
    assert ratchet.mutation_tally(out, 1) == {"killed": 13, "total": 14}
    assert ratchet.mutation_tally("14/14 rules failed the test named after them", 0) == {"killed": 14, "total": 14}
    # A run that failed is never a full score, and one that printed nothing counts nothing.
    assert ratchet.mutation_tally("14/14 rules failed the test named after them", 1) == {"killed": 13, "total": 14}
    assert ratchet.mutation_tally("", 1) == {"killed": 0, "total": 1}


def test_copied_blocks_are_counted(tmp_path):
    block = "\n".join(f"x{i} = {i}" for i in range(ratchet.BLOCK))
    (tmp_path / "a.py").write_text(block + "\ny = 1\n")
    (tmp_path / "b.py").write_text(block + "\n")
    lines = ratchet.BLOCK * 2 + 1
    assert ratchet.duplication(tmp_path) == round(100 * ratchet.BLOCK * 2 / lines, 2)
    (tmp_path / "b.py").write_text("z = 2\n")
    assert ratchet.duplication(tmp_path) == 0.0


def test_import_cycles_are_counted(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text("from . import a\n")
    (pkg / "c.py").write_text("import json\n")
    assert ratchet.cycles(pkg) == 1
    (pkg / "b.py").write_text("")
    assert ratchet.cycles(pkg) == 0


def test_an_import_that_reaches_up_or_sideways_breaks_the_layers(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "low.py").write_text("import json\n")
    (pkg / "mid.py").write_text("from . import low\n")
    (pkg / "top.py").write_text("from pkg import mid, low\n")
    layers = {"__init__": 0, "low": 0, "mid": 1, "top": 2}
    assert ratchet.layer_breaches(pkg, layers) == []
    (pkg / "low.py").write_text("from . import mid\n")  # up
    (pkg / "other.py").write_text("")  # no layer
    (pkg / "mid2.py").write_text("from . import mid\n")  # sideways
    assert ratchet.layer_breaches(pkg, {**layers, "mid2": 1}) == [
        "low (layer 0) imports mid (layer 1)",
        "mid2 (layer 1) imports mid (layer 1)",
        "other has no layer",
    ]


def test_every_module_of_the_server_keeps_to_its_layer():
    assert ratchet.layer_breaches(ratchet.PACKAGE, ratchet.LAYERS) == []


def test_more_layer_breaches_are_refused():
    assert ratchet.compare(BASE, _with(layer_violations=1), BUDGET, set()) == ["layer_violations got worse: 0 -> 1"]


def test_complexity_is_the_most_tangled_function_per_module(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text(
        "def plain():\n    return 1\n\n"
        "def tangled(x):\n    if x:\n        return 1\n    for i in x:\n        if i and x:\n            pass\n"
    )
    assert ratchet.complexity(tmp_path) == {"pkg/a.py": 5}


def test_the_levels_are_written_as_the_phone_page_reads_them(tmp_path):
    out = tmp_path / "levels.json"
    ratchet.write_levels(out, BASE)
    kept = json.loads(out.read_text())
    assert set(kept) == {
        "coverage",
        "mutation",
        "tests",
        "lint_errors",
        "type_errors",
        "cycles",
        "layer_violations",
        "duplication",
        "complexity",
        "test_seconds",
    }


def test_the_budget_comes_from_quality_json():
    assert ratchet.budget(ROOT / "quality.json") == json.loads((ROOT / "quality.json").read_text())["test_budget"]
