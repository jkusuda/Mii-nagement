import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import RecordingAsk, RecordingReport, make_job

from mii.agents import BrainRunner, StepResult, make_runner, parse_verdict, permission_for
from mii.agents.acp import (
    BRAINS,
    GIFTS,
    AcpRunner,
    MiiClient,
    available_brains,
    available_gifts,
    describe_command,
    gift_servers,
)
from mii.agents.mock import MockRunner

FAKE_AGENT = Path(__file__).parent / "fake_acp_agent.py"


@pytest.fixture(autouse=True)
async def close_kept_agents(monkeypatch):
    """Runners keep each resident's agent until end_task(); end them after every test."""
    runners: list[AcpRunner] = []
    start = AcpRunner.__init__

    def init(self, *args, **kwargs):
        start(self, *args, **kwargs)
        runners.append(self)

    monkeypatch.setattr(AcpRunner, "__init__", init)
    yield
    for runner in runners:
        for task_id in {key[0] for key in runner.live}:
            await runner.end_task(task_id)


def _gone(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) can't probe a process on Windows: ask tasklist
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return f" {pid} " not in listing
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


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
        ("permission", "Run the tests", ["Just this once", "Not now"]),  # our labels, not the agent's
        ("permission", "Edit hello.py", ["Just this once", "Not now"]),
    ]
    assert "edit permission: allow" in result.summary
    assert ask.details[0] == "npm test"  # the exact command, for the permission popup


@pytest.mark.parametrize(
    "command, expected",
    [
        ("python3 - <<'E'\np='backend/mii/github.py'\nE", "run a script, touching backend/mii/github.py"),
        ('bash -lc "npm install zod"', "install packages"),
        ("cd backend && uv run pytest tests/test_engine.py -q", "run the tests"),
        ("rm -rf build dist/app.js", "delete files, touching dist/app.js"),
        ("git push origin main", "change the git history"),
        ("weirdtool --x", "run `weirdtool`"),
    ],
)
def test_describe_command_says_what_a_command_does(command, expected):
    assert describe_command(command) == expected


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


def test_the_master_key_turns_every_ask_into_yes_but_keeps_no():
    job = make_job("codex")
    job.resident.keys = {"read": "yes", "edit": "ask", "execute": "yes", "fetch": "no"}
    job.rules, job.master_key = {"shell": True}, True
    assert permission_for(job, "edit", "x") == "yes" and permission_for(job, "execute", "ls") == "yes"
    assert permission_for(job, "fetch", "x") == "no"


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
        await runner.end_task("t_test")  # the next key gets a fresh agent


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
    assert _gone(int((workdir / "agent.pid").read_text()))  # the agent process is gone


async def test_a_resident_keeps_one_agent_across_visits_until_the_task_ends(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    first = await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 20)
    assert first.outcome == "pass" and "Looking at the code" in first.summary
    again = make_job("claude-code", workdir=workdir, sandbox=sandbox, instructions="Fix what the Lab reported")
    again.attempt = 2
    report = again.report

    second = await asyncio.wait_for(runner.run(again), 20)

    assert second.outcome == "pass" and "(prompt 2)" in second.summary  # same session, second prompt
    assert len((workdir / "agent.starts").read_text().split()) == 1  # no second process
    assert "Picking up where I left off" in report.lines
    pid = int((workdir / "agent.pid").read_text())
    assert not _gone(pid) and list(runner.live) == [("t_test", "r")]
    await runner.end_task("t_test")
    for _ in range(50):  # the process tree ends within a moment
        if _gone(pid):
            break
        await asyncio.sleep(0.1)
    assert _gone(pid) and runner.live == {}


async def test_a_timeout_on_a_kept_agent_ends_it(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 20)
    pid = int((workdir / "agent.pid").read_text())
    stuck = make_job("claude-code", workdir=workdir, sandbox=sandbox, instructions="please hang")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runner.run(stuck), 2)
    assert _gone(pid) and runner.live == {}


@pytest.mark.skipif(os.name == "nt", reason="kills the agent with a POSIX signal")
async def test_a_kept_agent_that_died_is_replaced(sandbox):
    import signal

    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 20)
    os.kill(int((workdir / "agent.pid").read_text()), signal.SIGKILL)
    await asyncio.sleep(0.2)
    result = await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 20)
    assert result.outcome == "pass" and "Looking at the code" in result.summary  # a fresh session
    assert len((workdir / "agent.starts").read_text().split()) == 2


async def test_another_task_or_model_gets_its_own_agent(sandbox):
    workdir = sandbox.create_worktree("t_test")
    runner = AcpRunner("claude-code", command=[sys.executable, str(FAKE_AGENT)])
    await asyncio.wait_for(runner.run(make_job("claude-code", workdir=workdir, sandbox=sandbox)), 20)
    other = make_job("claude-code", workdir=workdir, sandbox=sandbox, model="claude-haiku-4-5")
    result = await asyncio.wait_for(runner.run(other), 20)
    assert "Looking at the code" in result.summary  # the model changed: a fresh session
    assert len((workdir / "agent.starts").read_text().split()) == 2


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
    tools: list[dict] = []

    async def think(kind: str, text: str, **tool_info: str) -> None:
        streamed.append((kind, text))
        if kind == "tool":
            tools.append({"text": text, **tool_info})

    job.think = think
    await asyncio.wait_for(runner.run(job), 20)
    assert ("thought", "Hmm, hello.py first.") in streamed
    assert ("tool", "Reading hello.py") in streamed
    # The read is sent, then updated (same call) once its diff arrives; the status-only update sends nothing.
    reads = [t for t in tools if t.get("call") == "tc1"]
    assert len(reads) == 2
    assert reads[0] == {"text": "Reading hello.py", "tool": "read", "call": "tc1"}
    assert "-a" in reads[1]["detail"] and "+b" in reads[1]["detail"]
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


async def test_list_brain_models_asks_the_agent_in_a_scratch_worktree():
    from mii.agents.acp import list_brain_models

    listed = await asyncio.wait_for(list_brain_models("claude-code", command=[sys.executable, str(FAKE_AGENT)]), 30)
    assert listed["current"] == "default"
    assert [m["id"] for m in listed["models"]] == ["default", "claude-haiku-4-5"]
    assert listed["models"][1] == {"id": "claude-haiku-4-5", "name": "Haiku", "description": "Fast"}


async def test_list_brain_models_reports_a_missing_login_or_cli():
    from mii.agents.acp import BrainError, list_brain_models

    with pytest.raises(BrainError, match="isn't logged in"):
        await list_brain_models("codex", command=[sys.executable, str(FAKE_AGENT), "--needs-login"])
    with pytest.raises(BrainError, match="couldn't start"):
        await list_brain_models("gemini", command=["/no/such/agent-cli"])


async def test_parallel_subagents_can_each_wait_on_a_permission_bubble(tmp_path):
    from acp.schema import PermissionOption, ToolCallUpdate

    asked = asyncio.Event()
    open_bubbles = []

    async def ask(kind, text, options=None, detail=None):
        open_bubbles.append(text)
        if len(open_bubbles) == 2:
            asked.set()
        await asyncio.sleep(10)  # nobody answers

    client = MiiClient(make_job("claude-code", workdir=tmp_path, ask=ask), tmp_path)
    options = [PermissionOption(option_id="no", name="No", kind="reject_once")]
    calls = [
        asyncio.create_task(
            client.request_permission("s", ToolCallUpdate(tool_call_id=f"t{n}", title="ls", kind="execute"), options)
        )
        for n in range(2)
    ]
    await asyncio.wait_for(asked.wait(), 2)
    assert len(client.pending) == 2  # both subagents' bubbles are open, neither replaced the other
    for waiting in list(client.pending):
        waiting.cancel()  # what the runner does when the step ends
    await asyncio.gather(*calls, return_exceptions=True)
    assert not client.pending


def test_claude_code_is_told_it_may_use_parallel_subagents():
    assert "subagents" in BRAINS["claude-code"]["tip"]
    assert BRAINS["codex"]["tip"] is None
