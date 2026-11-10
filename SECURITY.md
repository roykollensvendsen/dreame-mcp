# Security

## Reporting

For the voice server and anything else on this fork's `voice` branch, report
privately through GitHub: **Security → Report a vulnerability** on
[roykollensvendsen/dreame-mcp](https://github.com/roykollensvendsen/dreame-mcp/security).
Please don't open a public issue. For the rest of dreame-mcp, report to
[Sandra Schipal's repository](https://github.com/sandraschi/dreame-mcp).

## What the voice server promises

- The DreameHome login is in two files in a folder of its own (`user` and
  `password`, readable by the owner alone), given with `--secrets-dir`. It is
  never passed through the voice bridge or the conversation, never written to
  `.env`, and never logged. A spoken answer about a failed login doesn't name
  the account. `test_the_password_is_never_in_an_answer_or_a_log` checks this.
- The server speaks MCP over stdio only and opens no port.
- It acts only when called. Nothing is retried on its own initiative, apart
  from logging in again after a pause.
