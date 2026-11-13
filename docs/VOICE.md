# The voice server

`dreame-voice-mcp` is a second, much smaller MCP server in this package, for
voice assistants and other clients that read a one-line answer aloud. It speaks
MCP over stdio only, and has nine tools and no web app or sampling.

| Tool | Does | Asks first |
|---|---|---|
| `vacuum_status` | Says what the vacuum is doing and its battery | — |
| `vacuum_clean_all` | Starts a full clean, unless it is already cleaning | only with `--ask-before-start` |
| `vacuum_pause` | Pauses a clean, unless there is nothing to pause | no |
| `vacuum_stop` | Stops where it is, unless there is nothing to stop | no |
| `vacuum_go_home` | Sends it to the dock, unless it is already there | no |
| `vacuum_find` | Makes it beep | no |
| `vacuum_where` | Says which room it is in, from its live map, and whether it is on its dock | — |
| `vacuum_rooms` | Names the rooms on its map | — |
| `vacuum_clean_rooms` | Cleans only the rooms named, in that order | no |

Only `vacuum_clean_rooms` takes an input, `rooms`, a list of room names. Every tool answers in the same shape:

```json
{"success": true, "say": "The vacuum is charging on the dock, battery 84 percent.", "state": "charging", "battery": 84}
```

`vacuum_where` adds `room`; `vacuum_rooms` and `vacuum_clean_rooms` add `rooms`, a list of names.
Room names are the ones set in the DreameHome app; unnamed rooms are
"Room 1", "Room 2" and so on. On a map without rooms, for example a floor it
has not mapped before, `vacuum_where` says so. When only the saved map can be
had, it says it does not know where the vacuum is now.

`vacuum_clean_rooms` matches each name against the map's rooms, ignoring
case, spacing and a leading "the", so "room 3" finds "Room 3" and a name set
later in the app works at once. An unknown name, or none, cleans nothing and
the answer names the rooms there are. It does not start while the vacuum is
already cleaning; stop it first. It sends what the DreameHome app sends for a
room clean (action 4.1 with status 18 and the rooms in property 4.10), with
the robot's current suction and water settings and one pass per room, then
reads the state back: "Started cleaning Kitchen and Hallway, battery 90 percent."

`say` is meant to be read aloud as it is. `state` is one short word
(`cleaning`, `paused`, `returning`, `charging`, `docked`, `idle`, `error`, …)
for a client that wants to act on it.

## Starting it

```bash
dreame-voice-mcp --simulate                          # a pretend vacuum: no login, no cloud
dreame-voice-mcp --secrets-dir ~/.config/dreame      # login read from files 'user' and 'password'
dreame-voice-mcp                                     # login from DREAME_USER / DREAME_PASSWORD or .env
dreame-voice-mcp --ask-before-start                  # vacuum_clean_all asks; vacuum_confirm(token) starts
```

Run it from the repository folder (`uv run dreame-voice-mcp …`), so it finds
`.env` for the region, device id and reference path.

`--secrets-dir` names a folder holding two files, `user` and `password`, with
the DreameHome login. Keep the folder at mode 700 and the files at 600. The
server expands a leading `~` itself, because a launcher without a shell passes
it on literally. The login never appears in an answer or a log.

A client config for Claude Desktop, OpenCode and the like:

```json
{
  "mcpServers": {
    "vacuum": {
      "command": "uv",
      "args": ["run", "dreame-voice-mcp", "--secrets-dir", "/home/me/.config/dreame"],
      "cwd": "/path/to/dreame-mcp"
    }
  }
}
```

## When the cloud is not there

The server logs in in the background as soon as it starts, so the tool list
comes back at once. Every call then answers within about 14 seconds:

- while the login is still running: "I'm still logging in to the Dreame cloud.
  Ask again in a moment."
- when the cloud cannot be reached: "I can't reach the Dreame cloud right now.
  Try again in a minute." For a command it adds "The vacuum wasn't told
  anything." before the last sentence, and `success` is false.
- when a command was handed to the cloud but no answer came back in time:
  "The Dreame cloud took too long to answer, so the command may still reach
  the vacuum. Ask me for its status before you try again."
- when the cloud answered a command but not with a yes: "The Dreame cloud
  didn't confirm the command, so it may have reached the vacuum or not. Ask me
  for its status before you try again." Neither says the vacuum wasn't told,
  since it may have been.
- when the password is wrong: "The Dreame cloud turned down the login. Ask
  the claude-voice manager to open the DreameHome login boxes, then to restart
  the vacuum plugin." The manager's boxes are masked, so the password never
  touches a terminal or a chat.
- when the cloud wants a 2FA or captcha check: "Open the DreameHome app, then
  ask again in a minute."

Every answer that is not a success says what is wrong and then what to do
next, as its last sentence; a bare question is not a next step.
`tests/test_voice_next_step.py` reads every such answer from the source and
fails on one whose last sentence does not start with something to do.

After a failed login, the server waits 60 seconds before it tries again,
however often it is asked, because repeated logins make the Xiaomi cloud ask
for a captcha. A failed cloud login is also kept in a small file,
`~/.local/state/dreame-mcp/last-failed-login` (mode 600, under
`$XDG_STATE_HOME` when that is set), which the web page's Settings reads too:
after a failed login on either side, neither logs in again for 60 seconds,
or for 5 minutes when the cloud refused it for too many logins (87001), and a
good login on either side ends the wait. Meanwhile the voice says "A Dreame
login failed a moment ago, so I'm waiting before I try again. Ask again in a
minute.", or after an 87001 "The Dreame cloud has had too many logins and
wants me to wait. Ask again in a few minutes, not sooner." The file holds only
the time and a reason code. One it cannot read, or one dated after now, is no
wait; and a kept wait ends at most 5 minutes after the file was written.

## Asking first

Every command can be undone by another, so by default none asks. With
`--ask-before-start`, `vacuum_clean_all` answers with a question and a token
(`"question": true, "token": "…"`), and a seventh tool, `vacuum_confirm(token)`,
starts the clean. The token works once, only for the latest question, and
only for two minutes. A wrong token closes the question.

## The pretend vacuum

`--simulate` runs the same tools against `dreame_mcp/simulator.py`, a vacuum
that runs on a clock. On the dock it charges, while cleaning its battery runs
down, below 15 percent it heads home, and the trip takes 20 seconds. Its map
has a living room (with the dock), a kitchen and a hallway. Every
answer begins "Simulated:", and the server's instructions tell the client to
say so. The tests use it, with `offline=True` for the cloud being down.

## Tests

```bash
uv run pytest tests/test_voice.py
```

They cover the spoken text for each state, a whole clean on the pretend
vacuum, where it is and its rooms, cleaning chosen rooms, ask-first tokens, the cloud being down, a slow login and a slow read,
the wait before a new login, that no password or account name reaches an
answer or a log, and that nothing but MCP reaches stdout.
