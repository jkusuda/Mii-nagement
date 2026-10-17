"""OpenRouterRunner: any OpenRouter model as a resident, with no CLI installed.

One OPENROUTER_API_KEY, `resident.model` = an OpenRouter model id such as
"anthropic/claude-sonnet-4.5" or "qwen/qwen3-coder". This is a small agent loop
over OpenRouter's OpenAI-compatible chat API with five tools. Files are only
read or written inside the task's worktree; shell commands are gated by a ✋
bubble, like the ACP brains' own commands.
"""

import asyncio
import json
from pathlib import Path

import httpx

from mii.agents import AgentJob, StepResult, build_prompt, parse_verdict, permission_for
from mii.personality import build_system_prompt
from mii.sandbox import SandboxError, is_inside

API_URL = "https://openrouter.ai/api/v1"
MAX_TURNS = 40  # round trips to the model per step
MAX_OUTPUT = 4000  # characters of tool output we send back
COMMAND_TIMEOUT = 120.0

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files in the worktree matching a glob (default: everything).",
     "parameters": {"type": "object", "properties": {"glob": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file from the worktree.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file in the worktree.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the worktree (the user is asked first).",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "ask_user", "description": "Ask the user a question when something is unclear.",
     "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]


async def list_models(api_key: str) -> list[dict]:
    """The OpenRouter catalogue, trimmed to what the resident creator needs."""
    async with httpx.AsyncClient(base_url=API_URL, timeout=20, headers=_headers(api_key)) as http:
        response = await http.get("/models")
        response.raise_for_status()
    return [
        {"id": m["id"], "name": m.get("name", m["id"]), "context_length": m.get("context_length")}
        for m in response.json().get("data", [])
    ]


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "X-Title": "Mii-nagement"}


class OpenRouterRunner:
    def __init__(self, api_key: str | None, default_model: str, http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.default_model = default_model
        self.http = http  # tests pass a client with a MockTransport

    async def run(self, job: AgentJob) -> StepResult:
        if not self.api_key:
            return StepResult("fail", "The openrouter brain needs OPENROUTER_API_KEY in .env.")
        if job.sandbox is None or job.workdir is None:
            return StepResult("fail", "The openrouter brain needs a worktree (MOCK_AGENTS=0 and an island repository).")
        try:
            job.sandbox.check_workdir(job.workdir)
        except SandboxError as e:
            return StepResult("fail", str(e))

        model = job.resident.model or self.default_model
        messages = [
            {"role": "system", "content": build_system_prompt(job.resident)},
            {"role": "user", "content": build_prompt(job, "")},
        ]
        http = self.http or httpx.AsyncClient(base_url=API_URL, timeout=120)
        try:
            for _ in range(MAX_TURNS):
                reply = await self._chat(http, model, messages, job)
                if isinstance(reply, StepResult):
                    return reply
                messages.append(reply)
                calls = reply.get("tool_calls") or []
                if not calls:
                    return parse_verdict(reply.get("content") or "")
                for call in calls:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    await job.report(_describe(name, args))
                    output = await self._tool(name, args, job)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": output[:MAX_OUTPUT]})
            return StepResult("fail", f"{job.resident.name} used all {MAX_TURNS} turns without finishing.")
        finally:
            if self.http is None:
                await http.aclose()

    async def _chat(self, http: httpx.AsyncClient, model: str, messages: list[dict], job: AgentJob) -> dict | StepResult:
        """One round trip. Returns the assistant message, or a StepResult when the API says no."""
        try:
            response = await http.post("/chat/completions", headers=_headers(self.api_key),
                                       json={"model": model, "messages": messages, "tools": TOOLS})
        except httpx.HTTPError as e:
            return StepResult("fail", f"OpenRouter is unreachable: {e}")
        if response.status_code in (401, 402, 429):
            detail = _api_error(response)
            await job.ask("tired", f"OpenRouter won't let me work ({response.status_code}): {detail}")
            return StepResult("fail", f"OpenRouter refused ({response.status_code}): {detail}")
        if response.status_code >= 400:
            return StepResult("fail", f"OpenRouter error {response.status_code}: {_api_error(response)}")
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError):
            return StepResult("fail", "OpenRouter sent a reply without a message.")
        return {k: v for k, v in message.items() if k in ("role", "content", "tool_calls")}

    # --- tools ---------------------------------------------------------------------

    async def _tool(self, name: str, args: dict, job: AgentJob) -> str:
        workdir: Path = job.workdir
        try:
            if name == "list_files":
                pattern = args.get("glob") or "**/*"
                paths = [p for p in workdir.glob(pattern) if is_inside(p, workdir) and ".git" not in p.parts]
                return "\n".join(str(p.relative_to(workdir)) for p in sorted(paths)[:500]) or "(no files)"
            if name == "read_file":
                return self._inside(workdir, args.get("path", "")).read_text(errors="replace")
            if name == "write_file":
                if job.read_only:
                    return "error: this step is read-only"
                path = self._inside(workdir, args.get("path", ""))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args.get("content", ""))
                return f"wrote {path.relative_to(workdir)}"
            if name == "run_command":
                command = args.get("command", "")
                key = permission_for(job, "execute", command)  # house keys + house rules
                if key == "no":
                    return "denied: this resident's house key says no to running commands"
                if key == "ask" and await job.ask("permission", f"May I run `{command}`?", ["Allow", "Deny"]) != "Allow":
                    return "denied: the user did not allow this command"
                return await _run(command, workdir)
            if name == "ask_user":
                answer = await job.ask("confused", args.get("question", "?"))
                return answer or "(nobody answered; use your best judgement)"
            return f"error: unknown tool {name}"
        except (SandboxError, OSError) as e:
            return f"error: {e}"

    @staticmethod
    def _inside(workdir: Path, path: str) -> Path:
        full = Path(path) if Path(path).is_absolute() else workdir / path
        if not is_inside(full, workdir):
            raise SandboxError(f"{path} is outside the worktree")
        return full


def _describe(name: str, args: dict) -> str:
    target = args.get("path") or args.get("command") or args.get("glob") or args.get("question") or ""
    verb = {"list_files": "Listing", "read_file": "Reading", "write_file": "Writing", "run_command": "Running", "ask_user": "Asking"}
    return f"{verb.get(name, name)} {target}"[:120]


def _api_error(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message") or response.text[:200])
    except ValueError:
        return response.text[:200]


async def _run(command: str, cwd: Path) -> str:
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
    except TimeoutError:
        proc.kill()
        return "error: command timed out"
    output = raw.decode(errors="replace")
    return f"exit code {proc.returncode}\n{output[-MAX_OUTPUT:]}"
