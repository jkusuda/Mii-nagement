import asyncio
import sys
from pathlib import Path

from conftest import RecordingAsk, RecordingReport, make_job

from mii.agents import BrainRunner, StepResult, make_runner, parse_verdict
from mii.agents.acp import BRAINS, AcpRunner, available_brains
from mii.agents.mock import MockRunner

FAKE_AGENT = Path(__file__).parent / "fake_acp_agent.py"


class AlwaysPass:
    async def run(self, job) -> StepResult:
        return StepResult("pass", job.resident.brain)


def test_brains_table_has_the_three_vendors():
    assert set(BRAINS) == {"claude-code", "codex", "gemini"}
    brains = available_brains()
    assert brains["mock"]["ok"] is True
    assert brains["openrouter"]["ok"] is False
    assert available_brains("sk-or-x")["openrouter"]["ok"] is True
    assert {"ok", "installed", "logged_in", "api_key", "hint"} <= set(brains["codex"])


def test_mock_mode_puts_everyone_on_the_mock_brain():
    assert isinstance(make_runner(mock=True), MockRunner)


async def test_brain_runner_picks_by_resident_brain():
    router = BrainRunner({"codex": AlwaysPass()})
    assert (await router.run(make_job("codex"))).summary == "codex"
    result = await router.run(make_job("clippy"))
    assert result.outcome == "fail" and "clippy" in result.summary


def test_parse_verdict_uses_the_last_verdict_line():
    assert parse_verdict("Looks good.\nVERDICT: PASS").outcome == "pass"
    assert parse_verdict("Hmm.\nVERDICT: PASS\nActually no.\nverdict: fail").outcome == "fail"
    assert parse_verdict("no verdict at all").outcome == "pass"
    assert "VERDICT" not in parse_verdict("Done.\nVERDICT: PASS").summary


async def test_acp_runner_needs_a_worktree():
    result = await AcpRunner("claude-code").run(make_job("claude-code"))
    assert result.outcome == "fail" and "worktree" in result.summary


async def test_acp_runner_against_fake_agent(sandbox):
    workdir = sandbox.create_worktree("t_test")
    ask, report = RecordingAsk(), RecordingReport()
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    job = make_job("claude-code", workdir=workdir, ask=ask, report=report, sandbox=sandbox)

    result = await asyncio.wait_for(runner.run(job), 20)

    assert result.outcome == "pass", result.summary
    assert "outside read refused" in result.summary
    assert "permission outcome: selected" in result.summary
    assert (workdir / "hello.txt").read_text() == "hi\n"
    assert "Reading hello.py" in report.lines
    assert ask.asked == [("permission", "Run `npm test`", ["Allow once", "Reject"])]


async def test_house_keys_answer_permissions_without_a_bubble(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    for key, expected in (("yes", "selected"), ("no", "selected")):
        ask = RecordingAsk()
        job = make_job("claude-code", workdir=workdir, ask=ask, sandbox=sandbox)
        job.resident.keys = {"read": "yes", "edit": "ask", "execute": key, "fetch": "no"}
        result = await asyncio.wait_for(runner.run(job), 20)
        assert result.outcome == "pass" and f"permission outcome: {expected}" in result.summary
        assert ask.asked == []  # decided by the house key, no bubble


async def test_acp_runner_reads_a_fail_verdict_and_blocks_writes_when_read_only(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    job = make_job("claude-code", workdir=workdir, instructions="please fail", read_only=True, sandbox=sandbox)

    result = await asyncio.wait_for(runner.run(job), 20)

    assert result.outcome == "fail"
    assert "write refused" in result.summary
    assert not (workdir / "hello.txt").exists()


async def test_acp_runner_reports_a_missing_login_as_a_tired_bubble(sandbox):
    workdir = sandbox.create_worktree("t_test")
    ask = RecordingAsk()
    runner = AcpRunner("codex", command=[sys.executable, str(FAKE_AGENT), "--needs-login"])

    result = await asyncio.wait_for(runner.run(make_job("codex", workdir=workdir, ask=ask, sandbox=sandbox)), 20)

    assert result.outcome == "fail" and "login" in result.summary
    assert ask.asked[0][0] == "tired"
