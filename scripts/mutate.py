#!/usr/bin/env python3
"""Switch each rule off in turn and check that its own test fails.

A test that has never failed proves nothing. Each row of scripts/mutations.toml
names a rule and the one line that carries it. For each row this replaces that
line, runs the tests, and puts the line back. The rule counts as guarded only
when the test named after it, test_ + the rule's words, is among the failures:
another test failing by chance is not evidence about this rule.

Run it from anywhere:

    uv run python scripts/mutate.py

It exits 1 when a rule survives, is caught only by other tests, or when a file
changed while it was mutated (that file is then left as it is).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "scripts" / "mutations.toml"
#: The file being mutated, kept here so a run that is killed can be undone.
RECOVERY = ROOT / "scripts" / ".mutate-recovery.json"
#: A mutation that makes the tests hang is a kill, but not one that can be attributed.
SUITE_TIMEOUT = 600


def test_name(rule: str) -> str:
    """The test a rule needs: its words, as an identifier."""
    return "test_" + "_".join(re.findall(r"[a-z0-9]+", rule.lower()))


def rules() -> list[dict]:
    return tomllib.loads(TABLE.read_text()).get("rule", [])


def target_line(lines: list[str], rule: dict) -> int:
    """The index of the rule's line: the nth line matching ``find`` that is not a comment."""
    want = rule.get("occurrence", 1)
    seen = 0
    for i, line in enumerate(lines):
        if re.search(rule["find"], line) and not line.lstrip().startswith("#"):
            seen += 1
            if seen == want:
                return i
    raise SystemExit(f"{rule['name']}: no line {want} matching {rule['find']!r} in {rule['file']}")


def mutated(text: str, rule: dict) -> str:
    lines = text.splitlines()
    i = target_line(lines, rule)
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    lines[i] = indent + rule["replace"].strip()
    return "\n".join(lines) + "\n"


def failing_tests() -> list[str]:
    """Run the tests; the names of those that failed."""
    # Without bytecode, a mutation never runs from the previous one's cache
    # (the cache is checked by size and a one-second timestamp).
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "--color=no", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=SUITE_TIMEOUT,
        check=False,
    )
    return [m.split("[")[0] for m in re.findall(r"^(?:FAILED|ERROR) \S+::(\S+)", done.stdout, re.MULTILINE)]


def repair() -> None:
    """Undo a mutation left behind by a run that was killed."""
    if not RECOVERY.is_file():
        return
    kept = json.loads(RECOVERY.read_text())
    path = ROOT / kept["file"]
    if path.read_text() == kept["mutated"]:
        path.write_text(kept["original"])
        print(f"put back {kept['file']}, left mutated by a run that was stopped")
    RECOVERY.unlink()


def main() -> int:
    repair()
    table = rules()
    if not table:
        print(f"no rules in {TABLE.relative_to(ROOT)}")
        return 0
    survived, by_others, changed = [], [], []
    for rule in table:
        path = ROOT / rule["file"]
        original = path.read_text()
        bad = mutated(original, rule)
        RECOVERY.write_text(json.dumps({"file": rule["file"], "original": original, "mutated": bad}))
        path.write_text(bad)
        try:
            failed = failing_tests()
        except subprocess.TimeoutExpired:
            failed = ["(the tests hung)"]
        finally:
            if path.read_text() == bad:
                path.write_text(original)
            else:
                # Someone edited it meanwhile: putting it back would lose their edit.
                changed.append(rule["file"])
            RECOVERY.unlink(missing_ok=True)
        own = test_name(rule["name"])
        if own in failed:
            verdict = "KILLED  "
        elif failed:
            verdict = "BY OTHER"
            by_others.append(rule["name"])
        else:
            verdict = "SURVIVED"
            survived.append(rule["name"])
        print(f"{verdict} {rule['name']} ({len(failed)} failed)")
    print(f"\n{len(table) - len(survived) - len(by_others)}/{len(table)} rules failed the test named after them")
    for label, names in (
        ("SURVIVED, so no test guards them", survived),
        ("caught only by other tests", by_others),
        ("changed while mutated, left as they are", changed),
    ):
        if names:
            print(f"{label}: {', '.join(names)}")
    return 1 if survived or by_others or changed else 0


if __name__ == "__main__":
    sys.exit(main())
