import asyncio
import random

import subprocess

import pytest
from conftest import FakeClient, ScriptedRunner, db_test, make_workflow

from mii import events
from mii.agents import StepResult
from mii.agents.mock import MockRunner
from mii.engine import IslandGraph, split_pr_text
from mii.models import Event, Step, Task
from mii.sandbox import Sandbox, SandboxError, is_inside

pytestmark = db_test


# --- Graph ----------------------------------------------------------------------


async def test_graph_follows_roads_by_outcome(island):
    graph = await IslandGraph.load("sunny")
    assert graph.start() == "mailbox"
    assert graph.next_building("mailbox", "pass") == "workshop"  # "always" road
    assert graph.next_building("lab", "pass") == "courthouse"
    assert graph.next_building("lab", "fail") == "workshop"
    assert graph.next_building("post_office", "pass") is None  # end of the line


# --- Engine -----------------------------------------------------------------------


async def test_task_bounces_back_on_fail_then_finishes(island):
    runner = ScriptedRunner(lab=["fail", "pass"])
    workflow, client = make_workflow(runner)
    task = await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)

    assert task.status == "done"
    assert runner.visited == ["mailbox", "workshop", "lab", "workshop", "lab", "courthouse", "post_office"]
    handoffs = [(e["from"], e["to"], e["outcome"]) for e in client.of_type("handoff")]
    assert handoffs == [("jun", "carlos", "success"), ("carlos", "sydney", "success"), ("sydney", "jun", "success")]


async def test_package_that_never_passes_gives_up(island):
    workflow, _ = make_workflow(ScriptedRunner(lab=["fail"] * 100))
    task = await workflow.run_task((await workflow.submit("sunny", "Impossible bug")).id)
    assert task.status == "failed" and task.finished_at is not None


async def test_every_step_is_recorded_for_insights(island):
    workflow, _ = make_workflow(ScriptedRunner(courthouse=["fail", "pass"]))
    task = await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)
    steps = [s async for s in Step.objects.filter(task_id=task.id).order_by("id")]
    assert [s.building_type for s in steps][:5] == ["mailbox", "workshop", "lab", "courthouse", "workshop"]
    assert steps[3].outcome == "fail" and steps[3].brain == "codex"  # Sydney's brain
    assert all(s.finished_at is not None for s in steps)


async def test_slow_step_times_out_and_fails(island):
    workflow, _ = make_workflow(ScriptedRunner(delay=1.0), step_timeout=0.01)
    task = await workflow.run_task((await workflow.submit("sunny", "Slow one")).id)
    first = await Step.objects.filter(task_id=task.id).order_by("id").afirst()
    assert first.timed_out and first.outcome == "fail"


async def test_every_event_is_stored_in_sqlite(island):
    workflow, client = make_workflow(ScriptedRunner())
    await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)
    assert await Event.objects.acount() == len(client.sent) > 0


async def test_mock_runner_end_to_end(island):
    runner = MockRunner(work_seconds=(0, 0), bubble_chance=1.0, rng=random.Random(7))
    workflow, client = make_workflow(runner)
    task = await workflow.run_task((await workflow.submit("sunny", "Mock task")).id)
    assert task.status in ("done", "failed")
    assert len(client.of_type("activity")) > 0
    assert len(client.of_type("bubble")) == len(client.of_type("bubble_resolved")) > 0


async def test_recover_requeues_waiting_tasks_and_fails_half_done_ones(island):
    workflow, _ = make_workflow(ScriptedRunner())
    waiting = await workflow.submit("sunny", "Waiting")
    half_done = await workflow.submit("sunny", "Half done")
    await Task.objects.filter(pk=half_done.id).aupdate(status="in_progress")
    workflow.queue = asyncio.Queue()  # pretend the server restarted

    await workflow.recover()

    assert (await Task.objects.aget(pk=half_done.id)).status == "failed"
    assert workflow.queue.get_nowait() == waiting.id


# --- Real-mode handlers (a throwaway git repo stands in for the island's repository) ----


class WritingRunner:
    """Writes a file at the workshop, like a coder would; passes everywhere."""

    async def run(self, job) -> StepResult:
        if job.building.type == "workshop":
            (job.workdir / "feature.py").write_text("x = 1\n")
        if job.building.type == "post_office":
            return StepResult("pass", "Add feature\n\nAdds x.")
        return StepResult("pass", "ok")


async def test_lab_runs_the_test_command_and_workshop_commits(island, sandbox, sandboxes):
    workflow, client = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="test -f feature.py", keep_worktrees=True)
    task = await workflow.run_task((await workflow.submit("sunny", "Add feature")).id)

    assert task.status == "done" and task.branch == "mii/" + task.id and task.finished_at is not None
    workdir = sandbox.worktree_path(task.id)
    assert workdir.exists()  # keep_worktrees
    log = sandbox._git(workdir, "log", "--oneline")
    assert "Add feature" in log and "init" in log
    assert any("Running: test -f feature.py" == e["text"] for e in client.of_type("activity"))
    steps = [s async for s in Step.objects.filter(task_id=task.id, building_type="lab")]
    assert steps[0].outcome == "pass"


async def test_lab_fails_on_a_failing_test_command_and_worktree_is_removed(island, sandbox, sandboxes):
    workflow, _ = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="exit 3")
    task = await workflow.run_task((await workflow.submit("sunny", "Broken")).id)
    assert task.status == "failed"
    lab = await Step.objects.filter(task_id=task.id, building_type="lab").afirst()
    assert lab.outcome == "fail"
    assert not sandbox.worktree_path(task.id).exists()


async def test_post_office_skips_the_pr_without_a_repo(island, sandbox, sandboxes):
    workflow, _ = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="true")
    task = await workflow.run_task((await workflow.submit("sunny", "Add feature")).id)
    assert task.status == "done" and task.pr_url is None


async def test_real_mode_task_fails_when_the_island_has_no_repository(island, tmp_path):
    from mii.sandbox import Sandboxes

    workflow, _ = make_workflow(WritingRunner(), sandboxes=Sandboxes(tmp_path / "wt"))
    task = await workflow.submit("sunny", "No repo")
    with pytest.raises(SandboxError, match="no repository"):
        await workflow.run_task(task.id)


async def test_tasks_branch_off_the_islands_branch(island, sandbox, sandboxes):
    from mii.models import Island

    git = ["git", "-C", str(sandbox.repo_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    first = subprocess.run([*git, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run([*git, "checkout", "-q", "-b", "develop"], check=True)
    (sandbox.repo_path / "dev.txt").write_text("dev\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "on develop"], check=True)
    subprocess.run([*git, "checkout", "-q", first], check=True)  # HEAD is back on the first branch
    await Island.objects.filter(pk="sunny").aupdate(branch="develop")

    workflow, _ = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="test -f dev.txt")
    task = await workflow.run_task((await workflow.submit("sunny", "From develop")).id)
    assert task.status == "done"  # dev.txt exists only on develop
    assert sandbox.resolve_branch("nope") == "HEAD" and sandbox.resolve_branch("develop") == "develop"


def test_split_pr_text():
    assert split_pr_text("# Add feature\n\nAdds x.", "fallback") == ("Add feature", "Adds x.")
    assert split_pr_text("Title: Fix\nDescription: body", "fallback") == ("Fix", "body")
    assert split_pr_text("   ", "fallback") == ("fallback", "")


# --- Bubbles -------------------------------------------------------------------------


async def test_bubble_answer_reaches_the_waiting_resident(island):
    bus = events.EventBus()
    client = type("C", (), {})()
    client.sent = []

    async def send_json(event):
        client.sent.append(event)

    client.send_json = send_json
    bus.add_client(client)
    bubbles = events.Bubbles(bus)

    asking = asyncio.create_task(bubbles.ask("sunny", "carlos", "permission", "Run npm?", 5, ["Allow", "Deny"]))
    while not [e for e in client.sent if e["type"] == "bubble"]:
        await asyncio.sleep(0.01)  # let ask() run until the bubble is shown
    shown = [e for e in client.sent if e["type"] == "bubble"][0]
    assert shown["options"] == ["Allow", "Deny"]

    assert bubbles.answer(shown["id"], "Allow") is True
    assert await asking == "Allow"
    assert bubbles.answer(shown["id"], "again") is False  # already closed
    [resolved] = [e for e in client.sent if e["type"] == "bubble_resolved"]
    assert resolved["answered"] is True and resolved["response"] == "Allow"


async def test_bubble_times_out_with_none(island):
    bus = events.EventBus()
    client = FakeClient()
    bus.add_client(client)
    bubbles = events.Bubbles(bus)
    assert await bubbles.ask("sunny", "jun", "tired", "Zzz?", timeout=0.01) is None
    assert bubbles.open == {}
    assert client.of_type("bubble_resolved")[0]["answered"] is False


async def test_permission_bubbles_can_wait_longer(island):
    workflow, _ = make_workflow(ScriptedRunner(), bubble_timeout=1, permission_timeout=7)
    assert workflow.permission_timeout == 7
    assert make_workflow(ScriptedRunner(), bubble_timeout=1)[0].permission_timeout == 1


# --- Sandbox -------------------------------------------------------------------------


def test_is_inside(tmp_path):
    assert is_inside(tmp_path / "a" / "b.py", tmp_path)
    assert not is_inside(tmp_path / ".." / "elsewhere", tmp_path)
    assert not is_inside("/etc/passwd", tmp_path)


def test_worktrees_are_created_under_the_worktree_root(tmp_path, sandbox):
    path = sandbox.create_worktree("t_abc123")
    assert path.exists() and is_inside(path, tmp_path / "worktrees")
    sandbox.check_workdir(path)  # doesn't raise
    with pytest.raises(SandboxError):
        sandbox.create_worktree("../../evil")
    with pytest.raises(SandboxError):
        sandbox.check_workdir(sandbox.repo_path)


def test_commit_all_and_diff_stat(sandbox):
    path = sandbox.create_worktree("t_commit")
    assert sandbox.commit_all("t_commit", "nothing yet") is False
    (path / "new.txt").write_text("hi\n")
    assert sandbox.commit_all("t_commit", "add new.txt", "Tester <t@example.com>") is True
    log = sandbox._git(path, "log", "-1", "--format=%an <%ae> %s")
    assert log.strip() == "Tester <t@example.com> add new.txt"
    assert "new.txt" in sandbox.diff_stat("t_commit")
    sandbox.remove_worktree("t_commit")
    assert not path.exists()
