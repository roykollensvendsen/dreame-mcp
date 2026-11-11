---
name: code-quality
description: Make this fork's row on the code-health page green, and keep it there - coverage, rules proven by a test, the most tangled function, layers, copied code, the time the tests take and the checks on the voice branch. Use when the page shows a dreame-mcp measure in amber or red, before a pull request to voice that adds or splits code, when someone asks how good the code is or how to raise a number, or when a measure seems impossible to meet and an exemption is tempting.
---

# Keeping the code-health page green

The claude-voice bridge's software page (`http://127.0.0.1:8811/ui/`, read
from `GET /api/v1/health/software`) shows one row per repository and one cell
per measure. This fork's numbers are the levels the `quality` job in
`.github/workflows/checks.yml` keeps, as the artifact `quality-levels`, after
every push to **voice**, so a pull request turns a cell green only once it has
landed on voice. This skill is the routine: read the page, measure the same
thing locally, fix the worst cell test-first, land it.

## 1. What the page measures, and when it is green

| Cell | Green | Amber | Red |
| --- | --- | --- | --- |
| Test coverage | 80 % or more | 60 - 80 % | under 60 % |
| Rules proven by a test (mutations killed) | every one | 90 % or more | fewer |
| Most tangled function (McCabe) | 15 or less | up to 25 | above 25 |
| Modules in their layers | 0 imports out of order | | any |
| Copied code | under 3 % of lines | up to 6 % | more |
| Lint and type errors | 0 | | any |
| Import cycles | 0 | any | |
| Time the tests take | inside the budget | inside budget plus tolerance | over |
| Checks on voice | passing | running | failed |

The page and its limits live in the bridge, in claude-voice's
`src/claude_voice/software.py`, `MEASURES`; read them there if this table and
the page ever disagree, and fix the table. The page labels the last cell
"Checks on main", but for this fork it follows the `checks` workflow on voice.

What is measured (`scripts/ratchet.py`): coverage of the whole `dreame_mcp`
package; complexity and copied code over everything in `src/`; layers and
cycles among the package's modules; lint over `src`, `tests` and the two
scripts; types over `src`. So Sandra's modules (`server.py`, `client.py`,
`portmanteau.py` and the rest) count as much as `voice.py`.

A cell can also read **exempt** (section 4). Exempt is never green, and nothing
in this skill makes a cell exempt by itself.

## 2. Measure it locally

Work in a `git worktree` off `voice`, never in the checkout the bridge runs
(see the `voice-plugin` skill). Run these from the worktree's root. The long
ones go in the background; never put a time limit on them, because a cut-off
mutation run leaves a rule's line switched off and reports rules as unproven
that are not. Do not run two of them at once: `mutate.py` edits the source
while it runs.

```sh
uv run python scripts/ratchet.py measure --out /tmp/levels.json  # every level at once, as voice's checks measure it (about six minutes)
uv run python scripts/ratchet.py check --base fork/voice         # what your branch made worse than voice
uv run python scripts/mutate.py                                  # each rule switched off in turn (about three minutes)
```

`fork` is the remote for `roykollensvendsen/dreame-mcp` in Roy's clone;
`origin` there is Sandra's repository. Use whichever remote points at the fork.

For one cell at a time:

```sh
uv run pytest -q --cov=dreame_mcp --cov-report=term-missing  # coverage, with the lines no test runs
uv run pytest -q --durations=15                              # the slowest tests
uv run ruff check src tests scripts/mutate.py scripts/ratchet.py
uv run ruff format --check src tests scripts/mutate.py scripts/ratchet.py
uv run pyright src scripts/mutate.py scripts/ratchet.py
```

The complexity, layer, cycle and duplication counts are functions in
`scripts/ratchet.py` (`complexity`, `layer_breaches`, `cycles`, `duplication`);
the file `measure --out` writes lists the worst function's score per module, so
the module to open first is the one with the highest number. The time budget
is the one thing in `quality.json` (90 seconds, plus 25 %), and it changes only
by hand, with Roy's yes.

## 3. Fix the worst cell, test-first

Work on one cell per pull request, the reddest first. Everything here follows
CONTRIBUTING.md: a failing test before the code, one topic per pull request, a
conventional commit subject (the `commits` job refuses others), commits
through the `write-commit` skill. A change to code in Sandra's modules follows
"Where a change goes" in CONTRIBUTING.md; tests for them may go to voice.

The suite uses the simulator and fakes. Never command the real robot, and
never run the token-extraction scripts, to check a change.

**Checks on voice red.** Fix that before anything else: every other number is
measured by those checks, so while they fail the page stops moving.

**Coverage under 80.** Open the lines `term-missing` lists and ask what the
person using the code would notice if each were wrong. Write a test of that
behaviour, through the module's public functions, and see it red: for code that
already exists, break the line by hand, watch the test fail, then put the line
back. A test that passed the first time it ran proves nothing. Never test a
private helper just to lift the number, and never mark code as excluded from
coverage to get past the limit; that is an exemption, and section 4 applies.

**A rule nobody proves.** `mutate.py` names each rule whose removal no test
noticed. A rule is a line in the voice server with `# RULE: what it says` just
above it; it needs a row in `scripts/mutations.toml` and a test named in the
rule's own words (`# RULE: a question expires` wants `test_a_question_expires`).
`tests/test_rules_have_evidence.py` keeps markers, rows and test names in step.
Write that test, run the mutation, see that very test go red, and keep the
row. Never loosen a row or point it at something easier to make the count go
up; if a row has to change, the pull request says why.

**A function above 15.** Split it without changing what it does:

1. Run the module's tests and see them green. If nothing covers the function's
   branches yet, write those tests first, as a pull request of their own.
2. Pull one decision out at a time: a guard clause that returns early, a
   lookup table in place of a chain of `if`s, one helper per branch of a
   `match`, a loop body as a function. Each helper does one thing and is named
   for it.
3. Run the same tests and see them green again, unchanged. A split that needs
   a test edited has changed behaviour, and is not a split.
4. Commit it as `refactor:`, with no behaviour change in the same commit.

A nested function counts on its own, not towards the function around it, so
hiding branches in a closure is not a fix the page will reward; give the helper
a name at module level.

**An import out of its layer.** The layers are `LAYERS` in `scripts/ratchet.py`:
0 the foundations (`client`, `state`, `prompts`, `activity_log`, `map_export`),
1 `portmanteau` and `simulator`, 2 `agentic` and `voice`, 3 `server`,
4 `__main__`. As it loads, a module may import only modules in lower layers.
Move the shared code down into a lower module that both can use, or pass the
thing in as an argument. Do not hide the import inside a function to dodge the
count. A new module gets its layer in `LAYERS` in the same pull request.

**Copied code at 3 % or more.** A block counts when six or more lines in a row
appear more than once in `src/`, ignoring indentation. Put the block in one
function both places call, with the tests green before and after, as
`refactor:`. Tests are not counted here.

**Tests over their budget.** Find the slowest with `--durations=15`. The usual
cause is a real wait: a sleep, a timeout, a subprocess. Make the wait
injectable and pass a fake clock or a short timeout in the test. Raising the
budget is Roy's decision, not a fix.

**Lint, type errors or a cycle.** Fix them as they are reported; a cycle is
broken the same way as a layer breach, by moving shared code down.

Before you push, run the gates in CONTRIBUTING.md and the ratchet against
voice. The ratchet refuses anything worse than voice; a pull request that must
make a measure worse says why on a line of its own in its text,
`Ratchet-Exception: <measure> - why`, and that is Roy's call too. The measure
there is the ratchet's name for it: `coverage`, `mutation`, `tests`,
`lint_errors`, `type_errors`, `cycles`, `layer_violations`, `duplication`,
`complexity` or `test_seconds`.

## 4. Exemptions

Sometimes a cell should stay outside its limit: a function that is one long
match on a tool's name, a generated table, a test that has to wait for real
hardware. Such a cell may be marked exempt, and only like this:

- **A written reason**, saying why meeting the limit would make the code worse,
  not that it would take long.
- **Roy's yes.** Ask through the manager session (`claude-voice-work`) with the
  exact entry and your recommendation; never add one on your own judgement,
  and never count silence as a yes.
- **In this repository's own `quality.toml`**, at its root, in a pull request
  of its own, one entry per measure, with the measure's id as the page names
  it:

```toml
[[exempt]]
measure = "complexity"
reason = "the tool table is one long match on the tool's name"
approved = "Roy, 2026-11-11, in pull request #42"
```

The bridge reads `quality.toml` from its clone of this repository. The page
then shows that cell as exempt, with the reason, never as green. An entry
without both a reason and an approval is ignored, and a measure back inside
its limit is green again whatever the file says, so delete the entry then. The
ids are `coverage`, `mutation`, `tests`, `problems`, `cycles`, `layers`,
`duplication`, `complexity` and `test_time`.

## 5. Land it

Push your own branch to the fork, never to Sandra's repository, and open the
pull request against voice:

```sh
gh pr create --repo roykollensvendsen/dreame-mcp --base voice
gh pr checks --watch --repo roykollensvendsen/dreame-mcp
gh pr merge --rebase --repo roykollensvendsen/dreame-mcp
```

Merge only once every check is green. Then wait for the `checks` run on the
push to voice to finish: only the levels it keeps move the page, and the page
itself refreshes every ten minutes. Look at the cell again; if it is still not
green, start again at section 3 with what it now says.
