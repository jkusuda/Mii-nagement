#!/usr/bin/env bash
# Build the Windows installer (`make windows`, in Git Bash): game/builds/windows/Mii-nagement-Setup.exe.
# What it installs (tools/windows/installer.nsi), next to each other:
#   Mii-nagement.exe   the game, exported by Godot (its data embedded); game/launcher.gd starts the backend
#   backend/           the backend's source
#   runtime/python/    a standalone Python (uv's) with the backend's packages already installed
#   runtime/git/       MinGit, Git for Windows made for bundling in apps
#   runtime/node/      Node, for the agents' `npx`
# So it works without Python, Git or Node on the computer, and nothing downloads on first launch.
# Every download is pinned and checked (SHA-256, or Godot's SHA-512) and cached in .build-tools/.
# Not code-signed: Windows SmartScreen asks once ("More info" > "Run anyway").
set -euo pipefail
cd "$(dirname "$0")/.."

GODOT="${GODOT:?set GODOT to the Godot binary}"
UV="${UV:-uv}"
VERSION=0.2.0
PYTHON_VERSION=3.12
GIT_VERSION=2.56.0.2
GIT_SHA256=da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be
NODE_VERSION=22.23.3
NODE_SHA256=2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71
NSIS_VERSION=3.13
NSIS_SHA256=ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8
GODOT_VERSION=4.7.2  # the export template must match the editor exactly
TEMPLATES_SHA512=ca4d71c4d7b81dfc15d1a98baa07534aa95b03fdda78a0075b06672e1648d2e5f40980c9adc28d23e1b92e732ee7bf3461997aa804af74ec2fcd7a93ccb84079

export MSYS_NO_PATHCONV=1  # Git Bash: pass Windows paths and /flags to native programs untouched
cache=.build-tools
out=game/builds/windows
stage="$out/stage"
mkdir -p "$cache"

# fetch URL FILE SUM [sha256|sha512]: download into the cache once, then check it every time.
fetch() {
  local url=$1 file=$cache/$2 sum=$3 algo=${4:-sha256}
  if [ ! -f "$file" ]; then
    echo "downloading $2"
    curl -fsSL -o "$file.part" "$url" && mv "$file.part" "$file"
  fi
  echo "$sum  $file" | "${algo}sum" -c --quiet - || { rm -f "$file"; echo "$2: wrong checksum, deleted it"; exit 1; }
}

# unpack ZIP DEST: unzip into DEST (an archive with one top folder: that folder's contents).
unpack() {
  local zip=$1 dest=$2 tmp=$2.tmp
  rm -rf "$dest" "$tmp" && mkdir -p "$tmp"
  unzip -q "$zip" -d "$tmp"
  local entries=("$tmp"/*)
  if [ ${#entries[@]} -eq 1 ] && [ -d "${entries[0]}" ]; then
    mv "${entries[0]}" "$dest" && rmdir "$tmp"
  else
    mv "$tmp" "$dest"
  fi
}

"$GODOT" --version | grep -q "^$GODOT_VERSION\.stable" || { echo "needs Godot $GODOT_VERSION (update GODOT_VERSION and TEMPLATES_SHA512)"; exit 1; }

# 1. Godot's Windows export template (only that one file out of the 1.3 GB template archive).
templates="$(cygpath -u "$APPDATA")/Godot/export_templates/$GODOT_VERSION.stable"
if [ ! -f "$templates/windows_release_x86_64.exe" ]; then
  fetch "https://github.com/godotengine/godot/releases/download/$GODOT_VERSION-stable/Godot_v$GODOT_VERSION-stable_export_templates.tpz" \
    "templates-$GODOT_VERSION.tpz" "$TEMPLATES_SHA512" sha512
  mkdir -p "$templates"
  unzip -q -o -j "$cache/templates-$GODOT_VERSION.tpz" templates/windows_release_x86_64.exe templates/version.txt -d "$templates"
fi

# 2. The game. (.gdignore: the editor must not import the builds, with their Python and Node.)
rm -rf "$out" && mkdir -p "$stage" && touch game/builds/.gdignore
"$GODOT" --headless --path game --import >/dev/null 2>&1 || true
"$GODOT" --headless --path game --export-release "Windows Desktop" "$(cygpath -m "$PWD/$stage")/Mii-nagement.exe"
[ -f "$stage/Mii-nagement.exe" ] || { echo "the Godot export failed"; exit 1; }

# 3. The backend's source.
mkdir -p "$stage/backend"
tar -c -C backend --exclude=.venv --exclude=.worktrees --exclude=__pycache__ --exclude='*.db*' \
  --exclude=.secrets.json --exclude=tests --exclude=.pytest_cache --exclude=.ruff_cache . | tar -x -C "$stage/backend"

# 4. Python with the backend's packages (the versions in uv.lock).
work="$cache/python"
rm -rf "$work"
"$UV" python install "$PYTHON_VERSION" --install-dir "$(cygpath -m "$PWD/$work")" --no-bin --quiet
python_dir=$(find "$work" -maxdepth 1 -type d -name "cpython-$PYTHON_VERSION.*-windows-x86_64-none" | head -1)
[ -n "$python_dir" ] || { echo "uv didn't install Python $PYTHON_VERSION"; exit 1; }
mkdir -p "$stage/runtime" && cp -r "$python_dir" "$stage/runtime/python"
python="$(cygpath -m "$PWD/$stage/runtime/python/python.exe")"
(cd backend && "$UV" export --frozen --no-dev --no-emit-project --format requirements-txt --quiet -o "$(cygpath -m "$PWD/../$work/requirements.txt")")
"$UV" pip install --python "$python" --break-system-packages --compile-bytecode --quiet -r "$(cygpath -m "$PWD/$work/requirements.txt")"
rm -rf "$stage/runtime/python/Lib/test" "$stage/runtime/python/Lib/idlelib" "$stage/runtime/python/Lib/turtledemo"
PYTHONPATH="$(cygpath -m "$PWD/$stage/backend")" "$python" -c "import django, channels, uvicorn, acp, mii.desktop" \
  || { echo "the bundled Python can't import the backend"; exit 1; }

# 5. Git and Node.
fetch "https://github.com/git-for-windows/git/releases/download/v${GIT_VERSION%.*}.windows.${GIT_VERSION##*.}/MinGit-$GIT_VERSION-64-bit.zip" \
  "MinGit-$GIT_VERSION-64-bit.zip" "$GIT_SHA256"
unpack "$cache/MinGit-$GIT_VERSION-64-bit.zip" "$stage/runtime/git"
fetch "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-win-x64.zip" "node-v$NODE_VERSION-win-x64.zip" "$NODE_SHA256"
unpack "$cache/node-v$NODE_VERSION-win-x64.zip" "$stage/runtime/node"

# 6. The installer (its icon made from the game's).
"$UV" run --no-project --quiet --with pillow==11.3.0 python -c \
  "import sys; from PIL import Image; Image.open('game/icon.png').save(sys.argv[1], sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])" \
  "$out/icon.ico"
fetch "https://sourceforge.net/projects/nsis/files/NSIS%203/$NSIS_VERSION/nsis-$NSIS_VERSION.zip/download" "nsis-$NSIS_VERSION.zip" "$NSIS_SHA256"
[ -f "$cache/nsis-$NSIS_VERSION/makensis.exe" ] || unpack "$cache/nsis-$NSIS_VERSION.zip" "$cache/nsis-$NSIS_VERSION"
"$cache/nsis-$NSIS_VERSION/makensis.exe" -V2 -DVERSION="$VERSION" -DSTAGE="$(cygpath -w "$PWD/$stage")" \
  -DOUTFILE="$(cygpath -w "$PWD/$out/Mii-nagement-Setup.exe")" -DICON="$(cygpath -w "$PWD/$out/icon.ico")" \
  "$(cygpath -w "$PWD/tools/windows/installer.nsi")"

echo "Built $out/Mii-nagement-Setup.exe ($(du -h "$out/Mii-nagement-Setup.exe" | cut -f1))"
