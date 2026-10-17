"""The island's vocabulary: every name the backend, the protocol and the game agree on.

Data only, no imports from `mii`, so any module can import it without cycles.
docs/protocol.md is the reference; the game mirrors these lists in game/scenes/protocol.gd.
"""

# --- Workflow steps (building types) ------------------------------------------------
# A building is one workflow step. Roads always run in STEP_ORDER over the steps an island has.
STEP_ORDER = ("mailbox", "workshop", "lab", "courthouse", "post_office")
REQUIRED_STEPS = ("mailbox", "workshop", "post_office")  # in, do the work, out
CHECK_STEPS = ("lab", "courthouse")  # pass -> the next step, fail -> back to the workshop
STEP_NAMES = {
    "mailbox": "Mailbox",
    "workshop": "Workshop",
    "lab": "Lab",
    "courthouse": "Courthouse",
    "post_office": "Post Office",
}
# Town plans: presets of steps (the island creator's quick picks).
PLANS = {
    "classic": STEP_ORDER,
    "quick_fixes": ("mailbox", "workshop", "post_office"),  # small chores, no tests or review stop
}
# The building types each job works in, first = home.
JOB_BUILDINGS = {"planner": ["mailbox", "post_office"], "coder": ["workshop", "lab"], "reviewer": ["courthouse"]}

# --- Islands ------------------------------------------------------------------------
RULES = ("merge_pr", "install_packages", "shell")  # house rules, True = ask me first (engine.py, agents/__init__.py)
SEASONS = ("spring", "summer", "autumn", "winter")
ROAD_CONDITIONS = ("always", "pass", "fail")

# --- Residents and tasks ------------------------------------------------------------
STATES = ("idle", "walking", "working", "sleeping")
MOODS = ("happy", "focused", "confused", "frustrated", "tired", "proud")
BUBBLE_KINDS = ("confused", "struggling", "permission", "ready", "tired")
# Tool sorts on `thinking` events (ACP's ToolKind); the game words its summary by them.
TOOL_SORTS = ("read", "edit", "delete", "move", "search", "execute", "fetch", "think", "other")
# Where a task's attached files go in its worktree. A `.gitignore` of `*` inside keeps
# git from ever committing them (not even with `git add -A`).
ATTACHMENTS_DIR = ".mii-attachments"
THINKING_DETAIL_MAX = 2000  # characters of a tool's `detail` on a `thinking` event
TASK_STATUSES = ("queued", "in_progress", "done", "failed", "cancelled")
# A `ready` bubble's "send it back" option. The answer may carry a note: "Send back: also test IPv6".
SEND_BACK = "Send back"

# --- Brains ---------------------------------------------------------------------------
ACP_BRAINS = ("claude-code", "codex", "gemini")  # one entry each in agents/acp.py BRAINS
BRAIN_NAMES = ("mock", *ACP_BRAINS, "openrouter")
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"  # the openrouter brain's default model
DEFAULT_GIT_AUTHOR = "Mii-nagement <mii@localhost>"  # author of the commits the backend makes
