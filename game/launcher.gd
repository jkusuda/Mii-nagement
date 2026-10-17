extends Node
## Launcher: in the packaged desktop app (`make app`), starts the backend that ships
## inside it and stops it on quit. Everywhere else (the editor, `make`, the web
## export) it does nothing: there the backend is started by hand or lives on a server.
##
## The app carries the backend's source (Mii-nagement.app/Contents/Resources/backend).
## On the first launch it downloads uv (checked against its published checksum), and
## uv installs Python and the dependencies, all into the user data folder: a minute or
## so, and it needs the internet. Later launches start in a few seconds. Until then Net
## shows "Reconnecting…" and keeps trying, as with any backend that isn't up yet.
## (uv isn't shipped inside the app: macOS blocks helper programs that came with a
## downloaded, un-notarized app, while the app itself is opened once with "Open Anyway".)
##
## Godot concept: this is an autoload listed *before* Net in project.godot, so its
## _init() runs first and can set MII_SERVER before Net reads it.

const PORT := "8765"  # not 8000, so a `make server` backend can run next to the app
const UV_VERSION := "0.13.1"

var _pid := -1  # the backend process, -1 when we didn't start one
var _thread: Thread


func _init() -> void:
	var resources := _resources_dir()
	if resources.is_empty():
		return
	var data := OS.get_user_data_dir()
	# Godot concept: OS.set_environment() changes this process's environment, which
	# processes started from it (uv, the backend) inherit.
	OS.set_environment("MII_SERVER", "127.0.0.1:" + PORT)
	OS.set_environment("DATABASE_PATH", data.path_join("mii.db"))
	OS.set_environment("WORKTREE_ROOT", data.path_join("worktrees"))
	OS.set_environment("UV_PROJECT_ENVIRONMENT", data.path_join("venv"))
	# Python's .pyc files go here too: written into the app, they'd break its signature.
	OS.set_environment("PYTHONPYCACHEPREFIX", data.path_join("pycache"))
	OS.set_environment("PATH", _login_path())
	# Godot concept: a Thread runs a function next to the game, so the first-launch
	# install doesn't freeze the window.
	_thread = Thread.new()
	_thread.start(_start_backend.bind(resources, data))


## Contents/Resources of the app bundle when it carries a backend, else "".
func _resources_dir() -> String:
	if OS.has_feature("editor"):
		return ""
	var resources := OS.get_executable_path().get_base_dir().path_join("../Resources").simplify_path()
	return resources if FileAccess.file_exists(resources.path_join("backend/manage.py")) else ""


## A Mac app opened from the Finder gets a bare PATH (no Homebrew, no npm tools), so the
## backend wouldn't find git, gh or the agent CLIs. Ask the user's login shell for theirs.
func _login_path() -> String:
	var output := []
	var shell := OS.get_environment("SHELL") if OS.has_environment("SHELL") else "/bin/zsh"
	if OS.execute(shell, ["-ilc", "echo $PATH"], output) == 0 and output:
		var lines: PackedStringArray = str(output[0]).strip_edges().split("\n")
		if lines and lines[-1].contains("/"):
			return lines[-1]
	return OS.get_environment("PATH") + ":/opt/homebrew/bin:/usr/local/bin"


## In the thread: get uv, install or update the dependencies, then start the server.
func _start_backend(resources: String, data: String) -> void:
	var uv := _get_uv(data)
	if uv.is_empty():
		return
	var backend := resources.path_join("backend")
	var output := []
	var code := OS.execute(uv, ["sync", "--frozen", "--no-dev", "--project", backend], output, true)
	if code != 0:
		push_error("Could not install the backend (uv exited with %d): %s" % [code, "".join(output)])
		return
	_pid = OS.create_process(
		data.path_join("venv/bin/python"),
		["-m", "uvicorn", "--app-dir", backend, "mii.asgi:application", "--host", "127.0.0.1", "--port", PORT]
	)


## uv in the user data folder, downloaded the first time. "" when that failed.
func _get_uv(data: String) -> String:
	var folder := data.path_join("uv-" + UV_VERSION)
	var uv := folder.path_join("uv")
	if FileAccess.file_exists(uv):
		return uv
	var arch := "aarch64" if Engine.get_architecture_name() == "arm64" else "x86_64"
	var name := "uv-%s-apple-darwin.tar.gz" % arch
	var url := "https://github.com/astral-sh/uv/releases/download/%s/%s" % [UV_VERSION, name]
	# curl, shasum and tar come with macOS. shasum -c checks the file against uv's own checksum.
	var script := (
		(
			"set -e; mkdir -p '%s'; cd '%s'; curl -fsSL '%s' -o '%s'; curl -fsSL '%s.sha256' | shasum -a 256 -c -;"
			+ " tar xzf '%s' --strip-components 1; rm '%s'"
		)
		% [folder, folder, url, name, url, name, name]
	)
	var output := []
	var code := OS.execute("/bin/sh", ["-c", script], output, true)
	if code != 0 or not FileAccess.file_exists(uv):
		push_error("Could not download uv (%d): %s" % [code, "".join(output)])
		DirAccess.remove_absolute(folder)
		return ""
	return uv


## Godot concept: _exit_tree() runs when the node leaves the tree, which for an
## autoload means the game is quitting. Stop the backend with it.
func _exit_tree() -> void:
	if _thread:
		_thread.wait_to_finish()
	if _pid > 0:
		OS.kill(_pid)
