---
name: robot-map
description: Get, show or debug the vacuum's map - the live map with where the robot is now, the saved map with its rooms, the backups, or a short animation of it moving. Use when someone asks where the robot is, wants to see the map, or the map tool fails (NoSuchKey, no MapData, "source": "saved").
---

# The vacuum's map

How the fetch works, step by step: [docs/MAP_AND_ROBOTICS.md](../../../docs/MAP_AND_ROBOTICS.md).
Run everything from the repository folder, with the login from
`~/.config/dreame` (never print it).

## See it

- Live map with the robot: `client.get_map()` → `image` (PNG, base64),
  `map_data.robot_position`, `map_data.rooms`, `source`.
- A film of it moving (only useful while it drives):
  `uv run python .claude/skills/robot-map/scripts/film.py /tmp/film.gif 180 3`.
- Send pictures to Roy with SendUserFile; he is often on his phone.

## What is where

| Property | Holds |
|---|---|
| 6.3 `OBJECT_NAME` | live map file, `<name>,<key>` |
| 6.8 `MAP_LIST` | saved maps file: `mapstr[]` + `curr_id` (a `map_id`) |
| 6.9 `RECOVERY_MAP_LIST` | backups the app can restore |
| 6.7 `MULTI_FLOOR_MAP` | 0 = one map only (Roy's, Nov 2026) |
| 4.20 relocation | 0 located, 1 locating, 10 failed, 11 succeeded |

Read properties with `client._safe_call("send", "get_properties", [...])`
and `_miot_did_slots`, as `_get_status_sync` does.

## Lessons

- `NoSuchKey` means the `,<key>` was sent as part of the file name.
- The file is AES-encrypted: the key from the name, plus the model's IV from the
  reference clone's `DEVICE_INFO` (`r2363a` has one). No IV, no map.
- The live map only changes when someone asks for it (action 6.1, which the
  app sends too). Reading 6.3 alone gives the same frame for minutes.
- Action 6.1 asks the robot to upload its map; it does not move it. Still
  tell Roy before sending any action.
- Roy's robot: one saved map (ground floor, 5 rooms "Room 1"-"Room 5", dock in
  Room 5). The loft (garage) is unmapped and got a new map with the same
  `map_id` (1) while it cleaned there; with multi-floor off, check that the
  ground floor survives each loft clean. On 6 Nov 2026 it did.
- The L10s Plus has no camera, so there is no "go to point" (cruising).
  The reference project fakes it with a small zone clean.
