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

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "scripts" / "mutations.toml"
#: The file being mutated, kept here so a run that is killed can be undone.
RECOVERY = ROOT / "scripts" / ".mutate-recovery.json"
#: Where each suite run keeps its temporary files: one folder per checkout, outside it (inside,
#: a test that makes its own git repository would find this one around it). A run's folder
#: goes when the run ends; one left by a killed run goes at the next start. On 2026-11-14 the
#: runs' leftovers had filled /tmp to its last inode.
MUTATE_TMP = Path(tempfile.gettempdir()) / f"dreame-mcp-mutate-{hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]}"


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


def _owner_may_enter(path: Path) -> None:
    """Give the owner read, write and enter on a folder or file, keeping its other bits.
    A link is left alone: what it points to may be outside, and is not ours to change."""
    if not path.is_symlink():
        path.chmod(stat.S_IMODE(path.lstat().st_mode) | stat.S_IRWXU)


def _again(func, path, _error) -> None:
    """rmtree's second try at something it could not remove: open its folder, then retry."""
    _owner_may_enter(Path(path).parent)
    func(path)


def remove(folder: Path) -> None:
    """Remove a folder of test files, also the ones a test left with no permissions,
    without following a link out of it."""
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        folder.unlink()  # a link goes, never what it points to; a stray file goes too
        return
    if not folder.is_dir():
        return
    _owner_may_enter(folder)
    for top, dirs, _files in os.walk(folder):  # top down: a folder is opened before it is entered
        for name in dirs:
            _owner_may_enter(Path(top) / name)
    shutil.rmtree(folder, onexc=_again)


def _same(fd: int, path: Path) -> bool:
    """Whether the folder open as ``fd`` is still the one at ``path``."""
    try:
        now = os.lstat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(fd)
    return (now.st_dev, now.st_ino) == (held.st_dev, held.st_ino)


@contextlib.contextmanager
def guarded() -> Iterator[None]:
    """Hold MUTATE_TMP's own lock. Run folders are made, swept and the folder itself removed
    only while it is held, so a sweep never sees a run's folder before the run has locked it.
    A run that waited while another removed the folder makes it again and waits once more."""
    while True:
        MUTATE_TMP.mkdir(mode=0o700, exist_ok=True)
        try:
            fd = os.open(MUTATE_TMP, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:  # a link, or something else, planted at the name
            raise SystemExit(_not_ours(f"is not a folder of yours ({error.strerror})")) from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            # RULE: a run trusts its folder only when it is really ours
            _check_ours(fd)
            if _same(fd, MUTATE_TMP):
                yield
                return
        finally:
            os.close(fd)


def _not_ours(why: str) -> str:
    return (
        f"{MUTATE_TMP} {why}. Its name is easy to guess in a shared /tmp, so this run will not"
        " use it: look at what is there, remove it if it is not yours, and run again."
    )


def _check_ours(fd: int) -> None:
    """MUTATE_TMP has a name anyone can guess, in a /tmp everyone shares. Only a folder made
    by this user, which no one else may enter, is used, so nothing planted there first can
    steer what the sweep removes."""
    held = os.fstat(fd)
    if not stat.S_ISDIR(held.st_mode) or held.st_uid != os.getuid():
        raise SystemExit(_not_ours("is not a folder of yours"))
    if held.st_mode & 0o077:
        raise SystemExit(_not_ours(f"is one others may enter (mode {stat.S_IMODE(held.st_mode):o})"))


def held(folder: Path) -> bool:
    """Whether a run still going holds this folder's lock. Only opens, never creates, and
    never follows a link. What cannot be opened as a folder (a file, a link, a folder no
    one may open) is no run's, so the sweep removes it."""
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def new_run() -> tuple[Path, int]:
    """A folder for one suite run, and the open folder whose lock the run holds until it
    has removed it."""
    with guarded():
        run = Path(tempfile.mkdtemp(prefix="run-", dir=MUTATE_TMP))
        fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY)
        fcntl.flock(fd, fcntl.LOCK_EX)
    return run, fd


def sweep() -> None:
    """Remove what killed runs left in MUTATE_TMP, and only that: another run of this
    checkout may be going at the same time."""
    with guarded():
        for left in MUTATE_TMP.iterdir():
            # RULE: a sweep leaves the folder of a run still going alone
            if held(left):
                continue
            remove(left)


def failing_tests() -> list[str]:
    """Run the tests; the names of those that failed."""
    # Without bytecode, a mutation never runs from the previous one's cache
    # (the cache is checked by size and a one-second timestamp).
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    run, lock = new_run()
    try:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                "--color=no",
                "-p",
                "no:cacheprovider",
                f"--basetemp={run}",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        # RULE: a suite run leaves no temporary files behind
        remove(run)
        os.close(lock)
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


#: What stops a run: Ctrl-C, kill or a cancelled CI job, and a closed terminal.
STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _stop(number: int, _frame: object) -> None:
    for each in STOPS:
        # RULE: a second stop does not cut the cleanup short
        signal.signal(each, signal.SIG_IGN)
    if number == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + number)


def stop_cleanly() -> None:
    """Let any stop end a run the way Ctrl-C does, so the finally blocks put the mutated line
    back and remove the run's temporary files; once stopping, further stops are ignored."""
    for each in STOPS:
        signal.signal(each, _stop)


def main() -> int:
    # RULE: a stopped run still cleans up
    stop_cleanly()
    try:
        return _run_rules()
    finally:
        with guarded():
            # RULE: a finished run leaves not even its empty folder
            quiet_rmdir(MUTATE_TMP)


def quiet_rmdir(folder: Path) -> None:
    """Remove the folder if it is empty. One a killed run left files in is kept, for the
    next run to sweep. (The ratchet measures its base in a copy at a new path each time,
    so a folder kept per checkout would add one to /tmp with every ratchet run.)"""
    with contextlib.suppress(OSError):
        folder.rmdir()


def _run_rules() -> int:
    repair()
    # RULE: temporary files a killed run left are swept at the next start
    sweep()
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
        # No time limit, on purpose (Roy's rule for mutation and ratchet runs): a suite cut
        # short proves nothing. A mutation that makes the tests hang hangs the run, in plain
        # sight, until someone stops it; the recovery file then puts the line back.
        try:
            failed = failing_tests()
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
