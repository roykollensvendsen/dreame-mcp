# Deferred

What has been put off for the voice server, and what would make it worth
doing.

| What | Why not now | Do it when |
|---|---|---|
| `vacuum_clean_rooms` asks first under `--ask-before-start`, as `vacuum_clean_all` does | Nobody runs the server in that mode | Someone turns `--ask-before-start` on |
| "The vacuum wasn't told anything" only when that is certain | After a timeout the command may have arrived | The fail-closed work (plugin standard rule 6) |
| Send the vacuum to a spot without cleaning | The L10s Plus has no camera, which its cruise mode needs; a small zone clean is the nearest thing | Roy asks for it |
| Upstream fixes: the charging flag, `.env` permissions | Not yet offered to Sandra | Roy says to send them |
