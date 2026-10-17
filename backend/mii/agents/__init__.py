"""Agent runners: run one resident on one step of one task.

A resident's `brain` decides which runner does the work:
- "mock"                             -> MockRunner (free, fake work; the default)
- "claude-code" / "codex" / "gemini" -> AcpRunner (a real coding agent over ACP, on your subscription)
- "openrouter"                       -> OpenRouterRunner (any OpenRouter model, no CLI needed)

Every runner has the same method, `async run(job) -> StepResult`, so the
engine never needs to know which brain it's talking to.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from mii.models import Building, Resident, Task


@dataclass
class StepResult:
    outcome: str  # "pass" | "fail"
    summary: str = ""
    explicit: bool = True  # False = the agent's reply had no VERDICT line, so the judge may decide
    flaky: bool = False  # the Lab's tests failed, then passed on their one re-run
    help_request: str | None = None  # a `HELP: ...` line: what the resident is stuck on
    says: list[tuple[str, str]] = field(default_factory=list)  # `SAY TO <Name>: ...` lines: (name, message)
    remember: list[str] = field(default_factory=list)  # `REMEMBER: ...` lines for the resident's diary


# ask(kind, text, options, detail) shows a bubble and waits. `detail` is extra context the
# popup shows as-is, such as the exact command a permission bubble asks about.
# Returns your answer, or None on timeout.
AskFn = Callable[..., Awaitable[str | None]]
# report(text) sends an `activity` event: what the resident is doing right now.
ReportFn = Callable[[str], Awaitable[None]]
# notebook(fraction) says how full the agent's context window is (0.0-1.0).
NotebookFn = Callable[[float], Awaitable[None]]
# think(kind, text) streams the resident's train of thought (`thinking` events):
# kind is "thought", "reply" or "tool". Runners send it through a ThinkingStream.
ThinkFn = Callable[[str, str], Awaitable[None]]


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
    struggled: bool = False  # a 💦 bubble was raised during this step (the judge reads it)
    messages: list[str] = field(default_factory=list)  # "Jun: ..." notes neighbours left for this resident on this task
    notes: str = ""  # the resident's diary (Resident.notes), what they chose to remember from earlier tasks
    neighbours: list[str] = field(default_factory=list)  # "Jun (planner)", so SAY TO has names to use
    think: ThinkFn | None = None  # the resident's train of thought, for the game's "thinking" dropdown


class ThinkingStream:
    """Batches an agent's streamed text into `thinking` events: one event per
    second or so (or per ~600 characters), not one per token. A change of kind
    ("thought" -> "reply") sends what's waiting first. Call flush() at the end."""

    EVERY = 1.0  # seconds
    MAX_CHARS = 600

    def __init__(self, think: ThinkFn | None):
        self.think = think
        self.kind = ""
        self.parts: list[str] = []
        self.size = 0
        self.last = time.monotonic()

    async def add(self, kind: str, text: str) -> None:
        if self.think is None or not text:
            return
        if kind != self.kind:
            await self.flush()
            self.kind = kind
        self.parts.append(text)
        self.size += len(text)
        if kind == "tool" or self.size >= self.MAX_CHARS or time.monotonic() - self.last >= self.EVERY:
            await self.flush()

    async def flush(self) -> None:
        if self.think is not None and self.parts:
            text, self.parts, self.size = "".join(self.parts), [], 0
            await self.think(self.kind, text)
        self.last = time.monotonic()


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
# Whole-line notes an agent can add to its reply (build_prompt tells it how).
HELP_PATTERN = re.compile(r"^[ \t]*HELP:[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
SAY_PATTERN = re.compile(r"^[ \t]*SAY TO ([^:\n]{1,40}?):[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
REMEMBER_PATTERN = re.compile(r"^[ \t]*REMEMBER:[ \t]*(.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)


def parse_verdict(text: str, summary_chars: int = 400) -> StepResult:
    """Read the agent's final text: the last `VERDICT: FAIL` fails, anything else passes.

    Also picks out the `HELP:`, `SAY TO <Name>:` and `REMEMBER:` lines (and drops them
    from the summary). `explicit` says whether there was a VERDICT line at all.
    """
    verdicts = VERDICT_PATTERN.findall(text)
    outcome = "fail" if verdicts and verdicts[-1].upper() == "FAIL" else "pass"
    helps = HELP_PATTERN.findall(text)
    says = [(name.strip(), message.strip()) for name, message in SAY_PATTERN.findall(text)]
    remember = [line.strip() for line in REMEMBER_PATTERN.findall(text)]
    summary = text
    for pattern in (HELP_PATTERN, SAY_PATTERN, REMEMBER_PATTERN, VERDICT_PATTERN):
        summary = pattern.sub("", summary)
    summary = summary.strip()
    if len(summary) > summary_chars:
        summary = "…" + summary[-summary_chars:]
    return StepResult(outcome, summary, explicit=bool(verdicts), help_request=helps[-1].strip() if helps else None,
                      says=says, remember=remember)


def build_prompt(job: AgentJob, system_prompt: str) -> str:
    """Personality + instructions + notes from earlier steps, as one text block."""
    parts = [system_prompt]
    if job.notes:
        parts += ["", "Things you remember from earlier tasks (your diary):"] + [f"- {line}" for line in job.notes.splitlines() if line.strip()]
    parts += ["", job.instructions]
    if job.history:
        parts += ["", "Notes from earlier steps on this task:"] + [f"- {note}" for note in job.history]
    if job.messages:
        parts += ["", "Messages from your neighbours:"] + [f"- {line}" for line in job.messages]
    if job.read_only:
        parts += ["", "Do not create or change any files at this step."]
    parts += ["", "End your reply with one line: `VERDICT: PASS` or `VERDICT: FAIL`."]
    neighbours = ", ".join(job.neighbours) if job.neighbours else "nobody else lives here yet"
    parts += [
        "You may also add single lines anywhere in your reply:",
        "- `HELP: <what you are stuck on>` if you fail and a neighbour coming over would help.",
        f"- `SAY TO <Name>: <message>` to leave a note a neighbour sees on their next step (your neighbours: {neighbours}).",
        "- `REMEMBER: <something>` to write a short line in your diary for future tasks.",
    ]
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
