# The voice server

`dreame-voice-mcp` is a second, much smaller MCP server in this package, for
voice assistants and other clients that read a one-line answer aloud. It speaks
MCP over stdio only, and has ten tools and no web app or sampling.

| Tool | Does | Asks first |
|---|---|---|
| `vacuum_status` | Says what the vacuum is doing and its battery | — |
| `vacuum_full_status` | Says everything it reports, problems first | — |
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

`vacuum_full_status` is for when the user asks for everything; `vacuum_status`
stays the short one. It reads, and only reads: errors and warnings, the
water tank (fitted or out), the mop pad, the water-flow setting, how much is
left of the main brush, side brush, filter, mop pad and sensors (a part at 10
percent or less is due), and the base's auto-empty and dust bag. Problems come
first:

"Problems: the dust bin is full; the sensors are due for cleaning. It is on
the dock, fully charged, battery 100 percent; water tank fitted, mop pad not
attached, water flow medium; left: main brush 87, side brush 81, filter 75,
mop pad 100, sensors 0 percent; auto-empty on, dust bag fitted. It can't
report the water level or how full the bin is, and the L10s Plus base has no
water tanks."

While it cleans, how far it has got comes straight after what it is doing,
and the room is the one its live map puts it in (a saved map names none):
"It is cleaning, battery 92 percent; 30 percent done, 17 square metres in 17
minutes; now in Kitchen; …". The cloud gives the share of the floor done,
the square metres and the minutes, but not which rooms are done (the map has
no flag for it, and no order on a full clean) or how long is left, so during
a clean the last sentence says so too. It works out no time left from the
pace either: that would be a guess, so none is said.

That last sentence is always said. The cloud reports the water-flow setting
but not how much water is in the tank, and how full the bin is only as a
warning once it is full. It does answer for a base's clean and dirty water
tanks, but an L10s Plus base has none, so those readings are left out. Which
properties the cloud answers was read from Roy's L10s Plus
(`dreame.vacuum.r2363a`) on 2026-11-14; `tests/test_voice_full_status.py`
keeps that answer, and `FULL_STATUS` in `client.py` says what each one means.

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
wait; and a kept wait ends at most its own wait (60 seconds, or 5 minutes after
an 87001) after the file was written.

A captcha waits the ordinary 60 seconds, not 5 minutes. Xiaomi calls it a
rate-limit, but it doesn't clear with time: it clears once it is answered in
the DreameHome app, which is what the voice asks for. A longer wait would only
keep the vacuum off after that is done, and one try a minute is still far from
the run of logins that brings a captcha on.

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
