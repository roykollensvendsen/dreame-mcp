# Deferred

What has been put off for the voice server, and what would make it worth
doing.

| What | Why not now | Do it when |
|---|---|---|
| `vacuum_clean_rooms` asks first under `--ask-before-start`, as `vacuum_clean_all` does | Nobody runs the server in that mode | Someone turns `--ask-before-start` on |
| Send the vacuum to a spot without cleaning | The L10s Plus has no camera, which its cruise mode needs; a small zone clean is the nearest thing | Roy asks for it |
| Upstream fixes: the charging flag, `.env` permissions | Not yet offered to Sandra | Roy says to send them |
| The ratchet takes the same one-run lock as the mutation run | It keeps its files only in folders of its own, with no sweep, so two ratchets cannot remove each other's; the mutation run it starts takes the lock itself | The ratchet gets a sweep for folders a killed run left: the sweep then honours a lock per folder, or runs under the checkout lock |
