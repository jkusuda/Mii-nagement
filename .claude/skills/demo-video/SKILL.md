---
name: demo-video
description: Record a demo video or trailer of the Mii-nagement game (Godot MovieWriter + ffmpeg), with a lively seeded island or a replayed task. Use when asked for a video, screen recording, trailer, GIF or demo capture of the island.
---

# Recording the island

The game is a native Godot 4 window (1280×720), so browser GIF tools don't apply. Godot's
built-in **MovieWriter** renders every frame to a file at a fixed frame rate, which gives a
clean recording even on a slow laptop.

## 1. Make the island worth watching

```bash
cd backend
uv run python -m uvicorn mii.asgi:application --reload --port 8000   # backend (mock mode by default)
uv run python manage.py seed --loop                                   # keeps dropping packages: walking, bubbles, chat
```

Alternatives:
- Replay a real recorded task at speed: `curl -X POST "localhost:8000/replay/<task_id>?speed=4"`
  (task ids from `GET /islands/<id>/tasks`). News → a shipped story → "Replay" does the same.
- Ambient life (strolls, visits, chatter) runs on its own between packages
  (`game/scenes/island/island_life.gd`, `ENABLED`); it is busiest with 3+ residents who have
  handed packages to each other (friendships).
- Time of day is real time; `game/clock.gd` has a dev override if you want golden hour.
- Seasons and the island shape change in Build mode (the game's build button) or
  `PUT /islands/<id>/layout`.

## 2. Record with MovieWriter

```bash
"$GODOT" --path game --write-movie demo.avi --fixed-fps 60   # GODOT = your Godot 4 binary (see the Makefile)
```

- `.avi` = MJPEG + uncompressed audio (big, lossless-ish); `.png` writes a frame sequence.
- Set the output size with `--resolution 1280x720` (the project default) or
  `--resolution 1920x1080` for a sharper trailer; the UI scales.
- Stop with the window close button; frames are written as they render, so the file is
  complete at any point.
- To record against another backend: `MII_SERVER=127.0.0.1:8001 ... --write-movie demo.avi`.

## 3. Finish with ffmpeg

```bash
ffmpeg -i demo.avi -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart demo.mp4
ffmpeg -ss 00:00:05 -t 12 -i demo.mp4 -vf "fps=15,scale=640:-1" demo.gif       # a short GIF
ffmpeg -i demo.mp4 -vf "scale=1280:-2" -an demo-silent.mp4                       # drop audio
```

`ffmpeg` is on PATH via Homebrew (`brew install ffmpeg`). Residents with the `video` gift can
run the same conversions through the `ffmpeg-mcp` server (`GIFTS` in `backend/mii/agents/acp.py`).

## 4. Shots that read well

- Open on the Archipelago (home) and switch into an island: the camera glides.
- A package arriving: Mailbox → Workshop walk, the hop loop while working, a ✋ bubble answered
  from the Bubbles sidebar.
- A help visit: a resident asks (speech line), a neighbour walks over, both face each other.
- News and Insights tabs for the "it's real work" beat (PR link on a shipped story).
