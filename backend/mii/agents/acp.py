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
import logging
import os
import re
import shutil
import signal
import subprocess
import time
from contextlib import asynccontextmanager, suppress
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

from mii import prefs
from mii.agents import VERDICT_PATTERN, AgentJob, StepResult, ThinkingStream, build_prompt, parse_verdict, permission_for
from mii.personality import build_system_prompt
from mii.sandbox import SandboxError, is_inside

log = logging.getLogger(__name__)

AUTH_REQUIRED = -32000  # ACP error code: the agent wants a login first

# brain -> how to start its ACP agent and what makes it tick. Adding another ACP agent = one more entry.
#   command        the agent process (speaks ACP on stdio)
#   needs          a CLI that must be on PATH
#   api_key_env    the API key it accepts instead of a subscription login
#   login_marker   a file that exists once the CLI is logged in (None = can't tell; macOS keychains)
#   edit_mode / readonly_mode   the agent's own session modes we switch on (None = leave the default).
#                  Claude Code's read-only steps also run in `acceptEdits`: MiiClient refuses edit
#                  permissions and file writes there instead. Its `plan` mode doesn't work for us
#                  (refusing "Approve plan" ends the turn as `cancelled` and the plan is lost) and in
#                  `default` mode the first shell command never came back (the step timed out).
#   env            extra environment for the process
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
    },
}

# Gifts: extra MCP servers a resident brings to every ACP session (Resident.gifts holds the ids).
# Each is a stdio MCP server started by the agent; `npx -y` fetches it on first use.
#   command    how to start it (the first word must be on PATH)
#   needs_env  an environment variable / setting the server can't run without ({NAME} in command/env is filled in)
#   gh_token   put the user's `gh auth token` in this environment variable
GIFTS = {
    "browser": {
        "label": "Browser", "command": ["npx", "-y", "@playwright/mcp@latest"],
        "hint": "a web browser the resident can drive (Playwright MCP)",
    },
    "github": {
        "label": "GitHub", "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "gh_token": "GITHUB_PERSONAL_ACCESS_TOKEN", "needs": "gh",
        "hint": "issues, PRs and repos through your `gh` login",
    },
    "docs": {
        "label": "Docs", "command": ["npx", "-y", "@upstash/context7-mcp"],
        "hint": "up-to-date library documentation (Context7)",
    },
    "database": {
        "label": "Database", "command": ["npx", "-y", "@bytebase/dbhub", "--dsn", "{DATABASE_URL}"],
        "needs_env": "DATABASE_URL",
        "hint": "query the database at DATABASE_URL (save it in settings)",
    },
    "terminal": {
        "label": "Terminal", "command": ["npx", "-y", "mcp-server-commands"],
        "hint": "an extra shell tool (the ACP agents have their own too)",
    },
}

# ACP tool kind -> house key (Resident.keys: "yes" = allow by itself, "ask" = ✋ bubble, "no" = reject).
KEY_FOR_KIND = {
    "read": "read", "search": "read", "think": "read",
    "edit": "edit", "delete": "edit", "move": "edit",
    "execute": "execute",
    "fetch": "fetch",
}
REPORT_EVERY = 1.0  # seconds between `activity` events, so the game isn't flooded


def available_brains(openrouter_api_key: str | None = None, saved_keys: dict[str, str] | None = None) -> dict[str, dict]:
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
        "hint": "any OpenRouter model, no CLI needed" if openrouter_api_key else "save an OpenRouter API key in Settings",
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


class MiiClient(Client):
    """Our side of the ACP connection for one step: streams progress, answers permission
    requests through bubbles, and serves file reads/writes inside the worktree only."""

    def __init__(self, job: AgentJob, workdir: Path):
        self.job = job
        self.workdir = workdir
        self.text: list[str] = []  # the agent's reply, chunk by chunk
        self.thinking = ThinkingStream(job.think)  # thoughts + reply + tool calls, for the game
        self.pending: asyncio.Task | None = None  # a bubble we're waiting on
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
        elif isinstance(update, (ToolCallStart, ToolCallProgress)) and update.title:
            if isinstance(update, ToolCallStart):
                await self.thinking.add("tool", update.title)
            await self._report(update.title)
        elif isinstance(update, UsageUpdate) and update.size > 0 and self.job.notebook:
            await self.job.notebook(update.used / update.size)  # the resident's notebook fills up

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

        # Show the agent's own option names as buttons, but decide by option_id (names can repeat).
        labels: dict[str, str] = {}
        for o in options:
            label = o.name if o.name not in labels else f"{o.name} [{o.option_id}]"
            labels[label] = o.option_id
        text = tool_call.title or "May I do this?"
        self.pending = asyncio.create_task(self.job.ask("permission", text, list(labels), _detail(tool_call)))
        try:
            answer = await self.pending
        finally:
            self.pending = None
        chosen = labels.get(answer or "") or pick("reject_once") or pick("reject_always")
        return _allow(chosen) if chosen else RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # --- files (worktree only) ---------------------------------------------------

    def _inside(self, path: str) -> Path:
        full = Path(path) if os.path.isabs(path) else self.workdir / path
        if not is_inside(full, self.workdir):
            raise RequestError(-32602, f"{path} is outside the worktree")
        return full

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


def _allow(option_id: str) -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id))


@asynccontextmanager
async def spawn_agent(client: Client, command: list[str], *, cwd: Path, env: dict[str, str]):
    """Start an ACP agent process and connect to it over stdio. Yields the connection.

    Like the SDK's `spawn_agent_process`, but the agent gets its own process group, so
    when the step ends (or times out) the whole tree goes: `npx` -> the adapter -> the
    CLI. Killing only `npx` leaves a stuck CLI holding the stdout pipe, and the step
    hangs for as long as it takes that CLI to give up (12 minutes, when we checked).
    """
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, **group
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
                    proc.kill()
                else:
                    os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
            return
        except TimeoutError:
            continue
    log.warning("agent process %s did not exit", proc.pid)


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


class AcpRunner:
    """One brain's runner. A fresh agent process per step, so nothing leaks between steps."""

    def __init__(self, brain: str, command: list[str] | None = None):
        self.brain = brain
        self.spec = BRAINS[brain]
        self.command = command or self.spec["command"]  # tests pass a fake agent here

    async def run(self, job: AgentJob) -> StepResult:
        if job.sandbox is None or job.workdir is None:
            return StepResult("fail", f"The {self.brain} brain needs a worktree (MOCK_AGENTS=0 and an island repository).")
        try:
            job.sandbox.check_workdir(job.workdir)
        except SandboxError as e:
            return StepResult("fail", str(e))

        # The SDK passes only a minimal environment by default; the agent needs the user's
        # full one (PATH, HOME, keychain access). Vendor API keys saved in the app's keychain
        # are added; the OpenRouter key stays with us.
        # An empty key (e.g. `ANTHROPIC_API_KEY=` in .env) is left out: some CLIs treat a
        # set-but-blank key as "use this key" and stop using your subscription login.
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY" and not (_is_key(k) and not v.strip())}
        env = prefs.secrets_env() | env | self.spec["env"]
        client = MiiClient(job, job.workdir)
        session_id: str | None = None
        conn = None
        try:
            async with spawn_agent(client, self.command, cwd=job.workdir, env=env) as conn:
                await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs=FileSystemCapabilities(read_text_file=True, write_text_file=True), terminal=False
                    ),
                    client_info=Implementation(name="mii-nagement", title="Mii-nagement", version="0.2.0"),
                )
                servers, skipped = await asyncio.to_thread(gift_servers, list(job.resident.gifts or []))
                for note in skipped:
                    await job.report(note)
                session = await conn.new_session(cwd=str(job.workdir), mcp_servers=servers)
                session_id = session.session_id
                await self._configure(conn, session, job)

                prompt = f"{build_prompt(job, build_system_prompt(job.resident))}\n\n{QUESTION_RULE}"
                for asked in range(MAX_QUESTIONS + 1):
                    client.text.clear()  # each turn's reply on its own: the last one carries the verdict
                    response = await conn.prompt(session_id=session_id, prompt=[TextContentBlock(type="text", text=prompt)])
                    if response.stop_reason != "end_turn":
                        return StepResult("fail", f"{self.brain} stopped early ({response.stop_reason}).")
                    question = find_question("".join(client.text))
                    if question is None or asked == MAX_QUESTIONS:
                        break
                    answer = await job.ask("confused", question)
                    prompt = (f"The island owner answered: {answer}" if answer
                              else "Nobody answered in time. Use your best judgement and carry on.")
                    prompt += "\n\nWhen you're done, end with one line: `VERDICT: PASS` or `VERDICT: FAIL`."
                return parse_verdict("".join(client.text))
        except asyncio.CancelledError:
            # The engine's step timeout. Ask the agent to stop before the process is killed.
            if conn is not None and session_id is not None:
                try:
                    await asyncio.wait_for(conn.cancel(session_id=session_id), timeout=1)
                except Exception:
                    pass
            raise
        except RequestError as e:
            if e.code != AUTH_REQUIRED:
                log.warning("%s brain failed: %s", self.brain, e)
                return StepResult("fail", f"The {self.brain} brain crashed: {e}")
            # Not logged in. Claude Code only finds out at the first prompt, the others at session/new.
            await job.ask("tired", f"My {self.brain} brain isn't logged in: {self.spec['login_hint']}.")
            return StepResult("fail", f"{self.brain} needs a login ({self.spec['login_hint']}).")
        except (OSError, ConnectionError) as e:
            log.warning("%s brain failed: %s", self.brain, e)
            return StepResult("fail", f"The {self.brain} brain crashed: {e}")
        finally:
            if client.pending is not None:
                client.pending.cancel()
            with suppress(Exception):
                await client.thinking.flush()  # the last few words

    async def _configure(self, conn, session, job: AgentJob) -> None:
        """Switch on the agent's edit / read-only mode and the resident's model, when offered."""
        wanted_mode = self.spec["readonly_mode"] if job.read_only else self.spec["edit_mode"]
        modes = session.modes.available_modes if session.modes else []
        if wanted_mode and any(m.id == wanted_mode for m in modes):
            await conn.set_session_mode(session_id=session.session_id, mode_id=wanted_mode)

        model = job.resident.model
        if not model:
            return
        for option in session.config_options or []:
            if getattr(option, "category", None) != "model":
                continue
            values = [o.value for o in getattr(option, "options", [])]
            chosen = pick_model(values, model)
            if chosen:
                await conn.set_config_option(config_id=option.id, session_id=session.session_id, value=chosen)
            else:
                await job.report(f"Model {model} isn't offered by {self.brain} ({', '.join(values)}); using its default")
            return
