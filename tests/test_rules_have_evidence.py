"""Rules that must fail when switched off (the plugin standard, rule 15).

Every `# RULE:` marker in the voice server has a row in scripts/mutations.toml,
every row has its marker just above the line it switches off, and every rule
has a test named after it. scripts/mutate.py then proves that test fails when
the rule is switched off; this keeps the three from drifting apart.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts" / "mutate.py")
assert _spec and _spec.loader
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)

MARKER = re.compile(r"^\s*# RULE: (.+?)\s*$")


def _markers() -> dict[str, tuple[str, int]]:
    found = {}
    for path in (ROOT / "src").rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines()):
            if m := MARKER.match(line):
                found[m.group(1)] = (str(path.relative_to(ROOT)), i)
    return found


def test_every_rule_marker_has_a_row_and_every_row_a_marker():
    assert set(_markers()) == {rule["name"] for rule in mutate.rules()}


def test_each_row_switches_off_the_line_under_its_marker():
    markers = _markers()
    for rule in mutate.rules():
        file, at = markers[rule["name"]]
        assert file == rule["file"], rule["name"]
        lines = (ROOT / file).read_text().splitlines()
        target = mutate.target_line(lines, rule)
        # Only other markers may stand between a marker and its line.
        between = lines[at + 1 : target]
        assert target > at and all(MARKER.match(line) for line in between), rule["name"]


def test_every_rule_has_a_test_named_after_it():
    tests = "\n".join(p.read_text() for p in (ROOT / "tests").glob("test_*.py"))
    for rule in mutate.rules():
        name = mutate.test_name(rule["name"])
        assert re.search(rf"^(async )?def {name}\(", tests, re.MULTILINE), f"{rule['name']} needs {name}"
