"""OpenRouterRunner: any OpenRouter model as a resident, with no CLI installed.

One OPENROUTER_API_KEY, `resident.model` = an OpenRouter model id such as
"anthropic/claude-sonnet-4.5" or "qwen/qwen3-coder". This is a small agent loop
over OpenRouter's OpenAI-compatible chat API with five tools. Files are only
read or written inside the task's worktree; shell commands are gated by a ✋
bubble, like the ACP brains' own commands.
"""

import json
from pathlib import Path

import httpx

from mii import usage
from mii.agents import AgentJob, StepResult, build_prompt, parse_verdict, permission_for
from mii.personality import build_system_prompt
from mii.sandbox import SandboxError, inside, is_inside, require_worktree, run_shell
from mii.vocab import THINKING_DETAIL_MAX

API_URL = "https://openrouter.ai/api/v1"
MAX_TURNS = 40  # round trips to the model per step
MAX_OUTPUT = 4000  # characters of tool output we send back
COMMAND_TIMEOUT = 120.0

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the worktree matching a glob (default: everything).",
            "parameters": {"type": "object", "properties": {"glob": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the worktree.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file in the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the worktree (the user is asked first).",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a question when something is unclear.",
            "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
        },
    },
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
            return StepResult(
                "fail", "The openrouter brain needs an OpenRouter API key (Settings, or OPENROUTER_API_KEY)."
            )
        if problem := require_worktree(job):
            return StepResult("fail", problem)

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
                if job.think and reply.get("content"):
                    await job.think("reply", str(reply["content"]))
                calls = reply.get("tool_calls") or []
                if not calls:
                    return parse_verdict(reply.get("content") or "", job.summary_chars)
                for call in calls:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    await job.report(_describe(name, args))
                    if job.think:
                        await job.think(
                            "tool",
                            _describe(name, args),
                            tool=TOOL_SORTS.get(name, "other"),
                            call=str(call.get("id", "")),
                            detail=_detail(name, args),
                        )
                    output = await self._tool(name, args, job)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": output[:MAX_OUTPUT]})
            return StepResult("fail", f"{job.resident.name} used all {MAX_TURNS} turns without finishing.")
        finally:
            if self.http is None:
                await http.aclose()

    async def _chat(
        self, http: httpx.AsyncClient, model: str, messages: list[dict], job: AgentJob
    ) -> dict | StepResult:
        """One round trip. Returns the assistant message, or a StepResult when the API says no."""
        try:
            response = await http.post(
                "/chat/completions",
                headers=_headers(self.api_key),
                json={"model": model, "messages": messages, "tools": TOOLS},
            )
        except httpx.HTTPError as e:
            return StepResult("fail", f"OpenRouter is unreachable: {e}")
        if response.status_code in (401, 402, 429):
            detail = _api_error(response)
            await job.ask("tired", f"OpenRouter won't let me work ({response.status_code}): {detail}")
            return StepResult("fail", f"OpenRouter refused ({response.status_code}): {detail}")
        if response.status_code >= 400:
            return StepResult("fail", f"OpenRouter error {response.status_code}: {_api_error(response)}")
        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, ValueError):
            return StepResult("fail", "OpenRouter sent a reply without a message.")
        spent = data.get("usage") or {}  # OpenRouter reports tokens and, usually, the cost in credits (USD)
        await usage.record(
            "openrouter", turns=1, tokens=int(spent.get("total_tokens") or 0), cost=float(spent.get("cost") or 0)
        )
        return {k: v for k, v in message.items() if k in ("role", "content", "tool_calls")}

    # --- tools ---------------------------------------------------------------------

    async def _tool(self, name: str, args: dict, job: AgentJob) -> str:
        workdir: Path = job.workdir
        try:
            if name in TOOL_KEYS:
                if name == "write_file" and job.read_only:
                    return "error: this step is read-only"
                denied = await _check_key(job, name, args)
                if denied:
                    return denied
            if name == "list_files":
                pattern = args.get("glob") or "**/*"
                paths = [p for p in workdir.glob(pattern) if is_inside(p, workdir) and ".git" not in p.parts]
                return "\n".join(str(p.relative_to(workdir)) for p in sorted(paths)[:500]) or "(no files)"
            if name == "read_file":
                return inside(workdir, args.get("path", "")).read_text(errors="replace")
            if name == "write_file":
                path = inside(workdir, args.get("path", ""))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args.get("content", ""))
                return f"wrote {path.relative_to(workdir)}"
            if name == "run_command":
                code, output = await run_shell(args.get("command", ""), workdir, COMMAND_TIMEOUT, limit=MAX_OUTPUT)
                return f"exit code {code}\n{output}"
            if name == "ask_user":
                answer = await job.ask("confused", args.get("question", "?"))
                return answer or "(nobody answered; use your best judgement)"
            return f"error: unknown tool {name}"
        except (SandboxError, OSError) as e:
            return f"error: {e}"


# Our tools -> the house key that guards them (models.DEFAULT_KEYS), and how a ✋ bubble asks.
TOOL_KEYS = {
    "list_files": ("read", "May I look through the files?"),
    "read_file": ("read", "May I read `{path}`?"),
    "write_file": ("edit", "May I write `{path}`?"),
    "run_command": ("execute", "May I run `{command}`?"),
}


async def _check_key(job: AgentJob, name: str, args: dict) -> str | None:
    """Ask the resident's house key (and the house rules) about a tool call: None = go ahead,
    else why it was refused. An "ask" key shows a ✋ bubble."""
    key_name, question = TOOL_KEYS[name]
    target = str(args.get("command") or args.get("path") or args.get("glob") or "")
    key = permission_for(job, key_name, target)
    if key == "no":
        return f"denied: this resident's house key says no to {key_name}"
    if key == "ask":
        text = question.format(path=args.get("path", ""), command=args.get("command", ""))
        if await job.ask("permission", text, ["Allow", "Deny"], target or None) != "Allow":
            return "denied: the user did not allow this"
    return None


# Our tools -> the tool sorts of `thinking` events (vocab.TOOL_SORTS).
TOOL_SORTS = {"list_files": "search", "read_file": "read", "write_file": "edit", "run_command": "execute"}


def _detail(name: str, args: dict) -> str:
    """The technical details the game shows under "Show details": the command, else the input."""
    if name == "run_command":
        return "$ " + str(args.get("command", ""))
    return json.dumps(args, indent=1)[:THINKING_DETAIL_MAX]


def _describe(name: str, args: dict) -> str:
    target = args.get("path") or args.get("command") or args.get("glob") or args.get("question") or ""
    verb = {
        "list_files": "Listing",
        "read_file": "Reading",
        "write_file": "Writing",
        "run_command": "Running",
        "ask_user": "Asking",
    }
    return f"{verb.get(name, name)} {target}"[:120]


def _api_error(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message") or response.text[:200])
    except ValueError:
        return response.text[:200]
