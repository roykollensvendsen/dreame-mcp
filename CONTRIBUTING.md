# Contributing to this fork

This fork is the home of `dreame-voice-mcp`, the vacuum's voice server
(`src/dreame_mcp/voice.py`, [docs/VOICE.md](docs/VOICE.md)). Everything else is
[Sandra Schipal's dreame-mcp](https://github.com/sandraschi/dreame-mcp), and her
ways apply to it.

## Where a change goes

- **The voice server:** a pull request to `voice`, this fork's default branch.
- **A general fix** (the cloud client, the map, the web app): a branch off
  `main` and a pull request to Sandra. This fork's `main` only follows hers.

## How a change is made

1. Write the test first and see it fail. A test that has never failed tests
   nothing.
2. Make it pass. Run what the checks run:

   ```sh
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run pyright src
   uv run pytest -q
   uv run python scripts/mutate.py   # about three minutes
   ```

   The `quality` job compares the pull request with the levels its base
   kept (coverage, mutation tally, tests, lint and type errors, import cycles,
   imports that break the layers, copied code, the most tangled function per
   module, the suite's time) and fails when one gets worse. A new module needs
   a layer in `LAYERS` in `scripts/ratchet.py`; as it loads, a module may
   import only modules in lower layers. A deliberate step back needs a line of its own
   in the pull request's text, `Ratchet-Exception: <measure> - why`. To see
   the levels locally: `uv run python scripts/ratchet.py measure`
   (about six minutes, the mutation run included).

   A new rule in the voice server gets a `# RULE: <its words>` line above the
   line that carries it, a row in `scripts/mutations.toml`, and a test named
   `test_<its words>`. The mutation run switches each rule off in turn and
   fails unless that test fails.

3. Commit with a [conventional commit](https://www.conventionalcommits.org)
   subject, e.g. `fix(voice): Say when the map cannot be read`. The checks
   refuse other subjects.
4. Open one pull request per topic to `voice`. It is merged by rebase once
   its checks are green.

The voice server keeps the shared
[plugin standard](https://github.com/roykollensvendsen/claude-voice/blob/main/docs/plugin-standard.md).
Decisions are recorded in [decisions/](decisions/), and what is put off is in
[docs/DEFERRED.md](docs/DEFERRED.md).

## The voice check

The tests can check a description's length, but not what the voice makes of
it. After a change to a tool's name or the first line of its description,
restart the bridge (`systemctl --user restart claude-voice`), start a new
chat in the Claude app, and ask the voice which vacuum tools it sees and how
each one's description begins. Write what it said in the pull request.

## The real robot

Tests use the simulator and fakes, and never reach the cloud. Don't send a
command that moves the robot while trying out a change: reading its status or
its map is fine. Never put the DreameHome login in a test, a log, `.env` or a
commit (see [SECURITY.md](SECURITY.md)).
