"""Film the vacuum on its live map: a frame every few seconds, saved as a GIF.

    uv run python .claude/skills/robot-map/scripts/film.py OUT.gif [SECONDS] [INTERVAL] [SECRETS_DIR]

Each frame is a get_map(), which asks the robot to upload its current map
(action 6.1, no movement) and decrypts it. Frames where the robot has not
moved are skipped. Stops early when the robot is back on its dock.
"""

# ruff: noqa: T201  (a command-line script: it reports by printing)

from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys
import time

from PIL import Image

from dreame_mcp.client import client_from_env

logging.disable(logging.CRITICAL)

DOCKED = {6, 13}


async def film(out: str, seconds: float, interval: float, secrets_dir: str) -> None:
    client = client_from_env(secrets_dir)
    if client is None or not await client.connect():
        sys.exit("could not log in")
    frames: list[Image.Image] = []
    last = None
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            m = await client.get_map()
            pos = (m.get("map_data") or {}).get("robot_position")
            if m.get("success") and m.get("source") == "live" and m.get("image") and pos != last:
                frames.append(Image.open(io.BytesIO(base64.b64decode(m["image"]))).convert("RGB"))
                last = pos
                print(f"frame {len(frames)}: robot {pos}", flush=True)
            if (await client.get_status()).raw.get("2.1") in DOCKED:
                print("on the dock, stopping", flush=True)
                break
            await asyncio.sleep(interval)
    finally:
        client.disconnect()
    if not frames:
        sys.exit("no frames")
    w, h = max(f.width for f in frames), max(f.height for f in frames)
    padded = []
    for f in frames:
        bg = Image.new("RGB", (w, h), "white")
        bg.paste(f, ((w - f.width) // 2, (h - f.height) // 2))
        padded.append(bg)
    padded[0].save(out, save_all=True, append_images=padded[1:], duration=400, loop=0, optimize=True)
    print(f"saved {len(padded)} frames to {out}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    asyncio.run(
        film(
            a[0],
            float(a[1]) if len(a) > 1 else 180,
            float(a[2]) if len(a) > 2 else 3,
            a[3] if len(a) > 3 else "~/.config/dreame",
        )
    )
