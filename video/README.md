# Demo video

The hackathon demo (under 2 minutes, 1920x1080), made with [Remotion](https://www.remotion.dev)
from the game's own assets: the GLB models, the DreamLife font, the music and sound effects.
Remotion bundles its own ffmpeg, so only Node is needed.

```bash
cd video
npm install
npm run studio     # preview in the browser, scene by scene
npm run render     # -> out/mii-nagement-demo.mp4
```

`npm run assets` (run by the others) copies `game/assets` and `deploy/landing` into
`public/assets`, which is gitignored: the game stays the one source.

## Scenes (`src/scenes/`, in order in `src/Demo.tsx`)

| Scene | What it shows |
|---|---|
| `Title` | logo, wordmark, the brains |
| `IslandIntro` | the real island from the air (`flyover.mp4`), the five buildings named |
| `Residents` | the Miis up close (`residents.mp4`) with job, brain and personality cards |
| `Workflow` | a package travelling the roads (`workflow.mp4`) with the road rules |
| `Talk` | asking a real Claude Code resident what they're working on (`talk.mp4`) |
| `GameCapture` | a resident asking before a risky step, and the Residents screen |
| `Outro` | logo and repo link |

Every 3D shot is a Godot recording, so the island, pond and Miis are the game's own;
Remotion only adds titles, captions, cards and sound.

## Re-recording the game footage

`public/capture/*.mp4` come from Godot's Movie Maker mode, against a throwaway mock backend
(its own port and database, never `backend/mii.db`). The helpers are in `godot-capture/`:

1. Start a backend: `cd backend && DATABASE_PATH=/tmp/cap/mii.db MOCK_AGENTS=1 uv run python -m uvicorn mii.asgi:application --port 8011`
2. Found an island named "Sunny Isle" (`POST /islands`, with a `folder` in `/tmp/cap`), then `uv run python ../video/godot-capture/seed.py`
   for the residents and `uv run python ../video/godot-capture/answerer.py &` to keep packages and answers coming.
3. Copy `capture.gd`, `capture.tscn` and `override.cfg` into `game/` (don't commit them), then record:
   `MII_SERVER=127.0.0.1:8011 CAPTURE_MODE=island Godot --path game res://capture.tscn --always-on-top --write-movie /tmp/cap/island.avi --fixed-fps 30 --quit-after 1080`
   (modes: `flyover`, `residents`, `workflow`, and `talk` with `CAPTURE_QUESTION`; the talk needs `MOCK_AGENTS=0` and a claude-code resident).
4. Encode: `npx remotion ffmpeg -i /tmp/cap/island.avi -c:v libx264 -pix_fmt yuv420p -crf 20 public/capture/island.mp4`,
   then remove the three files from `game/`.

`--always-on-top` keeps the window drawing (otherwise frames freeze), and the 1080p size comes
from `override.cfg`, since Movie Maker ignores `--resolution`.
