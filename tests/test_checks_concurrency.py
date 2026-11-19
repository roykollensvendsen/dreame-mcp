"""Only a pull request's stale run is ever cancelled.

With one concurrency group per ref, GitHub keeps only one waiting run in it, so a push to
`voice` between two others got no run at all, and cancel-in-progress cut short runs on the
base branch too. A pull request's runs share one group per ref and a newer push stops the
stale one; every other run is its own group, keyed by run_id, and runs to the end. (From
voice-architect's no-duplicates-v1-notes n1, 6fff3f05325a.)
"""

from __future__ import annotations

from pathlib import Path

CHECKS = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "checks.yml"

GROUP = "  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
CANCEL = "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}"


def _concurrency() -> list[str]:
    lines = CHECKS.read_text().splitlines()
    start = lines.index("concurrency:")
    block = []
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        block.append(line)
    return block


def test_only_a_pull_requests_stale_run_is_cancelled():
    assert _concurrency() == [GROUP, CANCEL]
