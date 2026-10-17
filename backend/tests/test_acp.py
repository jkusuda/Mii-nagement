import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import RecordingAsk, RecordingReport, make_job

from mii.agents import BrainRunner, StepResult, make_runner, parse_verdict, permission_for
from mii.agents.acp import BRAINS, GIFTS, AcpRunner, available_brains, available_gifts, gift_servers
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


def test_brains_count_api_keys_saved_in_settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert available_brains(saved_keys={"OPENAI_API_KEY": "sk-1"})["codex"]["api_key"] is True


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


def test_parse_verdict_reads_help_say_and_remember_lines():
    text = (
        "Tried twice.\nHELP: the auth tests hang\nSAY TO Sydney: I touched config.py\n"
        "remember: tests are slow\nVERDICT: FAIL"
    )
    result = parse_verdict(text)
    assert result.outcome == "fail" and result.explicit is True
    assert result.help_request == "the auth tests hang"
    assert result.says == [("Sydney", "I touched config.py")] and result.remember == ["tests are slow"]
    assert result.summary == "Tried twice."  # the note lines never reach the package history
    assert parse_verdict("no verdict at all").explicit is False


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
    assert "gifts: none" in result.summary
    assert ask.asked == [
        ("permission", "Run `npm test`", ["Allow once", "Reject"]),
        ("permission", "Edit hello.py", ["Allow once", "Reject"]),
    ]
    assert "edit permission: allow" in result.summary
    assert ask.details[0] == "npm test"  # the exact command, for the permission popup


async def test_acp_runner_hands_over_gifts_and_fills_the_notebook(sandbox, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    notebook: list[float] = []

    async def fill(value: float) -> None:
        notebook.append(value)

    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    job = make_job("claude-code", workdir=workdir, sandbox=sandbox)
    job.resident.gifts = ["docs", "browser"]
    job.notebook = fill
    result = await asyncio.wait_for(runner.run(job), 20)
    assert result.outcome == "pass" and "gifts: docs,browser" in result.summary
    assert notebook == [0.25]  # the fake agent reported 250 of 1000 tokens used


def test_gifts_table_and_what_is_missing(monkeypatch):
    import shutil

    assert set(GIFTS) == {"browser", "github", "docs", "database", "terminal", "godot", "video"}
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert all(not g["ok"] and "install" in g["hint"] for g in available_gifts().values())
    servers, skipped = gift_servers(["docs", "jetpack"])
    assert servers == [] and len(skipped) == 2 and "Docs" in skipped[0]
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("mii.prefs.extra_env", lambda name: None)
    assert available_gifts()["docs"]["ok"] is True
    assert "DATABASE_URL" in available_gifts()["database"]["hint"]
    [docs], _ = gift_servers(["docs"])
    assert docs.name == "docs" and docs.command == "/usr/bin/npx" and docs.args[-1] == "@upstash/context7-mcp"


def test_house_rules_tighten_house_keys():
    job = make_job("codex")
    job.resident.keys = {"read": "yes", "edit": "yes", "execute": "yes", "fetch": "no"}
    assert permission_for(job, "execute", "npm test") == "yes"
    job.rules = {"install_packages": True}
    assert permission_for(job, "execute", "npm test") == "yes"
    assert permission_for(job, "execute", "npm install left-pad") == "ask"
    assert permission_for(job, "execute", "pip install requests") == "ask"
    job.rules = {"shell": True}
    assert permission_for(job, "execute", "ls") == "ask"
    assert permission_for(job, "edit", "x") == "yes" and permission_for(job, "fetch", "x") == "no"
    job.resident.keys = {"execute": "no"}
    assert permission_for(job, "execute", "ls") == "no"


async def test_house_keys_answer_permissions_without_a_bubble(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    for key, expected in (("yes", "selected"), ("no", "selected")):
        ask = RecordingAsk()
        job = make_job("claude-code", workdir=workdir, ask=ask, sandbox=sandbox)
        job.resident.keys = {"read": "yes", "edit": "yes", "execute": key, "fetch": "no"}
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
    assert "edit permission: reject" in result.summary  # refused without a bubble, whatever the house key says
    assert [text for _, text, _ in job.ask.asked if "Edit" in text] == []
    assert not (workdir / "hello.txt").exists()


async def test_a_timed_out_step_ends_the_agent_process(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT), "--hang"])
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 2)
    assert time.monotonic() - started < 12  # the step timeout, not the stuck agent, decides
    pid = int((workdir / "agent.pid").read_text())
    if os.name == "nt":  # os.kill(pid, 0) can't probe a process on Windows: ask tasklist
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        assert f" {pid} " not in listing  # the agent process is gone
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # the agent process is gone


async def test_acp_runner_reports_a_missing_login_as_a_tired_bubble(sandbox):
    workdir = sandbox.create_worktree("t_test")
    ask = RecordingAsk()
    runner = AcpRunner("codex", command=[sys.executable, str(FAKE_AGENT), "--needs-login"])

    result = await asyncio.wait_for(runner.run(make_job("codex", workdir=workdir, ask=ask, sandbox=sandbox)), 20)

    assert result.outcome == "fail" and "login" in result.summary
    assert ask.asked[0][0] == "tired"


async def test_login_needed_at_the_first_prompt_is_a_tired_bubble_too(sandbox):
    workdir = sandbox.create_worktree("t_test")
    ask = RecordingAsk()
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT), "--login-at-prompt"])
    result = await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, ask=ask, sandbox=sandbox)), 20)
    assert result.outcome == "fail" and "login" in result.summary
    assert ask.asked[0][0] == "tired" and "claude" in ask.asked[0][1]


async def test_blank_api_keys_are_not_passed_to_the_agent(sandbox, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # like `ANTHROPIC_API_KEY=` in .env
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    result = await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 20)
    assert result.outcome == "pass", result.summary


async def test_acp_resident_asks_a_question_and_carries_on_with_the_answer(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    ask = RecordingAsk("A new file, please")
    job = make_job("claude-code", workdir=workdir, instructions="please ask", ask=ask, sandbox=sandbox)
    result = await asyncio.wait_for(runner.run(job), 20)
    assert ask.asked == [("confused", "Should I change hello.py or a new file?", None)]
    assert result.outcome == "pass" and "A new file, please" in result.summary
    assert "QUESTION" not in result.summary  # only the last turn makes the summary


def test_find_question():
    from mii.agents.acp import find_question

    assert find_question("Hmm.\nQUESTION: Which branch?") == "Which branch?"
    assert find_question("QUESTION: one?\nVERDICT: PASS") is None  # it decided after all
    assert find_question("All done.\nVERDICT: FAIL") is None


def test_pick_model_takes_a_short_name():
    from mii.agents.acp import pick_model

    offered = ["default", "claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"]
    assert pick_model(offered, "claude-haiku-4-5") == "claude-haiku-4-5"
    assert pick_model(offered, "Haiku") == "claude-haiku-4-5"
    assert pick_model(offered, "claude") is None  # ambiguous: keep the default
    assert pick_model(offered, "gpt-5") is None


async def test_acp_streams_thoughts_replies_and_tools_as_thinking(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    job = make_job("claude-code", workdir=workdir, sandbox=sandbox)
    streamed: list[tuple[str, str]] = []

    async def think(kind: str, text: str) -> None:
        streamed.append((kind, text))

    job.think = think
    await asyncio.wait_for(runner.run(job), 20)
    assert ("thought", "Hmm, hello.py first.") in streamed
    assert ("tool", "Reading hello.py") in streamed
    assert "VERDICT: PASS" in "".join(text for kind, text in streamed if kind == "reply")


async def test_thinking_stream_batches_chunks():
    from mii.agents import ThinkingStream

    sent: list[tuple[str, str]] = []

    async def think(kind, text):
        sent.append((kind, text))

    stream = ThinkingStream(think)
    for word in ["The ", "fix ", "is ", "small."]:
        await stream.add("thought", word)
    assert sent == []  # still batching
    await stream.add("reply", "Done.")  # a new kind sends what was waiting
    await stream.flush()
    assert sent == [("thought", "The fix is small."), ("reply", "Done.")]
