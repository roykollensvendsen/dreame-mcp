---
name: voice-plugin
description: Change, test or roll out the vacuum's voice plugin (dreame-voice-mcp) that the claude-voice bridge offers Roy in the Claude app. Use when a vacuum_* tool or its spoken answer changes, when the plugin misbehaves by voice, or when its bridge entry, approval or arguments change.
---

# The vacuum voice plugin

`dreame-voice-mcp` (`src/dreame_mcp/voice.py`) is served to Roy's voice
through the claude-voice bridge as the plugin `vacuum`. The robot is a Dreame
**L10s Plus** (`dreame.vacuum.r2363a`), not the D20 Pro Plus that upstream's
README names. What the tools do and say: [docs/VOICE.md](../../../docs/VOICE.md).

## How it is wired

- The bridge owns `[plugins.vacuum]` in `~/.config/claude-voice/config.toml`;
  the session `claude-voice-work` edits it, never this one.
- It runs `uv run dreame-voice-mcp …` **in this checkout**, so whatever branch
  is checked out here is what Roy talks to. Work on another branch in a
  `git worktree`, never by switching branches here, and keep this one passing.
- Simulated: `["run", "dreame-voice-mcp", "--simulate"]`.
  Real: `["run", "dreame-voice-mcp", "--secrets-dir", "/home/roy/.config/dreame"]`.
  The bridge does not expand `~` in args (the server does anyway).
- The login is in `~/.config/dreame/{user,password}` (600). Never print them,
  pass them to the bridge or put them in `.env`.

## Changing it

1. Change `voice.py`, keep `uv run pytest tests/test_voice.py` green.
2. Any change to the command, args, folder, tool list or a tool's inputs voids
   Roy's approval. Tell `claude-voice-work`; Roy runs
   `claude-voice plugins approve vacuum` himself. Never approve for him.
3. Try it simulated first, then one real `vacuum_status` (read-only), before
   anything that moves the robot.

## Lessons

- Answer within about 15 s; the Claude app cuts longer calls off.
  `CALL_BUDGET` is 14 s.
- Do not retry a failed login quickly: the Xiaomi cloud then asks for a
  captcha. `RETRY_AFTER` is 60 s.
- Read state and charging from the raw codes (`2.1`, `3.2`), not
  `DreameStatus.is_charging`, which counts "not charging" (2) as charging.
- Idle off or on the dock looks the same (state 2, charging 2), so the answer
  says only "idle".
- Where it is and the map: see the `robot-map` skill. A `vacuum_where`
  tool is proposed (room from the live map, relocation status), not built.
