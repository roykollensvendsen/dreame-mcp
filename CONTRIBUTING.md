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
   ```

3. Commit with a [conventional commit](https://www.conventionalcommits.org)
   subject, e.g. `fix(voice): Say when the map cannot be read`. The checks
   refuse other subjects.
4. Open one pull request per topic to `voice`. It is merged by rebase once
   its checks are green.

The voice server keeps the shared
[plugin standard](https://github.com/roykollensvendsen/claude-voice/blob/main/docs/plugin-standard.md).
Decisions are recorded in [decisions/](decisions/), and what is put off is in
[docs/DEFERRED.md](docs/DEFERRED.md).

## The real robot

Tests use the simulator and fakes, and never reach the cloud. Don't send a
command that moves the robot while trying out a change: reading its status or
its map is fine. Never put the DreameHome login in a test, a log, `.env` or a
commit (see [SECURITY.md](SECURITY.md)).
