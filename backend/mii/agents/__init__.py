"""Agent runners: run one resident on one step of one task.

A resident's `brain` decides which runner does the work:
- "mock"                             -> MockRunner (free, fake work; the default)
- "claude-code" / "codex" / "gemini" -> AcpRunner (a real coding agent over ACP, on your subscription)
- "openrouter"                       -> OpenRouterRunner (any OpenRouter model, no CLI needed)

Every runner has the same method, `async run(job) -> StepResult`, so the
engine never needs to know which brain it's talking to.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from mii.models import Building, Resident, Task


@dataclass
class StepResult:
    outcome: str  # "pass" | "fail"
    summary: str = ""


# ask(kind, text, options, detail) shows a bubble and waits. `detail` is extra context the
# popup shows as-is, such as the exact command a permission bubble asks about.
# Returns your answer, or None on timeout.
AskFn = Callable[..., Awaitable[str | None]]
# report(text) sends an `activity` event: what the resident is doing right now.
ReportFn = Callable[[str], Awaitable[None]]
# notebook(fraction) says how full the agent's context window is (0.0-1.0).
NotebookFn = Callable[[float], Awaitable[None]]


@dataclass
class AgentJob:
    """Everything a runner needs for one step."""

    resident: Resident
    task: Task
    building: Building
    instructions: str  # what to do at this building (from the building handler)
    ask: AskFn
    report: ReportFn
    attempt: int = 1  # 2+ means the package came back here after a fail
    history: list[str] = field(default_factory=list)  # notes from earlier steps
    workdir: Path | None = None  # the task's worktree (None in mock mode)
    sandbox: object | None = None  # the island's Sandbox, for path checks (None in mock mode)
    read_only: bool = False  # True at buildings where the resident must not change files
    rules: dict = field(default_factory=dict)  # the island's house rules (see permission_for)
    notebook: NotebookFn | None = None  # how full the agent's context window is (ACP usage updates)


# House rules (Island.rules, True = ask me first) that tighten a resident's house keys.
INSTALL_PATTERN = re.compile(
    r"\b(npm|pnpm|yarn|bun)\s+(i|install|add)\b|\bpip3?\s+install\b|\buv\s+(add|pip|sync)\b|\bpoetry\s+add\b"
    r"|\bcargo\s+add\b|\bgo\s+get\b|\b(apt|apt-get|brew|gem|composer)\s+install\b",
    re.IGNORECASE,
)


def permission_for(job: "AgentJob", kind: str, title: str = "") -> str:
    """What to do with an agent's permission request: "yes" (allow), "ask" (a ✋ bubble) or "no".

    The resident's house key for the tool kind decides, except that the island's
    house rules can turn a "yes" into an "ask": `shell` for any command, and
    `install_packages` for commands that look like a package install.
    """
    keys = getattr(job.resident, "keys", None) or {}
    answer = keys.get(kind, "ask")
    if answer == "yes" and kind == "execute":
        if job.rules.get("shell") or (job.rules.get("install_packages") and INSTALL_PATTERN.search(title)):
            return "ask"
    return answer


VERDICT_PATTERN = re.compile(r"VERDICT:\s*(PASS|FAIL)", re.IGNORECASE)


def parse_verdict(text: str, summary_chars: int = 400) -> StepResult:
    """Read the agent's final text: the last `VERDICT: FAIL` fails, anything else passes."""
    verdicts = VERDICT_PATTERN.findall(text)
    outcome = "fail" if verdicts and verdicts[-1].upper() == "FAIL" else "pass"
    summary = VERDICT_PATTERN.sub("", text).strip()
    if len(summary) > summary_chars:
        summary = "…" + summary[-summary_chars:]
    return StepResult(outcome, summary)


def build_prompt(job: AgentJob, system_prompt: str) -> str:
    """Personality + instructions + notes from earlier steps, as one text block."""
    parts = [system_prompt, "", job.instructions]
    if job.history:
        parts += ["", "Notes from earlier steps on this task:"] + [f"- {note}" for note in job.history]
    if job.read_only:
        parts += ["", "Do not create or change any files at this step."]
    parts += ["", "End your reply with one line: `VERDICT: PASS` or `VERDICT: FAIL`."]
    return "\n".join(parts)


class AgentRunner(Protocol):
    async def run(self, job: AgentJob) -> StepResult: ...


class BrainRunner:
    """Sends each job to the runner for its resident's brain."""

    def __init__(self, runners: dict[str, AgentRunner]):
        self.runners = runners

    async def run(self, job: AgentJob) -> StepResult:
        runner = self.runners.get(job.resident.brain)
        if runner is None:
            return StepResult("fail", f"Unknown brain {job.resident.brain!r}.")
        return await runner.run(job)


def make_runner(mock: bool, *, openrouter_api_key: str | None = None,
                openrouter_model: str = "anthropic/claude-sonnet-4.5") -> AgentRunner:
    """MOCK_AGENTS=1 puts every resident on the mock brain, whatever they're set to."""
    from mii.agents.acp import BRAINS, AcpRunner
    from mii.agents.mock import MockRunner
    from mii.agents.openrouter import OpenRouterRunner

    if mock:
        return MockRunner()
    runners: dict[str, AgentRunner] = {"mock": MockRunner()}
    for brain in BRAINS:
        runners[brain] = AcpRunner(brain)
    runners["openrouter"] = OpenRouterRunner(openrouter_api_key, openrouter_model)
    return BrainRunner(runners)
