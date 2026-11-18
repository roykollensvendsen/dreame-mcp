"""Throwaway probes are not kept in the tree.

The old scratch/ folder held one-off login and discovery probes that
nothing imported or ran. Checked-in probes go stale, carry copied
constants and dodge the lint rules, so the folder must not come back.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_there_is_no_scratch_folder_of_throwaway_probes():
    assert not (ROOT / "scratch").exists()


def test_no_lint_exception_is_kept_for_a_scratch_folder():
    assert "scratch/" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
