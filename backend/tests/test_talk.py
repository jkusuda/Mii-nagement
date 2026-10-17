"""Talking to a resident at a building (talk.py, the `talk` message)."""

import asyncio
import random
from types import SimpleNamespace

from conftest import FakeClient, ScriptedRunner, db_test, make_job, make_workflow

from mii import talk
from mii.agents import StepResult, build_prompt, parse_verdict
from mii.agents.mock import MockRunner
from mii.consumers import handle_message
from mii.models import Island

pytestmark = db_test


def mock_runtime(runner=None):
    workflow, _ = make_workflow(runner or MockRunner(rng=random.Random(1)))
    return SimpleNamespace(workflow=workflow, bus=workflow.bus, bubbles=workflow.bubbles, sandboxes=None)


async def ask_once(runtime, message: dict) -> list[dict]:
    client = FakeClient()
    await talk.talk(runtime, "sunny", message, client.send_json)
    return client.sent


async def test_mock_talk_replies_in_character(island):
    sent = await ask_once(
        mock_runtime(),
        {"type": "talk", "id": "k_1", "resident": "carlos", "building": "workshop", "message": "Where is the login?"},
    )
    assert [e["type"] for e in sent] == ["talk_thinking", "talk_reply"]
    reply = sent[-1]
    assert reply["id"] == "k_1" and reply["agent"] == "carlos"
    assert reply["text"].startswith("(mock)")


async def test_talk_prompt_has_role_and_history_but_no_step_rules(island):
    runner = ScriptedRunner()
    history = [
        {"from": "you", "text": "Hi!"},
        {"from": "resident", "text": "Hello there."},
        {"from": "x", "text": "DROPPED"},
    ]
    msg = {"id": "k_2", "resident": "sydney", "building": "courthouse", "message": "Is my diff ok?", "history": history}
    sent = await ask_once(mock_runtime(runner), msg)
    assert sent[-1]["type"] == "talk_reply"
    [job] = runner.jobs
    assert job.read_only and job.chat == "Is my diff ok?" and job.building.id == "courthouse"
    prompt = build_prompt(job, "SYSTEM")
    assert "reviewed" in prompt  # the Courthouse's role
    assert "Owner: Hi!" in prompt and "You: Hello there." in prompt and "DROPPED" not in prompt
    assert "Owner: Is my diff ok?" in prompt
    assert "VERDICT" not in prompt and "SAY TO" not in prompt


async def test_talk_defaults_to_the_home_building(island):
    runner = ScriptedRunner()
    await ask_once(mock_runtime(runner), {"id": "k_3", "resident": "jun", "message": "Hey"})
    assert runner.jobs[0].building.id == "mailbox"


async def test_talk_errors(island):
    runtime = mock_runtime()
    sent = await ask_once(runtime, {"id": "k_4", "resident": "nobody", "message": "Hi"})
    assert sent == [talk.talk_error("k_4", "nobody", "no resident 'nobody' on this island")]
    assert (await ask_once(runtime, {"id": "bad id!", "resident": "jun", "message": "Hi"}))[0]["type"] == "talk_error"
    assert (await ask_once(runtime, {"id": "k_5", "resident": "jun", "message": "  "}))[0]["type"] == "talk_error"
    sent = await ask_once(runtime, {"id": "k_6", "resident": "jun", "building": "castle", "message": "Hi"})
    assert sent[0]["type"] == "talk_error"


async def test_runner_refusal_becomes_talk_error(island):
    class Refuses:
        async def run(self, job):
            return StepResult("fail", "The claude-code brain needs a login.")

    sent = await ask_once(mock_runtime(Refuses()), {"id": "k_7", "resident": "jun", "message": "Hi"})
    assert sent == [talk.talk_error("k_7", "jun", "The claude-code brain needs a login.")]


async def test_one_turn_at_a_time_per_resident(island):
    runtime = mock_runtime(ScriptedRunner(delay=0.2))
    first, second = FakeClient(), FakeClient()
    msg = {"id": "k_8", "resident": "jun", "message": "Hi"}
    await asyncio.gather(
        talk.talk(runtime, "sunny", msg, first.send_json),
        talk.talk(runtime, "sunny", {**msg, "id": "k_9"}, second.send_json),
    )
    assert first.sent[-1]["type"] == "talk_reply"
    assert second.sent[-1]["type"] == "talk_error"


async def test_websocket_talk_answers_the_sender_only(island):
    runtime = mock_runtime(ScriptedRunner())
    watcher = FakeClient()
    runtime.bus.add_client(watcher)
    sender = FakeClient()
    msg = {"type": "talk", "id": "k_10", "resident": "jun", "message": "Hi"}
    assert await handle_message(runtime, "sunny", msg, sender.send_json) is None
    for _ in range(50):
        if sender.sent:
            break
        await asyncio.sleep(0.01)
    assert sender.sent[-1]["type"] == "talk_reply"
    assert watcher.sent == []  # not broadcast, not stored
    assert (await handle_message(runtime, "sunny", msg))["type"] == "error"  # no sender to answer


async def test_real_mode_talk_uses_a_throwaway_worktree(island, sandboxes):
    from mii.models import Resident

    await Resident.objects.filter(pk="carlos").aupdate(brain="claude-code")
    seen = {}

    class Peeks:
        async def run(self, job):
            seen["workdir"] = job.workdir
            seen["exists"] = job.workdir.exists()
            return parse_verdict("The login lives in hello.py.", job.summary_chars)

    runtime = mock_runtime(Peeks())
    runtime.sandboxes = sandboxes
    sent = await ask_once(runtime, {"id": "k_11", "resident": "carlos", "message": "Where's the login?"})
    assert sent[-1] == talk.talk_reply("k_11", "carlos", "The login lives in hello.py.")
    assert seen["exists"] and not seen["workdir"].exists()  # cut for the talk, removed after
    island_row = await Island.objects.aget(pk="sunny")
    branches = sandboxes.for_island(island_row)._git(sandboxes.for_island(island_row).repo_path, "branch")
    assert "talk-" not in branches


def test_chat_reply_keeps_long_answers():
    job = make_job()
    job.chat = "Tell me everything"
    long = "word " * 300
    assert parse_verdict(long, job.summary_chars).summary == long.strip()
    assert len(parse_verdict(long, make_job().summary_chars).summary) <= 401
