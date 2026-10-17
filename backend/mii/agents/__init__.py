"""Agent runners: run one resident on one step of one task.

A resident's `brain` decides which runner does the work:
- "mock"                          -> MockRunner (free, fake work; the default)
- "claude-code" / "codex" / "gemini" -> AcpRunner (a real coding agent over ACP)

Every runner has the same method, `async run(job) -> StepResult`, so the
engine never needs to know which brain it's talking to.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from mii.models import Building, Resident, Task


@dataclass
class StepResult:
    outcome: str  # "pass" | "fail"
    summary: str = ""


# ask(kind, text, options) shows a bubble and waits. Returns your answer, or None on timeout.
AskFn = Callable[..., Awaitable[str | None]]
# report(text) sends an `activity` event: what the resident is doing right now.
ReportFn = Callable[[str], Awaitable[None]]


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


def make_runner(mock: bool, sandbox=None) -> AgentRunner:
    """MOCK_AGENTS=1 puts every resident on the mock brain, whatever they're set to."""
    from mii.agents.acp import BRAINS, AcpRunner
    from mii.agents.mock import MockRunner

    if mock:
        return MockRunner()
    runners: dict[str, AgentRunner] = {"mock": MockRunner()}
    for brain in BRAINS:
        runners[brain] = AcpRunner(brain, sandbox)
    return BrainRunner(runners)
