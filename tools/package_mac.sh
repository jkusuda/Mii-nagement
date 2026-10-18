#!/usr/bin/env bash
# Build the macOS app and its .dmg (`make app`): the game exported by Godot, with the
# backend's source inside, so the app starts its own backend (game/launcher.gd, which
# also fetches uv on the first launch).
# Not notarized (that needs a paid Apple account): the first time, macOS refuses it, and
# people allow it in System Settings > Privacy & Security > Open Anyway.
set -euo pipefail
cd "$(dirname "$0")/.."

GODOT="${GODOT:?set GODOT to the Godot binary}"
out=game/builds/macos
app="$out/Mii-nagement.app"
res="$app/Contents/Resources"

# Export from a copy of the game (.app-src, kept between runs so only changes re-import)
# with the ETC2/ASTC texture formats Apple Silicon needs. Turning that on in the project
# itself would make every editor re-import every texture.
rsync -a --delete --exclude .godot --exclude builds game/ .app-src/
sed -i '' 's/^\[rendering\]$/[rendering]\
\
textures\/vram_compression\/import_etc2_astc=true/' .app-src/project.godot
"$GODOT" --headless --path .app-src --import >/dev/null 2>&1 || true

rm -rf "$out" && mkdir -p "$out"
"$GODOT" --headless --path .app-src --export-release macOS "$PWD/$app"

rsync -a --exclude .venv --exclude .worktrees --exclude __pycache__ --exclude '*.db*' \
  --exclude .secrets.json --exclude tests --exclude .pytest_cache --exclude .ruff_cache \
  backend/ "$res/backend/"

# An ad-hoc signature over the backend we added (Apple Silicon won't run unsigned code).
codesign --force --deep --sign - "$app"

# The .dmg: the app and a shortcut to Applications to drag it onto.
stage=$(mktemp -d)
cp -R "$app" "$stage/" && ln -s /Applications "$stage/Applications"
hdiutil create -quiet -volname Mii-nagement -srcfolder "$stage" -ov -format UDZO "$out/Mii-nagement.dmg"
rm -rf "$stage"
echo "Built $out/Mii-nagement.dmg ($(du -h "$out/Mii-nagement.dmg" | cut -f1))"
