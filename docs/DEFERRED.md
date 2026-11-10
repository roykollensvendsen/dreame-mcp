# Deferred

What has been put off for the voice server, and what would make it worth
doing.

| What | Why not now | Do it when |
|---|---|---|
| `vacuum_clean_rooms` asks first under `--ask-first`, as `vacuum_start` does | Nobody runs the server in that mode | Someone turns `--ask-first` on |
| Rename `vacuum_start` (e.g. to `vacuum_clean_all`), away from `vacuum_stop` | A rename changes the tool list, so the plugin needs approving again | Roy decides, or the voice mixes the two up |
| "The vacuum wasn't told anything" only when that is certain | After a timeout the command may have arrived | The fail-closed work (plugin standard rule 6) |
| Send the vacuum to a spot without cleaning | The L10s Plus has no camera, which its cruise mode needs; a small zone clean is the nearest thing | Roy asks for it |
| Upstream fixes: the charging flag, `.env` permissions | Not yet offered to Sandra | Roy says to send them |
