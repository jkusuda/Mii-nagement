"""AcpRunner: runs a resident as a real coding agent over the Agent Client Protocol.

ACP (https://agentclientprotocol.com) is an open, JSON-RPC-over-stdio protocol
between a *client* (us) and a coding *agent* (Claude Code, Codex, Gemini CLI).
We start the agent as a child process, send it one prompt, and it streams back
progress, asks permission for risky actions, and can ask us to read/write files.

Each brain authenticates the way its CLI already does: the user's subscription
login first (Claude Pro/Max, ChatGPT, Google), or an API key in the environment
(ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY), which the child inherits.

Python SDK: `agent-client-protocol` (https://agentclientprotocol.github.io/python-sdk/).
"""

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, Client, RequestError, connect_to_agent
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    EnvVariable,
    FileEditToolCallContent,
    FileSystemCapabilities,
    Implementation,
    McpServerStdio,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    WriteTextFileResponse,
)

from mii import prefs, usage
from mii.agents import (
    VERDICT_PATTERN,
    AgentJob,
    StepResult,
    ThinkingStream,
    build_prompt,
    chat_followup,
    parse_verdict,
    permission_for,
)
from mii.personality import build_system_prompt
from mii.sandbox import Sandbox, SandboxError, inside, require_worktree
from mii.vocab import THINKING_DETAIL_MAX

log = logging.getLogger(__name__)

AUTH_REQUIRED = -32000  # ACP error code: the agent wants a login first

# brain -> how to start its ACP agent and what makes it tick. Adding another ACP agent = one more entry.
#   command        the agent process (speaks ACP on stdio)
#   needs          a CLI that must be on PATH
#   api_key_env    the API key it accepts instead of a subscription login
#   login_marker   a file that exists once the CLI is logged in (None = can't tell; macOS keychains)
#   edit_mode / readonly_mode   the agent's own session modes we switch on (None = leave the default).
#                  Claude Code runs read-only steps in `acceptEdits` too: MiiClient refuses edit
#                  permissions and file writes there instead (its `plan` and `default` modes stall).
#   env            extra environment for the process
#   tip            a line added to a fresh session's prompt about what this agent can do (None = nothing)
BRAINS = {
    "claude-code": {
        "command": ["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
        "needs": "claude",
        "api_key_env": "ANTHROPIC_API_KEY",
        "login_hint": "run `claude` once and log in with your Claude subscription",
        "login_marker": None,
        "edit_mode": "acceptEdits",
        "readonly_mode": "acceptEdits",
        "env": {},
        "tip": (
            "Work in parallel: when the job splits into independent parts (separate files, a search "
            "through the code, writing tests next to the change), launch several subagents with your "
            "Agent tool in one message so they run at the same time, then combine their results."
        ),
    },
    "codex": {
        "command": ["npx", "-y", "@agentclientprotocol/codex-acp"],  # bundles its own Codex binary
        "needs": "npx",
        "api_key_env": "OPENAI_API_KEY",
        "login_hint": "run `codex login` (ChatGPT subscription)",
        "login_marker": "~/.codex/auth.json",
        "edit_mode": "workspace-write",
        "readonly_mode": "read-only",
        "env": {"NO_BROWSER": "1"},
        "tip": None,
    },
    "gemini": {
        "command": ["gemini", "--experimental-acp"],  # Gemini CLI speaks ACP natively
        "needs": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "login_hint": "run `gemini` once and log in with Google",
        "login_marker": "~/.gemini/oauth_creds.json",
        "edit_mode": None,
        "readonly_mode": None,
        "env": {},
        "tip": None,
    },
}

# Gifts: extra MCP servers a resident brings to every ACP session (Resident.gifts holds the ids).
# Each is a stdio MCP server started by the agent; `npx -y` fetches it on first use.
#   command    how to start it (the first word must be on PATH)
#   needs_env  an environment variable / setting the server can't run without ({NAME} in command/env is filled in)
#   gh_token   put the user's `gh auth token` in this environment variable
GIFTS = {
    "browser": {
        "label": "Browser",
        "command": ["npx", "-y", "@playwright/mcp@latest"],
        "hint": "a web browser the resident can drive (Playwright MCP)",
    },
    "github": {
        "label": "GitHub",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "gh_token": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "needs": "gh",
        "hint": "issues, PRs and repos through your `gh` login",
    },
    "docs": {
        "label": "Docs",
        "command": ["npx", "-y", "@upstash/context7-mcp"],
        "hint": "up-to-date library documentation (Context7)",
    },
    "database": {
        "label": "Database",
        "command": ["npx", "-y", "@bytebase/dbhub", "--dsn", "{DATABASE_URL}"],
        "needs_env": "DATABASE_URL",
        "hint": "query the database at DATABASE_URL (save it in settings)",
    },
    "terminal": {
        "label": "Terminal",
        "command": ["npx", "-y", "mcp-server-commands"],
        "hint": "an extra shell tool (the ACP agents have their own too)",
    },
    # Animation and video work (the island's own game lives in game/, a Godot 4 project).
    "godot": {
        "label": "Godot",
        "command": ["npx", "-y", "godot-mcp-runtime"],
        "needs_env": "GODOT_PATH",
        "hint": "run, script and screenshot a Godot 4 project (set GODOT_PATH, the Godot executable, in settings)",
    },
    "video": {
        "label": "Video",
        "command": ["npx", "-y", "ffmpeg-mcp"],
        "needs": "ffmpeg",
        "hint": "convert, trim and thumbnail media with ffmpeg",
    },
}

# ACP tool kind -> house key (Resident.keys: "yes" = allow by itself, "ask" = ✋ bubble, "no" = reject).
KEY_FOR_KIND = {
    "read": "read",
    "search": "read",
    "think": "read",
    "edit": "edit",
    "delete": "edit",
    "move": "edit",
    "execute": "execute",
    "fetch": "fetch",
}
REPORT_EVERY = 1.0  # seconds between `activity` events, so the game isn't flooded

# Our names for an agent's permission options (agents name them "Yes", "Allow", ...).
OPTION_LABELS = {
    "allow_once": "Just this once",
    "allow_always": "Always",
    "reject_once": "Not now",
    "reject_always": "Never",
}
# Fields where agents put their own reason for a tool call (Claude: description, Codex: reason).
REASON_FIELDS = ("description", "justification", "reason")
# What a command does, by its first word(s); checked in order, first match wins.
COMMAND_WORDS = [
    (re.compile(r"^(pytest|npm (run )?test|make (check|test)|uv run pytest|go test|cargo test)\b"), "run the tests"),
    (re.compile(r"^(npm|pnpm|yarn|pip3?|uv|cargo|brew) (install|add|i)\b"), "install packages"),
    (re.compile(r"^(python3?|node|ruby|perl|bash|sh|uv run python)\b"), "run a script"),
    (re.compile(r"^git (commit|push|merge|rebase|reset|checkout|switch)\b"), "change the git history"),
    (re.compile(r"^git\b"), "look at the git history"),
    (re.compile(r"^gh\b"), "talk to GitHub"),
    (re.compile(r"^(curl|wget)\b"), "download something from the internet"),
    (re.compile(r"^(rm|rmdir)\b"), "delete files"),
    (re.compile(r"^(mv|cp|mkdir|touch|chmod)\b"), "move or create files"),
    (re.compile(r"^(sed -i|tee|patch)\b"), "change files"),
    (re.compile(r"^(ls|cat|head|tail|grep|rg|find|sed|wc|tree)\b"), "look through files"),
]
FILE_PATTERN = re.compile(
    r"[\w.-]*(?:/[\w.-]+)*\.(?:py|gd|tscn|js|jsx|ts|tsx|json|toml|ya?ml|md|txt|html|css|sh|go|rs|rb|java|c|h|cpp|lock|cfg|ini)\b"
)
# Wrappers in front of the real command: `bash -lc "..."`, `cd x &&`, `A=1`.
COMMAND_PREFIX = re.compile(r"^(\s*((ba|z)?sh -l?c|cd \S+ *(&&|;)|\w+=\S+)\s*['\"]?)+")


def available_brains(
    openrouter_api_key: str | None = None, saved_keys: dict[str, str] | None = None
) -> dict[str, dict]:
    """Which brains can run on this machine. Best effort: `logged_in` is None when we can't tell.

    `saved_keys`: API keys saved in Settings, by environment variable (prefs.secrets_env()).
    """
    saved_keys = saved_keys or {}
    result: dict[str, dict] = {"mock": {"ok": True, "hint": "free fake work"}}
    for brain, spec in BRAINS.items():
        installed = shutil.which(spec["needs"]) is not None
        marker = spec["login_marker"]
        logged_in = Path(marker).expanduser().exists() if marker else None
        api_key = bool(os.getenv(spec["api_key_env"]) or saved_keys.get(spec["api_key_env"]))
        result[brain] = {
            "ok": installed and (logged_in is not False or api_key),
            "installed": installed,
            "logged_in": logged_in,
            "api_key": api_key,
            "hint": spec["login_hint"] if installed else f"install the `{spec['needs']}` CLI",
        }
    result["openrouter"] = {
        "ok": bool(openrouter_api_key),
        "hint": "any OpenRouter model, no CLI needed"
        if openrouter_api_key
        else "save an OpenRouter API key in Settings",
    }
    return result


def available_gifts() -> dict[str, dict]:
    """Which gifts can be handed over on this machine (the resident creator greys out the rest)."""
    result = {}
    for gift, spec in GIFTS.items():
        missing = _gift_missing(spec)
        result[gift] = {"ok": missing is None, "label": spec["label"], "hint": missing or spec["hint"]}
    return result


def _gift_missing(spec: dict) -> str | None:
    """Why a gift can't be used right now, or None when it can."""
    for tool in (spec["command"][0], spec.get("needs")):
        if tool and shutil.which(tool) is None:
            return f"install `{tool}` first"
    if spec.get("needs_env") and not prefs.extra_env(spec["needs_env"]):
        return f"set {spec['needs_env']} in settings first"
    return None


def gift_servers(gifts: list[str]) -> tuple[list[McpServerStdio], list[str]]:
    """MCP servers for a resident's gifts, plus a note for each gift we had to skip. Sync (may run `gh`)."""
    servers, skipped = [], []
    for gift in gifts:
        spec = GIFTS.get(gift)
        if spec is None:
            skipped.append(f"Unknown gift {gift!r}")
            continue
        missing = _gift_missing(spec)
        if missing:
            skipped.append(f"Left the {spec['label']} gift at home: {missing}")
            continue
        fill = {spec["needs_env"]: prefs.extra_env(spec["needs_env"])} if spec.get("needs_env") else {}
        command = [word.format(**fill) for word in spec["command"]]
        env = []
        if spec.get("gh_token"):
            token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
            if not token:
                skipped.append(f"Left the {spec['label']} gift at home: `gh auth login` first")
                continue
            env.append(EnvVariable(name=spec["gh_token"], value=token))
        servers.append(McpServerStdio(name=gift, command=shutil.which(command[0]), args=command[1:], env=env))
    return servers, skipped


def tool_detail(update: ToolCallStart | ToolCallProgress) -> str:
    """The technical side of a tool call, for the thinking dropdown's details: the diffs
    it makes, else its command, else its input as JSON, else the files it touches."""
    diffs = []
    for item in update.content or []:
        if isinstance(item, FileEditToolCallContent):
            old, new = (item.old_text or "").splitlines(), (item.new_text or "").splitlines()
            diffs.append("\n".join(difflib.unified_diff(old, new, item.path, item.path, lineterm="")))
    if any(diffs):
        detail = "\n".join(d for d in diffs if d)
    elif isinstance(update.raw_input, dict) and isinstance(update.raw_input.get("command"), str):
        detail = "$ " + update.raw_input["command"]
    elif update.raw_input:
        detail = json.dumps(update.raw_input, indent=1, default=str)
    else:
        detail = "\n".join(str(where.path) for where in update.locations or [])
    return detail[:THINKING_DETAIL_MAX]


class MiiClient(Client):
    """Our side of the ACP connection for one step: streams progress, answers permission
    requests through bubbles, and serves file reads/writes inside the worktree only."""

    def __init__(self, job: AgentJob, workdir: Path, brain: str = ""):
        self.brain = brain  # for the usage pills (usage.py)
        self.cost_seen = 0.0  # the session's running cost we've already counted
        self.start(job, workdir)

    def start(self, job: AgentJob, workdir: Path) -> None:
        """Serve this step's job (a kept agent's client is handed each new step of its resident)."""
        self.job = job
        self.workdir = workdir
        self.text: list[str] = []  # the agent's reply, chunk by chunk
        every = ThinkingStream.CHAT_EVERY if job.chat is not None else ThinkingStream.EVERY
        self.thinking = ThinkingStream(job.think, every)  # thoughts + reply + tool calls, for the game
        self.pending: set[asyncio.Task] = set()  # bubbles we're waiting on (parallel subagents can ask at once)
        self._tools: dict[str, dict] = {}  # tool call id -> what the game was last told about it
        self._last_report = 0.0
        self._last_text = ""

    # --- progress ----------------------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                self.text.append(update.content.text)
                await self.thinking.add("reply", update.content.text)
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                await self.thinking.add("thought", update.content.text)
        elif isinstance(update, (ToolCallStart, ToolCallProgress)):
            await self._tool_line(update)
            if update.title:
                await self._report(update.title)
        elif isinstance(update, UsageUpdate):
            if update.size > 0 and self.job.notebook:
                await self.job.notebook(update.used / update.size)  # the resident's notebook fills up
            await self._count_cost(update)

    async def _count_cost(self, update: UsageUpdate) -> None:
        """The agent's running session cost went up: add the difference to today's usage."""
        cost = update.cost
        if not self.brain or cost is None or cost.currency != "USD" or cost.amount <= self.cost_seen:
            return
        await usage.record(self.brain, cost=cost.amount - self.cost_seen)
        self.cost_seen = cost.amount

    async def _tool_line(self, update: ToolCallStart | ToolCallProgress) -> None:
        """A tool call for the thinking dropdown: one line, sent again (same `call`) when
        its title or details fill in. Status-only progress updates send nothing."""
        before = self._tools.get(update.tool_call_id, {"title": "", "tool": "", "detail": ""})
        kind = getattr(update.kind, "value", update.kind)
        now = {
            "title": update.title or before["title"],
            "tool": str(kind or before["tool"]),
            "detail": tool_detail(update) or before["detail"],
        }
        if not now["title"] or now == before:
            return
        self._tools[update.tool_call_id] = now
        await self.thinking.tool(now["title"], tool=now["tool"], call=update.tool_call_id, detail=now["detail"])

    async def _report(self, text: str) -> None:
        now = time.monotonic()
        if text == self._last_text or now - self._last_report < REPORT_EVERY:
            return
        self._last_report, self._last_text = now, text
        await self.job.report(text[:120])

    # --- permissions -------------------------------------------------------------

    async def request_permission(
        self, session_id: str, tool_call: ToolCallUpdate, options: list[PermissionOption], **kwargs: Any
    ) -> RequestPermissionResponse:
        def pick(kind: str) -> str | None:
            return next((o.option_id for o in options if o.kind == kind), None)

        key_name = KEY_FOR_KIND.get(tool_call.kind or "", "other")
        key = permission_for(self.job, key_name, tool_call.title or "")
        if self.job.read_only and key_name == "edit":
            key = "no"  # planning and reviewing never change files, whatever the house key says
        if key == "yes" and pick("allow_once"):
            return _allow(pick("allow_once"))
        if key == "no" and (pick("reject_once") or pick("reject_always")):
            await self._report(f"Refused ({tool_call.title or tool_call.kind}): house key says no")
            return _allow(pick("reject_once") or pick("reject_always"))

        # Our names for the options as buttons, but decide by option_id (names can repeat).
        labels: dict[str, str] = {}
        for o in options:
            name = OPTION_LABELS.get(getattr(o.kind, "value", o.kind), o.name)
            label = name if name not in labels else f"{name} [{o.option_id}]"
            labels[label] = o.option_id
        text = ask_reason(tool_call)
        waiting = asyncio.create_task(self.job.ask("permission", text, list(labels), _detail(tool_call)))
        self.pending.add(waiting)
        try:
            answer = await waiting
        finally:
            self.pending.discard(waiting)
        chosen = labels.get(answer or "") or pick("reject_once") or pick("reject_always")
        return _allow(chosen) if chosen else RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # --- files (worktree only) ---------------------------------------------------

    def _inside(self, path: str) -> Path:
        try:
            return inside(self.workdir, path)
        except SandboxError as e:
            raise RequestError(-32602, str(e))

    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **kwargs: Any
    ) -> ReadTextFileResponse:
        full = self._inside(path)
        try:
            content = full.read_text(errors="replace")
        except OSError as e:
            raise RequestError(-32602, f"cannot read {path}: {e.strerror}")
        if line is not None or limit is not None:
            lines = content.splitlines(keepends=True)
            start = max((line or 1) - 1, 0)
            end = start + limit if limit is not None else None
            content = "".join(lines[start:end])
        return ReadTextFileResponse(content=content)

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        full = self._inside(path)
        if self.job.read_only:
            raise RequestError(-32602, "this step is read-only")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return WriteTextFileResponse()

    # --- everything else: not supported (the agent runs commands itself) ---------

    async def ext_method(self, method: str, params: dict) -> dict:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict) -> None:
        pass


def _detail(tool_call: ToolCallUpdate) -> str | None:
    """What exactly the agent wants to do (the command or file), for the permission popup."""
    raw = tool_call.raw_input if isinstance(tool_call.raw_input, dict) else {}
    for field in ("command", "file_path", "path", "url"):
        value = raw.get(field)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if value:
            return str(value)[:500]
    return None


def ask_reason(tool_call: ToolCallUpdate) -> str:
    """Why the agent wants this key, in a short plain sentence: its own reason when it
    gives one, else its title when that isn't just the command, else a guess from the command."""
    raw = tool_call.raw_input if isinstance(tool_call.raw_input, dict) else {}
    for field in REASON_FIELDS:
        if isinstance(raw.get(field), str) and raw[field].strip():
            return _sentence(raw[field])
    command = _detail(tool_call) if (tool_call.kind or "") == "execute" else None
    title = (tool_call.title or "").strip()
    if title and "`" not in title and len(title) <= 80 and (not command or command not in title):
        return _sentence(title)
    if command:
        return _sentence(describe_command(command))
    return "May I do this?"


def describe_command(command: str) -> str:
    """A guess at what a shell command does, e.g. "run a script, touching backend/x.py"."""
    words = COMMAND_PREFIX.sub("", command.strip())
    what = next((text for pattern, text in COMMAND_WORDS if pattern.search(words)), None)
    if what is None:
        what = f"run `{words.split()[0]}`" if words.split() else "run a command"
    files = list(dict.fromkeys(m for m in FILE_PATTERN.findall(command) if not m.startswith(".")))
    if files and what not in ("install packages", "run the tests"):
        shown = ", ".join(files[:2]) + (f" and {len(files) - 2} more" if len(files) > 2 else "")
        what += f", touching {shown}"
    return what


def _sentence(text: str) -> str:
    text = " ".join(text.split())[:160]
    return text[:1].upper() + text[1:]


def _allow(option_id: str) -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id))


@asynccontextmanager
async def spawn_agent(client: Client, command: list[str], *, cwd: Path, env: dict[str, str]):
    """Start an ACP agent process and connect to it over stdio. Yields the connection.

    Like the SDK's `spawn_agent_process`, but the agent gets its own process group, so
    when the step ends (or times out) the whole tree goes: `npx` -> the adapter -> the
    CLI. Killing only `npx` would leave the CLI holding the stdout pipe for minutes.
    """
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    # The full path: on Windows `npx` is `npx.cmd`, which CreateProcess won't find by its bare name.
    program = command[0]
    if not os.path.dirname(program):  # a bare name like `npx` (tests pass a full path)
        program = shutil.which(program, path=env.get("PATH")) or program
    proc = await asyncio.create_subprocess_exec(
        program, *command[1:], cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, **group
    )
    conn = connect_to_agent(client, proc.stdin, proc.stdout)
    try:
        yield conn
    finally:
        with suppress(Exception):
            await conn.close()  # closes the agent's stdin: a healthy agent exits on its own
        await _end_process_tree(proc)


async def _end_process_tree(proc: asyncio.subprocess.Process, grace: float = 2.0) -> None:
    """Wait briefly for the agent to exit, then terminate, then kill: the whole process group."""
    # Windows has no SIGKILL (proc.kill() is used there anyway); fall back to SIGTERM so this loads.
    for sig in (None, signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        if sig is not None:
            with suppress(ProcessLookupError, PermissionError):
                if os.name == "nt":
                    await _taskkill_tree(proc.pid)  # proc.kill() would only end `npx`, not the CLI under it
                else:
                    os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
            return
        except TimeoutError:
            continue
    log.warning("agent process %s did not exit", proc.pid)


async def _taskkill_tree(pid: int) -> None:
    """Windows: end a process and everything it started (there are no process groups to signal)."""
    killer = await asyncio.create_subprocess_exec(
        "taskkill", "/PID", str(pid), "/T", "/F", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    await killer.wait()


# ACP has no "ask the user" request, so agents ask by ending their turn with a
# QUESTION line. The runner shows it as a ❓ bubble and prompts again with the answer.
QUESTION_RULE = (
    "If you need the island owner (the human) to decide something you can't work out from the code, "
    "stop and end your reply with one line `QUESTION: <your question>` instead of a VERDICT line. "
    "You'll get their answer and can carry on. Only ask when it really matters."
)
QUESTION_PATTERN = re.compile(r"^\s*QUESTION:\s*(.+?)\s*$", re.MULTILINE)
MAX_QUESTIONS = 3  # per step; after that the agent has to decide


def find_question(text: str) -> str | None:
    """The agent's last QUESTION line, unless it also gave a verdict (then it decided)."""
    if VERDICT_PATTERN.search(text):
        return None
    questions = QUESTION_PATTERN.findall(text)
    return questions[-1][:300] if questions else None


def _is_key(name: str) -> bool:
    return name.endswith(("_API_KEY", "_TOKEN", "_AUTH_TOKEN"))


def pick_model(offered: list[str], wanted: str) -> str | None:
    """The offered model the resident asked for: an exact match, else the one
    whose id contains it ("haiku" -> "claude-haiku-4-5"), else None."""
    if wanted in offered:
        return wanted
    close = [v for v in offered if wanted.strip().lower() in str(v).lower()]
    return close[0] if len(close) == 1 else None


def agent_env(spec: dict) -> dict[str, str]:
    """The agent's environment: the user's (PATH, HOME, keychain) plus the vendor keys saved in
    Settings, minus our own keys (prefs.PRIVATE_ENV) and minus blank keys (a set-but-empty
    ANTHROPIC_API_KEY makes some CLIs skip the subscription login)."""
    env = {k: v for k, v in os.environ.items() if k not in prefs.PRIVATE_ENV and not (_is_key(k) and not v.strip())}
    return prefs.secrets_env() | env | spec["env"]


def offered_models(config_options: list | None) -> tuple[list[dict], str | None]:
    """The models a session offers (its config option of category "model"): [{id, name,
    description}] and the current one. Grouped options are flattened."""
    for option in config_options or []:
        if getattr(option, "category", None) != "model":
            continue
        models = []
        for item in getattr(option, "options", []):
            for choice in getattr(item, "options", None) or [item]:  # a group holds options
                models.append(
                    {"id": choice.value, "name": choice.name or choice.value, "description": choice.description}
                )
        return models, getattr(option, "current_value", None)
    return [], None


class BrainError(Exception):
    """An agent couldn't be asked (not installed, not logged in, too slow). Shown to the user."""


class _ListingClient(Client):
    """Our side of the connection while we only ask which models an agent offers. We never
    send a prompt, so the agent has no reason to call us: refuse anything it asks."""

    async def request_permission(self, session_id: str, tool_call: Any, options: list, **kwargs: Any) -> Any:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        return None

    async def read_text_file(self, session_id: str, path: str, **kwargs: Any) -> Any:
        raise RequestError.invalid_request("no files while listing models")

    async def write_text_file(self, session_id: str, path: str, content: str, **kwargs: Any) -> Any:
        raise RequestError.invalid_request("no files while listing models")


def _scratch_worktree(root: Path) -> Path:
    """An empty git repo with one empty commit and a worktree of it, under `root`: somewhere
    the sandbox lets an agent start (check_workdir) that holds none of the user's files."""
    repo = root / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=mii", "-c", "user.email=mii@localhost"]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "scratch"], check=True, capture_output=True)
    sandbox = Sandbox(repo, root / "worktrees")
    workdir = sandbox.create_worktree("models")
    sandbox.check_workdir(workdir)
    return workdir


MODELS_TIMEOUT = 90.0  # seconds: the first `npx -y` download can be slow
MODELS_CACHE_SECONDS = 600.0
_models_cache: dict[str, tuple[float, dict]] = {}  # brain -> (when, {"models", "current"})


async def list_brain_models(brain: str, command: list[str] | None = None, refresh: bool = False) -> dict:
    """The models an ACP brain offers on this machine: {"models": [{id, name, description}],
    "current": its default}. Starts the agent in a scratch worktree, opens a session (no
    prompt, so nothing is spent), reads the session's model option and closes it. Cached for
    10 minutes per brain. Raises BrainError."""
    cached = _models_cache.get(brain)
    if cached and not refresh and command is None and time.monotonic() - cached[0] < MODELS_CACHE_SECONDS:
        return cached[1]
    spec = BRAINS[brain]
    try:
        with tempfile.TemporaryDirectory(prefix="mii-models-") as tmp:
            workdir = await asyncio.to_thread(_scratch_worktree, Path(tmp))
            async with asyncio.timeout(MODELS_TIMEOUT):
                async with spawn_agent(
                    _ListingClient(), command or spec["command"], cwd=workdir, env=agent_env(spec)
                ) as conn:
                    await conn.initialize(
                        protocol_version=PROTOCOL_VERSION,
                        client_capabilities=ClientCapabilities(
                            fs=FileSystemCapabilities(read_text_file=False, write_text_file=False), terminal=False
                        ),
                        client_info=Implementation(name="mii-nagement", title="Mii-nagement", version="0.2.0"),
                    )
                    session = await conn.new_session(cwd=str(workdir), mcp_servers=[])
                    models, current = offered_models(session.config_options)
    except RequestError as e:
        if e.code == AUTH_REQUIRED:
            raise BrainError(f"{brain} isn't logged in: {spec['login_hint']}")
        raise BrainError(f"{brain} refused: {e}")
    except TimeoutError:
        raise BrainError(f"{brain} took longer than {MODELS_TIMEOUT:.0f}s to start")
    except (OSError, ConnectionError, subprocess.CalledProcessError, SandboxError) as e:
        raise BrainError(f"couldn't start {brain}: {e}")
    result = {"models": models, "current": current}
    if command is None:
        _models_cache[brain] = (time.monotonic(), result)
    return result


# The first prompt of a kept session already held the personality and the diary; later visits say this instead.
RESUMED = (
    "You are {name} again, back at the {building} for this task (visit {attempt}). This is the same "
    "session as before: you already know the repository and what you did, so only re-read files that changed."
)


@dataclass
class LiveAgent:
    """One resident's agent process and session, kept between their visits on one task:
    each Mii is one agent, so a retry skips the start-up, the repo re-read and the context rebuild."""

    stack: AsyncExitStack  # owns the process (spawn_agent): closing it ends the whole process tree
    conn: Any
    client: MiiClient
    session: Any  # the session/new response
    workdir: Path
    model: str | None
    gifts: tuple[str, ...]
    read_only: bool
    prompted: bool = False  # False until the first prompt (an agent started early by prepare() has had none)

    def fits(self, job: AgentJob) -> bool:
        """Can this job use the kept session? Not if the worktree, model or gifts changed."""
        return (self.workdir, self.model, self.gifts) == (job.workdir, job.resident.model, _gifts(job))

    async def close(self) -> None:
        with suppress(Exception):
            await self.stack.aclose()


def _gifts(job: AgentJob) -> tuple[str, ...]:
    return tuple(job.resident.gifts or [])


class AcpRunner:
    """One brain's runner. Each resident gets one agent process per task: started on their first
    visit, prompted again on later visits (same session), closed by end_task() when the task is
    over, or right away when a step times out, is cancelled or the agent breaks."""

    def __init__(self, brain: str, command: list[str] | None = None):
        self.brain = brain
        self.spec = BRAINS[brain]
        self.command = command or self.spec["command"]  # tests pass a fake agent here
        self.live: dict[tuple[str, str], LiveAgent] = {}  # (task id, resident id) -> their kept agent

    async def end_task(self, task_id: str) -> None:
        """The task is over: close every agent kept for it."""
        for key in [key for key in self.live if key[0] == task_id]:
            await self.live.pop(key).close()

    async def prepare(self, job: AgentJob) -> None:
        """Start the agent and open its session now, kept for the job's (task, resident), so the
        first run() skips the start-up. Failures are left for run() to report."""
        key = (job.task.id, job.resident.id)
        if key in self.live or require_worktree(job):
            return
        try:
            self.live[key] = await self._start(job, MiiClient(job, job.workdir, self.brain))
        except Exception as e:  # not logged in, no npx...: run() tries again and says why
            log.info("%s: warming up for %s failed: %s", self.brain, job.resident.id, e)

    async def run(self, job: AgentJob) -> StepResult:
        if problem := require_worktree(job):
            return StepResult("fail", problem)

        key = (job.task.id, job.resident.id)
        agent = self.live.pop(key, None)  # out of the table while it works: nothing else may use it
        if agent is not None and not agent.fits(job):
            await agent.close()
            agent = None
        resumed = agent is not None
        client = agent.client if agent else MiiClient(job, job.workdir, self.brain)
        client.start(job, job.workdir)
        keep = False
        try:
            if agent is None:
                agent = await self._start(job, client)
            else:
                await job.report("Picking up where I left off")
                if agent.read_only != job.read_only:
                    await self._set_mode(agent.conn, agent.session, job.read_only)
                    agent.read_only = job.read_only
            result, keep = await self._prompt(agent, job)
            return result
        except asyncio.CancelledError:
            # The engine's step timeout, or the task was cancelled. Ask the agent to stop; the
            # finally below ends the process.
            if agent is not None:
                with suppress(Exception):
                    await asyncio.wait_for(agent.conn.cancel(session_id=agent.session.session_id), timeout=1)
            raise
        except RequestError as e:
            if e.code != AUTH_REQUIRED:
                log.warning("%s brain failed: %s", self.brain, e)
                return StepResult("fail", f"The {self.brain} brain crashed: {e}")
            # Not logged in. Claude Code only finds out at the first prompt, the others at session/new.
            await job.ask("tired", f"My {self.brain} brain isn't logged in: {self.spec['login_hint']}.")
            return StepResult("fail", f"{self.brain} needs a login ({self.spec['login_hint']}).")
        except (OSError, ConnectionError) as e:
            if resumed:  # the kept agent died between visits: start a fresh one
                log.info("%s: kept agent for %s is gone (%s), starting a new one", self.brain, job.resident.id, e)
                await agent.close()
                agent = None
                return await self.run(job)
            log.warning("%s brain failed: %s", self.brain, e)
            return StepResult("fail", f"The {self.brain} brain crashed: {e}")
        finally:
            for waiting in client.pending:
                waiting.cancel()
            with suppress(Exception):
                await client.thinking.flush()  # the last few words
            if agent is not None:
                if keep:
                    self.live[key] = agent
                else:
                    await agent.close()

    async def _start(self, job: AgentJob, client: MiiClient) -> LiveAgent:
        """Start the agent in the worktree, open a session and configure it."""
        stack = AsyncExitStack()
        try:
            conn = await stack.enter_async_context(
                spawn_agent(client, self.command, cwd=job.workdir, env=agent_env(self.spec))
            )
            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True), terminal=False
                ),
                client_info=Implementation(name="mii-nagement", title="Mii-nagement", version="0.2.0"),
            )
            servers, skipped = await asyncio.to_thread(gift_servers, list(_gifts(job)))
            for note in skipped:
                await job.report(note)
            session = await conn.new_session(cwd=str(job.workdir), mcp_servers=servers)
            await self._set_mode(conn, session, job.read_only)
            await self._set_model(conn, session, job)
        except BaseException:
            await stack.aclose()  # ends the process, also on a timeout or cancel
            raise
        return LiveAgent(stack, conn, client, session, job.workdir, job.resident.model, _gifts(job), job.read_only)

    async def _prompt(self, agent: LiveAgent, job: AgentJob) -> tuple[StepResult, bool]:
        """Prompt the session (answering QUESTION lines with ❓ bubbles). Returns the result and
        whether the session is still fit to keep for this resident's next visit. A chat (talk.py)
        asks no questions, keeps its whole reply, and is never kept: its worktree goes right after."""
        conn, client, session_id = agent.conn, agent.client, agent.session.session_id
        chat = job.chat is not None
        if chat and agent.prompted:  # the session already has the role and the conversation so far
            prompt = chat_followup(job)
        elif agent.prompted:
            intro = RESUMED.format(name=job.resident.name, building=job.building.name, attempt=job.attempt)
            prompt = build_prompt(job, intro, diary=False)
        else:
            prompt = build_prompt(job, build_system_prompt(job.resident))
            if self.spec.get("tip"):
                prompt = f"{prompt}\n\n{self.spec['tip']}"
        agent.prompted = True
        if not chat:
            prompt = f"{prompt}\n\n{QUESTION_RULE}"
        for asked in range(MAX_QUESTIONS + 1):
            client.text.clear()  # each turn's reply on its own: the last one carries the verdict
            response = await conn.prompt(session_id=session_id, prompt=[TextContentBlock(type="text", text=prompt)])
            await usage.record(self.brain, turns=1, tokens=response.usage.total_tokens if response.usage else 0)
            if response.stop_reason != "end_turn":
                return StepResult("fail", f"{self.brain} stopped early ({response.stop_reason})."), False
            question = None if chat else find_question("".join(client.text))
            if question is None or asked == MAX_QUESTIONS:
                break
            answer = await job.ask("confused", question)
            prompt = (
                f"The island owner answered: {answer}"
                if answer
                else "Nobody answered in time. Use your best judgement and carry on."
            )
            prompt += "\n\nWhen you're done, end with one line: `VERDICT: PASS` or `VERDICT: FAIL`."
        return parse_verdict("".join(client.text), job.summary_chars), True  # a talk's session: talk.py ends it

    async def _set_mode(self, conn, session, read_only: bool) -> None:
        """Switch on the agent's edit / read-only mode, when it offers it."""
        wanted_mode = self.spec["readonly_mode"] if read_only else self.spec["edit_mode"]
        modes = session.modes.available_modes if session.modes else []
        if wanted_mode and any(m.id == wanted_mode for m in modes):
            await conn.set_session_mode(session_id=session.session_id, mode_id=wanted_mode)

    async def _set_model(self, conn, session, job: AgentJob) -> None:
        """Pick the resident's model, when the agent offers it."""
        model = job.resident.model
        if not model:
            return
        for option in session.config_options or []:
            if getattr(option, "category", None) != "model":
                continue
            values = [m["id"] for m in offered_models([option])[0]]  # flattens grouped options
            chosen = pick_model(values, model)
            if chosen:
                await conn.set_config_option(config_id=option.id, session_id=session.session_id, value=chosen)
            else:
                await job.report(
                    f"Model {model} isn't offered by {self.brain} ({', '.join(values)}); using its default"
                )
            return
