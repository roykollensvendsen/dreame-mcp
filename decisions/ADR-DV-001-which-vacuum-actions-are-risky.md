# ADR-DV-001: Which vacuum actions are risky, and which ask first

## Status

Accepted. Roy's choice, made when the bridge was set up (it runs the server
without `--ask-first`). Written down by Claude on 2026-11-10, as the plugin
standard's rule 7 asks.

## Context

The [plugin standard](https://github.com/roykollensvendsen/claude-voice/blob/main/docs/plugin-standard.md)
leaves it to the owner which actions are risky. A risky action takes two steps:
a question read aloud, then a confirm with a one-time token. For a garage door
that is plainly needed. A robot vacuum is different: the worst a wrong start
does is clean the floor at a bad moment, and saying "stop" undoes it.

## Decision

No vacuum action is risky enough to ask first by default:

| Tool | Changes | Asks first |
|---|---|---|
| `vacuum_status`, `vacuum_where`, `vacuum_rooms` | nothing (read-only) | no |
| `vacuum_find` | the vacuum beeps | no |
| `vacuum_pause`, `vacuum_stop`, `vacuum_go_home` | ends or interrupts a clean | no: stopping is the safe direction |
| `vacuum_start` | starts a full clean | no, unless the server runs with `--ask-first` |
| `vacuum_clean_rooms` | starts cleaning the named rooms | no, in either mode |

`--ask-first` exists for an owner who wants a start to be confirmed. Then
`vacuum_start` asks, and `vacuum_confirm` starts the clean with the token. The
token expires after 120 seconds, and a wrong token closes the question. The
token is spent only once the start has been sent, so a confirm that never got
through can be tried again with the same token. A retry reads the state first,
so a start that did arrive is not sent twice.

## Consequences

- By voice, "start the vacuum" starts it. The instructions still tell the
  voice to call a command only when the user asks for it now, and never to
  start a clean on its own.
- With `--ask-first`, `vacuum_clean_rooms` still starts without a question.
  That is not deliberate: nobody runs the server in that mode today. If an
  owner turns `--ask-first` on, `vacuum_clean_rooms` should ask too. Deferred
  until then.
