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
import shutil
import time
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, Client, RequestError, spawn_agent_process
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    FileSystemCapabilities,
    Implementation,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    WriteTextFileResponse,
)

from mii.agents import AgentJob, StepResult, build_prompt, parse_verdict
from mii.personality import build_system_prompt
from mii.sandbox import SandboxError, is_inside

log = logging.getLogger(__name__)

AUTH_REQUIRED = -32000  # ACP error code: the agent wants a login first

# brain -> how to start its ACP agent and what makes it tick. Adding another ACP agent = one more entry.
#   command        the agent process (speaks ACP on stdio)
#   needs          a CLI that must be on PATH
#   api_key_env    the API key it accepts instead of a subscription login
#   login_marker   a file that exists once the CLI is logged in (None = can't tell; macOS keychains)
#   edit_mode / readonly_mode   the agent's own session modes we switch on (None = leave the default)
#   env            extra environment for the process
BRAINS = {
    "claude-code": {
        "command": ["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
        "needs": "claude",
        "api_key_env": "ANTHROPIC_API_KEY",
        "login_hint": "run `claude` once and log in with your Claude subscription",
        "login_marker": None,
        "edit_mode": "acceptEdits",
        "readonly_mode": "plan",
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

# ACP tool kind -> house key (Resident.keys: "yes" = allow by itself, "ask" = ✋ bubble, "no" = reject).
KEY_FOR_KIND = {
    "read": "read", "search": "read", "think": "read",
    "edit": "edit", "delete": "edit", "move": "edit",
    "execute": "execute",
    "fetch": "fetch",
}
REPORT_EVERY = 1.0  # seconds between `activity` events, so the game isn't flooded


def available_brains(openrouter_api_key: str | None = None) -> dict[str, dict]:
    """Which brains can run on this machine. Best effort: `logged_in` is None when we can't tell."""
    result: dict[str, dict] = {"mock": {"ok": True, "hint": "free fake work"}}
    for brain, spec in BRAINS.items():
        installed = shutil.which(spec["needs"]) is not None
        marker = spec["login_marker"]
        logged_in = Path(marker).expanduser().exists() if marker else None
        api_key = bool(os.getenv(spec["api_key_env"]))
        result[brain] = {
            "ok": installed and (logged_in is not False or api_key),
            "installed": installed,
            "logged_in": logged_in,
            "api_key": api_key,
            "hint": spec["login_hint"] if installed else f"install the `{spec['needs']}` CLI",
        }
    result["openrouter"] = {
        "ok": bool(openrouter_api_key),
        "hint": "any OpenRouter model, no CLI needed" if openrouter_api_key else "set OPENROUTER_API_KEY in .env",
    }
    return result


class MiiClient(Client):
    """Our side of the ACP connection for one step: streams progress, answers permission
    requests through bubbles, and serves file reads/writes inside the worktree only."""

    def __init__(self, job: AgentJob, workdir: Path):
        self.job = job
        self.workdir = workdir
        self.text: list[str] = []  # the agent's reply, chunk by chunk
        self.pending: asyncio.Task | None = None  # a bubble we're waiting on
        self._last_report = 0.0
        self._last_text = ""

    # --- progress ----------------------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                self.text.append(update.content.text)
        elif isinstance(update, (ToolCallStart, ToolCallProgress)) and update.title:
            await self._report(update.title)

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

        if self.job.read_only and tool_call.kind == "switch_mode" and pick("reject_once"):
            return _allow(pick("reject_once"))  # Claude's "exit plan mode": stay read-only, no bubble
        keys = getattr(self.job.resident, "keys", None) or {}
        key = keys.get(KEY_FOR_KIND.get(tool_call.kind or "", "other"), "ask")
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
        self.pending = asyncio.create_task(self.job.ask("permission", text, list(labels)))
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


def _allow(option_id: str) -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id))


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
        # full one (PATH, HOME, keychain access, API keys). The OpenRouter key stays with us.
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"} | self.spec["env"]
        client = MiiClient(job, job.workdir)
        session_id: str | None = None
        conn = None
        try:
            async with spawn_agent_process(client, *self.command, cwd=job.workdir, env=env) as (conn, _proc):
                await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs=FileSystemCapabilities(read_text_file=True, write_text_file=True), terminal=False
                    ),
                    client_info=Implementation(name="mii-nagement", title="Mii-nagement", version="0.2.0"),
                )
                try:
                    session = await conn.new_session(cwd=str(job.workdir), mcp_servers=[])
                except RequestError as e:
                    if e.code == AUTH_REQUIRED:
                        await job.ask("tired", f"My {self.brain} brain isn't logged in: {self.spec['login_hint']}.")
                        return StepResult("fail", f"{self.brain} needs a login ({self.spec['login_hint']}).")
                    raise
                session_id = session.session_id
                await self._configure(conn, session, job)

                prompt = build_prompt(job, build_system_prompt(job.resident))
                response = await conn.prompt(session_id=session_id, prompt=[TextContentBlock(type="text", text=prompt)])
                if response.stop_reason != "end_turn":
                    return StepResult("fail", f"{self.brain} stopped early ({response.stop_reason}).")
                return parse_verdict("".join(client.text))
        except asyncio.CancelledError:
            # The engine's step timeout. Ask the agent to stop before the process is killed.
            if conn is not None and session_id is not None:
                try:
                    await asyncio.wait_for(conn.cancel(session_id=session_id), timeout=1)
                except Exception:
                    pass
            raise
        except (RequestError, OSError, ConnectionError) as e:
            log.warning("%s brain failed: %s", self.brain, e)
            return StepResult("fail", f"The {self.brain} brain crashed: {e}")
        finally:
            if client.pending is not None:
                client.pending.cancel()

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
            if model in values:
                await conn.set_config_option(config_id=option.id, session_id=session.session_id, value=model)
            else:
                await job.report(f"Model {model} isn't offered by {self.brain}; using its default")
            return
