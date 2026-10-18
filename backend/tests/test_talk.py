"""Talking to a resident at a building (talk.py, the `talk` message)."""

import asyncio
import random
from types import SimpleNamespace

import pytest
from conftest import FakeClient, ScriptedRunner, db_test, make_job, make_workflow
from mock_runner import MockRunner

from mii import events, talk
from mii.agents import StepResult, build_prompt, parse_verdict
from mii.consumers import handle_message
from mii.models import Event, Island, Resident, Step, Task, utcnow

pytestmark = db_test


@pytest.fixture
def at_work(island):
    """Everyone working inside their building (the test island puts each one at theirs): talkable."""
    Resident.objects.filter(island_id="sunny").update(state="working")
    return island


def mock_runtime(runner=None):
    workflow, _ = make_workflow(runner or MockRunner(rng=random.Random(1)))
    return SimpleNamespace(workflow=workflow, bus=workflow.bus, bubbles=workflow.bubbles, sandboxes=None)


async def ask_once(runtime, message: dict) -> list[dict]:
    client = FakeClient()
    await talk.talk(runtime, "sunny", message, client.send_json)
    return client.sent


async def test_mock_talk_replies_in_character(at_work):
    sent = await ask_once(
        mock_runtime(),
        {"type": "talk", "id": "k_1", "resident": "carlos", "building": "workshop", "message": "Where is the login?"},
    )
    types = [e["type"] for e in sent]
    assert types[0] == "talk_thinking" and types[-1] == "talk_reply"
    assert set(types[1:-1]) == {"talk_partial"}  # the reply streams in, a few words at a time
    reply = sent[-1]
    assert reply["id"] == "k_1" and reply["agent"] == "carlos"
    assert reply["text"].startswith("(mock)")
    assert "".join(e["text"] for e in sent if e["type"] == "talk_partial").strip() == reply["text"]


async def test_talk_prompt_has_role_and_history_but_no_step_rules(at_work):
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


async def test_talk_defaults_to_the_building_they_are_in(at_work):
    runner = ScriptedRunner()
    await ask_once(mock_runtime(runner), {"id": "k_3", "resident": "jun", "message": "Hey"})
    assert runner.jobs[0].building.id == "mailbox"


async def test_only_residents_working_inside_can_talk(island):
    runner = ScriptedRunner()
    runtime = mock_runtime(runner)
    sent = await ask_once(runtime, {"id": "k_12", "resident": "jun", "building": "mailbox", "message": "Hi"})
    assert sent == [talk.talk_error("k_12", "jun", "Jun isn't inside the Mailbox right now.")]  # idle: outside
    await Resident.objects.filter(pk="jun").aupdate(state="working")
    sent = await ask_once(runtime, {"id": "k_13", "resident": "jun", "building": "workshop", "message": "Hi"})
    assert sent[0]["type"] == "talk_error"  # working, but in another building
    sent = await ask_once(runtime, {"id": "k_14", "resident": "jun", "building": "mailbox", "message": "Hi"})
    assert sent[-1]["type"] == "talk_reply" and len(runner.jobs) == 1


async def test_talk_errors(at_work):
    runtime = mock_runtime()
    sent = await ask_once(runtime, {"id": "k_4", "resident": "nobody", "message": "Hi"})
    assert sent == [talk.talk_error("k_4", "nobody", "no resident 'nobody' on this island")]
    assert (await ask_once(runtime, {"id": "bad id!", "resident": "jun", "message": "Hi"}))[0]["type"] == "talk_error"
    assert (await ask_once(runtime, {"id": "k_5", "resident": "jun", "message": "  "}))[0]["type"] == "talk_error"
    sent = await ask_once(runtime, {"id": "k_6", "resident": "jun", "building": "castle", "message": "Hi"})
    assert sent[0]["type"] == "talk_error"


async def test_runner_refusal_becomes_talk_error(at_work):
    class Refuses:
        async def run(self, job):
            return StepResult("fail", "The claude-code brain needs a login.")

    sent = await ask_once(mock_runtime(Refuses()), {"id": "k_7", "resident": "jun", "message": "Hi"})
    assert sent == [talk.talk_error("k_7", "jun", "The claude-code brain needs a login.")]


async def test_one_turn_at_a_time_per_resident(at_work):
    runtime = mock_runtime(ScriptedRunner(delay=0.2))
    first, second = FakeClient(), FakeClient()
    msg = {"id": "k_8", "resident": "jun", "message": "Hi"}
    await asyncio.gather(
        talk.talk(runtime, "sunny", msg, first.send_json),
        talk.talk(runtime, "sunny", {**msg, "id": "k_9"}, second.send_json),
    )
    assert first.sent[-1]["type"] == "talk_reply"
    assert second.sent[-1]["type"] == "talk_error"


async def test_websocket_talk_answers_the_sender_only(at_work):
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


async def test_real_mode_talk_uses_a_throwaway_worktree(at_work, sandboxes):
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


async def carrying(resident: str, building: str) -> Task:
    """A package in progress that `resident` is working on at `building`, one step in already."""
    task = await Task.objects.acreate(
        id="t_talk1",
        island_id="sunny",
        title="Add a dark mode",
        description="Users want a dark theme toggle in settings.",
        status="in_progress",
        building_id=building,
        agent_id=resident,
    )
    await Step.objects.acreate(
        island_id="sunny",
        task_id=task.id,
        building_id="mailbox",
        building_type="mailbox",
        resident_id="jun",
        brain="mock",
        outcome="pass",
        summary="Plan: add a toggle in settings.py and a dark palette.",
        finished_at=utcnow(),
    )
    await Event.objects.acreate(
        island_id="sunny",
        type="thinking",
        payload=events.thinking(resident, task.id, "thought", "Now wiring the toggle into the settings page."),
    )
    return task


async def test_talk_knows_the_package_they_carry(at_work):
    await carrying("carlos", "workshop")
    runner = ScriptedRunner()
    await ask_once(mock_runtime(runner), {"id": "k_40", "resident": "carlos", "message": "How's it going?"})
    prompt = build_prompt(runner.jobs[0], "SYSTEM")
    assert "'Add a dark mode'" in prompt and "visit 1 to the Workshop" in prompt
    assert "Users want a dark theme toggle" in prompt  # the request
    assert "Jun at the Mailbox: PASS. Plan: add a toggle" in prompt  # the steps so far
    assert "wiring the toggle into the settings page" in prompt  # what they were just thinking
    assert "passed on to whoever works on it next" in prompt and "This is not a task" not in prompt


async def test_talk_without_a_package_is_a_plain_chat(at_work):
    runner = ScriptedRunner()
    await ask_once(mock_runtime(runner), {"id": "k_41", "resident": "carlos", "message": "Hi"})
    assert "This is not a task" in build_prompt(runner.jobs[0], "SYSTEM")


async def test_talk_lines_are_stored_with_the_package(at_work):
    await carrying("carlos", "workshop")
    await ask_once(mock_runtime(ScriptedRunner()), {"id": "k_42", "resident": "carlos", "message": "Use CSS vars"})
    lines = await talk.history("sunny", "workshop")
    assert [(t["from"], t["task"]) for t in lines] == [("you", "t_talk1"), ("resident", "t_talk1")]


async def test_real_mode_talk_reads_the_packages_worktree(at_work, sandboxes):
    task = await carrying("carlos", "workshop")
    await Resident.objects.filter(pk="carlos").aupdate(brain="claude-code")
    island_row = await Island.objects.aget(pk="sunny")
    package_dir = sandboxes.for_island(island_row).create_worktree(task.id)
    seen = []

    class Peeks:
        async def run(self, job):
            seen.append((job.workdir, job.read_only))
            return parse_verdict("I'm halfway there.", job.summary_chars)

    runtime = mock_runtime(Peeks())
    runtime.sandboxes = sandboxes
    await ask_once(runtime, {"id": "k_43", "resident": "carlos", "message": "Show me"})  # one-off
    client, _ = await open_one(runtime, "c_9", "carlos", "workshop")  # and a conversation
    msg = {"id": "k_44", "conversation": "c_9", "resident": "carlos", "message": "And now?"}
    await talk.talk(runtime, "sunny", msg, client.send_json)
    await talk.close_conversation(runtime, "c_9")
    assert seen == [(package_dir, True), (package_dir, True)]  # the work in progress, read-only
    assert package_dir.exists()  # the talk never removes the package's own worktree


async def test_what_you_say_steers_the_packages_later_steps(island):
    runner = ScriptedRunner()
    workflow, _ = make_workflow(runner)
    runtime = SimpleNamespace(workflow=workflow, bus=workflow.bus, bubbles=workflow.bubbles, sandboxes=None)
    client = FakeClient()
    workflow.holds.hold("jun")  # as talk_open does: Jun stays at the Mailbox until the talk ends
    run = asyncio.create_task(workflow.run_task((await workflow.submit("sunny", "Add a dark mode")).id))
    for _ in range(200):
        if runner.visited:
            break
        await asyncio.sleep(0.01)
    msg = {"id": "k_45", "resident": "jun", "building": "mailbox", "message": "Keep it to CSS variables, please."}
    await talk.talk(runtime, "sunny", msg, client.send_json)
    assert client.sent[-1]["type"] == "talk_reply"
    workflow.holds.release("jun")
    task = await asyncio.wait_for(run, 5)
    assert task.status == "done"
    work = [j for j in runner.jobs if j.chat is None]  # the package's steps, not the talk
    assert [j.building.id for j in work] == ["mailbox", "workshop", "lab", "courthouse", "post_office"]
    assert "Keep it to CSS variables" not in work[0].instructions  # the plan was made before you said it
    for job in work[1:]:  # every later step, whoever does it
        assert "Follow any direction they gave:\n- To Jun: Keep it to CSS variables, please." in job.instructions


class AtWork(ScriptedRunner):
    """A runner whose agent for the package can answer side chats (like a forkable ACP agent)."""

    def __init__(self, answers: bool = True):
        super().__init__()
        self.answers = answers
        self.side: list = []  # (job, task id) of each side chat asked
        self.prepared: list = []

    def can_side_chat(self, task_id, resident_id):
        return self.answers

    async def side_chat(self, job, task_id):
        self.side.append((job, task_id))
        return StepResult("pass", "Halfway through the toggle!") if self.answers else None

    async def prepare(self, job):
        self.prepared.append(job.task.id)


async def test_talk_asks_the_agent_at_work_like_btw(at_work):
    await carrying("carlos", "workshop")
    runner = AtWork()
    sent = await ask_once(mock_runtime(runner), {"id": "k_50", "resident": "carlos", "message": "How's it going?"})
    assert sent[-1] == talk.talk_reply("k_50", "carlos", "Halfway through the toggle!")
    [(job, task_id)] = runner.side
    assert task_id == "t_talk1" and job.read_only and job.chat == "How's it going?"
    assert job.instructions.startswith("btw: ") and job.instructions.endswith("Owner: How's it going?")
    assert runner.jobs == []  # no fresh agent was asked
    last = (await talk.history("sunny", "workshop"))[-1]
    assert (last["from"], last["task"], last["text"]) == ("resident", "t_talk1", "Halfway through the toggle!")


async def test_a_conversation_with_the_agent_at_work_starts_nothing_new(at_work):
    await carrying("carlos", "workshop")
    runner = AtWork()
    runtime = mock_runtime(runner)
    client, sent = await open_one(runtime, "c_10", "carlos", "workshop")
    assert sent == [talk.talk_ready("c_10", "carlos")] and runner.prepared == []  # no warm-up
    msg = {"id": "k_51", "conversation": "c_10", "resident": "carlos", "message": "Any blockers?"}
    await talk.talk(runtime, "sunny", msg, client.send_json)
    assert client.sent[-1]["type"] == "talk_reply" and len(runner.side) == 1
    await talk.close_conversation(runtime, "c_10")


async def test_talk_falls_back_to_a_fresh_agent_when_the_side_chat_cannot(at_work):
    await carrying("carlos", "workshop")
    runner = AtWork(answers=False)
    sent = await ask_once(mock_runtime(runner), {"id": "k_52", "resident": "carlos", "message": "Hi"})
    assert sent[-1]["type"] == "talk_reply" and len(runner.side) == 1
    [job] = runner.jobs  # the fresh agent, told the package
    assert "'Add a dark mode'" in build_prompt(job, "SYSTEM")


def test_chat_reply_keeps_long_answers():
    job = make_job()
    job.chat = "Tell me everything"
    long = "word " * 300
    assert parse_verdict(long, job.summary_chars).summary == long.strip()
    assert len(parse_verdict(long, make_job().summary_chars).summary) <= 401


# --- Conversations (talk_open / talk_close) ---------------------------------------


async def open_one(runtime, conv_id: str, resident: str, building: str) -> tuple[FakeClient, list[dict]]:
    client = FakeClient()
    await talk.open_conversation(
        runtime, "sunny", {"id": conv_id, "resident": resident, "building": building}, client.send_json
    )
    return client, client.sent


async def test_a_conversation_holds_the_resident_until_it_closes(at_work):
    runtime = mock_runtime()
    _, sent = await open_one(runtime, "c_1", "jun", "mailbox")
    assert sent == [talk.talk_ready("c_1", "jun")]
    assert runtime.workflow.holds.held("jun")
    _, sent = await open_one(runtime, "c_2", "jun", "mailbox")
    assert sent[0]["type"] == "talk_error"  # one conversation per resident
    await talk.close_conversation(runtime, "c_1")
    assert not runtime.workflow.holds.held("jun") and "c_1" not in talk._conversations


async def test_only_residents_inside_can_open_a_conversation(island):
    _, sent = await open_one(mock_runtime(), "c_3", "jun", "mailbox")  # idle: out on the island
    assert sent == [talk.talk_error("c_3", "jun", "Jun isn't inside the Mailbox right now.")]


async def test_the_engine_keeps_a_held_resident_inside(island):
    runner = ScriptedRunner()
    workflow, client = make_workflow(runner)
    workflow.holds.hold("jun")
    run = asyncio.create_task(workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id))
    for _ in range(100):
        if runner.visited:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    assert runner.visited == ["mailbox"] and not run.done()  # Jun finished the plan but stays at the Mailbox
    jun = await Resident.objects.aget(pk="jun")
    assert (jun.state, jun.building_id) == ("working", "mailbox")
    assert any("Chatting with you" in e["text"] for e in client.of_type("activity"))
    workflow.holds.release("jun")
    task = await asyncio.wait_for(run, 5)
    assert task.status == "done" and runner.visited[1] == "workshop"


async def test_disconnect_and_silence_end_conversations(at_work, monkeypatch):
    runtime = mock_runtime()
    client, _ = await open_one(runtime, "c_4", "carlos", "workshop")
    await talk.close_all(runtime, client.send_json)
    assert not runtime.workflow.holds.held("carlos")

    monkeypatch.setattr(talk, "IDLE_LIMIT", 0.05)
    client, _ = await open_one(runtime, "c_5", "sydney", "courthouse")
    for _ in range(100):
        if not runtime.workflow.holds.held("sydney"):
            break
        await asyncio.sleep(0.02)
    assert not runtime.workflow.holds.held("sydney")
    assert client.sent[-1]["type"] == "talk_closed"


async def test_talk_lines_are_kept_per_building_and_out_of_the_news(at_work):
    from mii.models import Event

    runtime = mock_runtime(ScriptedRunner())
    client, _ = await open_one(runtime, "c_6", "carlos", "workshop")
    msg = {"id": "k_20", "conversation": "c_6", "resident": "carlos", "building": "workshop", "message": "Hi!"}
    await talk.talk(runtime, "sunny", msg, client.send_json)
    await ask_once(runtime, {"id": "k_21", "resident": "jun", "building": "mailbox", "message": "Hello?"})
    lines = await talk.history("sunny", "workshop")
    assert [(t["from"], t["agent"], t["text"]) for t in lines] == [
        ("you", "carlos", "Hi!"),
        ("resident", "carlos", "scripted"),
    ]
    assert [t["text"] for t in await talk.history("sunny", "mailbox")] == ["Hello?", "scripted"]
    assert await Event.objects.filter(type=talk.TURN_TYPE).acount() == 4
    await talk.close_conversation(runtime, "c_6")


async def test_a_conversation_reuses_one_worktree_and_agent(at_work, sandboxes):
    await Resident.objects.filter(pk="carlos").aupdate(brain="claude-code")
    seen = []

    class Remembers:
        prepared = []
        ended = []

        async def prepare(self, job):
            self.prepared.append(job.task.id)

        async def end_task(self, task_id):
            self.ended.append(task_id)

        async def run(self, job):
            seen.append((job.task.id, job.workdir))
            return parse_verdict("Hi again!", job.summary_chars)

    runner = Remembers()
    runtime = mock_runtime(runner)
    runtime.sandboxes = sandboxes
    client, sent = await open_one(runtime, "c_7", "carlos", "workshop")
    assert sent[-1]["type"] == "talk_ready"
    for i in range(2):
        msg = {"id": f"k_3{i}", "conversation": "c_7", "resident": "carlos", "building": "workshop", "message": "Hey"}
        await talk.talk(runtime, "sunny", msg, client.send_json)
    assert runner.prepared == ["talk_c_7"]  # warmed up as soon as it opened
    assert seen[0] == seen[1] and seen[0][1].exists()  # same agent key, same worktree, both turns
    await talk.close_conversation(runtime, "c_7")
    assert runner.ended == ["talk_c_7"] and not seen[0][1].exists()
