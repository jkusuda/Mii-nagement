extends Node
## Launcher: in the packaged desktop app (`make app` on macOS, `make windows`), starts the
## backend that ships with it and stops it on quit. Everywhere else (the editor, `make`)
## it does nothing: there the backend is started by hand or lives on a server.
##
## Windows: the installer puts everything next to Mii-nagement.exe (tools/package_windows.sh):
## the backend, a Python with its packages already installed, Git and Node (for the agents'
## `npx`). So the backend starts at once, with nothing to download or set up.
##
## macOS: the app carries the backend's source (Mii-nagement.app/Contents/Resources/backend).
## On the first launch it downloads uv (checked against its published checksum), and
## uv installs Python and the dependencies, all into the user data folder: a minute or
## so, and it needs the internet. Later launches start in a few seconds. Until then Net
## keeps trying, as with any backend that isn't up yet, and the connection pill shows
## `status` ("Setting up…", or why it failed: it tries again every 30 seconds).
## (uv isn't shipped inside the app: macOS blocks helper programs that came with a
## downloaded, un-notarized app, while the app itself is opened once with "Open Anyway".)
##
## Godot concept: this is an autoload listed *before* Net in project.godot, so its
## _init() runs first and can set MII_SERVER before Net reads it.

## `status` changed (emitted on the main thread).
signal status_changed

const PORT := "8765"  # not 8000, so a `make server` backend can run next to the app
const UV_VERSION := "0.13.1"
const RETRY_SECONDS := 30  # after a failed setup (say, no internet on the first launch)
const SHELL_TIMEOUT_MS := 5000  # the login shell asked for PATH; a stuck .zshrc doesn't hold us up

## What the backend is up to, for the connection pill: "" once started (or outside the app).
var status := ""

var _pid := -1  # the backend process, -1 when we didn't start one
var _helper_pid := -1  # a setup step running now (uv download, uv sync, the login shell)
var _quitting := false  # set on quit: the setup thread stops at its next step
var _thread: Thread


func _init() -> void:
	var resources := _resources_dir()
	if resources.is_empty():
		return
	var data := OS.get_user_data_dir()
	DirAccess.make_dir_recursive_absolute(data)
	# Godot concept: OS.set_environment() changes this process's environment, which
	# processes started from it (uv, the backend) inherit.
	OS.set_environment("MII_SERVER", "127.0.0.1:" + PORT)
	OS.set_environment("DJANGO_DEBUG", "0")  # a shipped app: no debug pages or debug logging
	OS.set_environment("DATABASE_PATH", data.path_join("mii.db"))
	OS.set_environment("WORKTREE_ROOT", data.path_join("worktrees"))
	if OS.get_name() == "Windows":
		_start_windows(resources, data)
		return
	OS.set_environment("UV_PROJECT_ENVIRONMENT", data.path_join("venv"))
	# Python's .pyc files go here too: written into the app, they'd break its signature.
	OS.set_environment("PYTHONPYCACHEPREFIX", data.path_join("pycache"))
	# Godot concept: a Thread runs a function next to the game, so the first-launch
	# install doesn't freeze the window.
	_thread = Thread.new()
	_thread.start(_start_backend.bind(resources, data))


## Where the shipped backend is: Contents/Resources of the macOS app bundle, or the folder
## of Mii-nagement.exe on Windows. "" when this game didn't come with one.
func _resources_dir() -> String:
	if OS.has_feature("editor"):
		return ""
	var here := OS.get_executable_path().get_base_dir()
	for resources in [here.path_join("../Resources").simplify_path(), here]:
		if FileAccess.file_exists(resources.path_join("backend/manage.py")):
			return resources
	return ""


## Windows: start the backend with the Python the installer brought (mii/desktop.py writes
## its output to backend.log in the user data folder, for when something goes wrong).
func _start_windows(resources: String, data: String) -> void:
	var runtime := resources.path_join("runtime")
	var python := runtime.path_join("python/python.exe")
	if not FileAccess.file_exists(python):
		status = "The backend is missing: reinstall Mii-nagement"
		return
	# The user's own git and node come first; ours fill in when they have none.
	var path := OS.get_environment("PATH")
	for folder in ["git/cmd", "node"]:
		path += ";" + runtime.path_join(folder).replace("/", "\\")
	OS.set_environment("PATH", path)
	OS.set_environment("PYTHONPATH", resources.path_join("backend"))
	OS.set_environment("MII_PORT", PORT)
	OS.set_environment("MII_LOG_FILE", data.path_join("backend.log"))
	_pid = OS.create_process(python, ["-m", "mii.desktop"])


## In the thread: get uv, install or update the dependencies, start the server. A failed
## step is shown in the pill and the whole setup tries again a little later.
func _start_backend(resources: String, data: String) -> void:
	OS.set_environment("PATH", _login_path(data))
	var backend := resources.path_join("backend")
	while not _quitting:
		_set_status.call_deferred("Setting up the backend…")
		var problem := _get_uv(data)
		if problem.is_empty():
			var uv := data.path_join("uv-%s/uv" % UV_VERSION)
			var result := _run(data, "exec '%s' sync --frozen --no-dev --project '%s'" % [uv, backend])
			if result["code"] != 0:
				problem = "couldn't install it: " + _last_line(result["output"])
		if _quitting:
			return
		if problem.is_empty():
			_pid = OS.create_process(
				data.path_join("venv/bin/python"),
				["-m", "uvicorn", "--app-dir", backend, "mii.asgi:application", "--host", "127.0.0.1", "--port", PORT]
			)
			_set_status.call_deferred("")
			return
		push_error("Backend setup failed: " + problem)
		_set_status.call_deferred("Backend setup failed (%s): trying again soon" % problem)
		for _i in RETRY_SECONDS * 5:
			if _quitting:
				return
			OS.delay_msec(200)


func _set_status(text: String) -> void:
	status = text
	status_changed.emit()


## A Mac app opened from the Finder gets a bare PATH (no Homebrew, no npm tools), so the
## backend wouldn't find git, gh or the agent CLIs. Ask the user's login shell for theirs.
## Whatever the shell says, the usual places for per-user tools are added too (the Claude
## Code installer puts `claude` in ~/.local/bin). The shell's answer stays in launcher-path.log.
func _login_path(data: String) -> String:
	var shell := OS.get_environment("SHELL") if OS.has_environment("SHELL") else "/bin/zsh"
	# Only stdout: an interactive shell without a terminal may print warnings on stderr.
	var query := "'%s' -ilc 'echo $PATH' 2>/dev/null </dev/null" % shell
	var result := _run(data, query, SHELL_TIMEOUT_MS, "launcher-path.log")
	var path := ""
	for line in str(result["output"]).split("\n"):
		if line.contains("/usr/bin"):
			path = line.strip_edges()  # the last line that looks like a PATH
	if path.is_empty():
		path = OS.get_environment("PATH")
	var home := OS.get_environment("HOME")
	var known := [home + "/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", home + "/.npm-global/bin"]
	for folder in known:
		if not (":%s:" % path).contains(":%s:" % folder):
			path += ":" + folder
	return path


## uv in the user data folder, downloaded the first time. Returns "" or what went wrong.
func _get_uv(data: String) -> String:
	var folder := data.path_join("uv-" + UV_VERSION)
	var uv := folder.path_join("uv")
	if FileAccess.file_exists(uv):
		return ""
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
	var result := _run(data, script)
	if result["code"] != 0 or not FileAccess.file_exists(uv):
		DirAccess.remove_absolute(folder)
		return "couldn't download uv: " + _last_line(result["output"])
	return ""


## Run a shell script and wait for it, without blocking quit: {code, output}. `code` is -1
## when it was stopped (quitting, or still running after `timeout_ms`).
## OS.execute() can't be interrupted, so the script runs as its own process (its output
## goes to a file) and this checks on it every 100 ms.
func _run(data: String, script: String, timeout_ms := -1, log_name := "launcher.log") -> Dictionary:
	var log_path := data.path_join(log_name)
	var pid := OS.create_process("/bin/sh", ["-c", "%s > '%s' 2>&1" % [script, log_path]])
	_helper_pid = pid
	var started := Time.get_ticks_msec()
	while OS.is_process_running(pid):
		if _quitting or (timeout_ms > 0 and Time.get_ticks_msec() - started > timeout_ms):
			_stop_helper()
			return {"code": -1, "output": ""}
		OS.delay_msec(100)
	_helper_pid = -1
	if _quitting:
		return {"code": -1, "output": ""}
	var code := OS.get_process_exit_code(pid)
	return {"code": code, "output": FileAccess.get_file_as_string(log_path)}


## Stop the running setup step and what it started (curl, uv...).
func _stop_helper() -> void:
	var pid := _helper_pid
	if pid > 0:
		OS.execute("pkill", ["-TERM", "-P", str(pid)])
		OS.kill(pid)
	_helper_pid = -1


static func _last_line(output: String) -> String:
	var lines := output.strip_edges().split("\n")
	return lines[-1].left(120) if lines and lines[-1] != "" else "no details"


## Godot concept: _exit_tree() runs when the node leaves the tree, which for an
## autoload means the game is quitting. Stop the setup or the backend with it.
func _exit_tree() -> void:
	_quitting = true
	_stop_helper()
	if _thread:
		_thread.wait_to_finish()
	if _pid > 0 and OS.get_name() == "Windows":
		# The whole tree: the backend and the agents it started, which would keep running.
		OS.execute("taskkill", ["/PID", str(_pid), "/T", "/F"])
	elif _pid > 0:
		# SIGTERM first so uvicorn shuts the engine down and its agents stop with it;
		# OS.kill (SIGKILL) only if it is still running a few seconds later.
		OS.execute("kill", ["-TERM", str(_pid)])
		for _i in 50:
			if not OS.is_process_running(_pid):
				return
			OS.delay_msec(100)
		OS.kill(_pid)
