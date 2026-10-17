import pytest

from mii.agents import AgentJob, BrainRunner, StepResult, make_runner
from mii.agents.acp import BRAINS, AcpRunner, available_brains
from mii.agents.mock import MockRunner
from mii.models import Building, Resident, Task


async def no_ask(kind, text, options=None):
    return None


async def no_report(text):
    pass


def job_for(brain: str) -> AgentJob:
    return AgentJob(
        resident=Resident(id="r", island_id="sunny", name="R", job="coder", brain=brain),
        task=Task(id="t", island_id="sunny", title="T"),
        building=Building(id="lab", island_id="sunny", type="lab", name="Lab", x=0, y=0),
        instructions="", ask=no_ask, report=no_report,
    )


class AlwaysPass:
    async def run(self, job) -> StepResult:
        return StepResult("pass", job.resident.brain)


def test_brains_table_has_the_three_vendors():
    assert set(BRAINS) == {"claude-code", "codex", "gemini"}
    assert available_brains()["mock"] is True


def test_mock_mode_puts_everyone_on_the_mock_brain():
    assert isinstance(make_runner(mock=True), MockRunner)


async def test_brain_runner_picks_by_resident_brain():
    router = BrainRunner({"codex": AlwaysPass()})
    assert (await router.run(job_for("codex"))).summary == "codex"
    result = await router.run(job_for("clippy"))
    assert result.outcome == "fail" and "clippy" in result.summary


async def test_acp_runner_is_a_placeholder_for_now():
    result = await AcpRunner("claude-code", sandbox=None).run(job_for("claude-code"))
    assert result.outcome == "fail"


@pytest.mark.skip(reason="TODO(mvp): drive AcpRunner against a tiny fake ACP agent script")
async def test_acp_runner_against_fake_agent():
    pass
