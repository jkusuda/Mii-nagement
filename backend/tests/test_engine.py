import asyncio
import random

import subprocess

import pytest
from conftest import FakeClient, RecordingAsk, ScriptedRunner, db_test, make_job, make_workflow

from mii import events
from mii.agents import StepResult
from mii.agents.mock import MockRunner
from mii.engine import IslandGraph, split_pr_text
from mii.models import Event, Island, Resident, Step, Task
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


class FakeGitHub:
    """Stands in for `gh`: records the PRs opened and merged."""

    def __init__(self, monkeypatch):
        self.opened, self.merged = [], []
        monkeypatch.setattr("mii.github.open_pull_request", self.open)
        monkeypatch.setattr("mii.github.merge_pull_request", self.merge)

    def open(self, repo, worktree, branch, title, body, base=None):
        self.opened.append(branch)
        return f"https://github.com/{repo}/pull/{len(self.opened)}"

    def merge(self, url):
        self.merged.append(url)
        return "squash"


async def _answer_ready(workflow, answers: list[str]):
    """Answer each ✅ Ready bubble in turn, as the island owner would."""
    for answer in answers:
        while not [b for b in workflow.bubbles.open.values() if b.kind == "ready"]:
            await asyncio.sleep(0.01)
        bubble = next(b for b in workflow.bubbles.open.values() if b.kind == "ready")
        assert bubble.options == ["Approve & merge", "Send back"] and bubble.task
        workflow.bubbles.answer(bubble.id, answer)
        while bubble.id in workflow.bubbles.open:
            await asyncio.sleep(0.01)


async def test_courthouse_approval_merges_at_the_post_office(island, sandbox, sandboxes, monkeypatch):
    gh = FakeGitHub(monkeypatch)
    await Island.objects.filter(pk="sunny").aupdate(repo="o/r", rules={"merge_pr": True})
    workflow, _ = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="true")
    task = await workflow.submit("sunny", "Add feature")
    answering = asyncio.create_task(_answer_ready(workflow, ["Send back: also test IPv6", "Approve & merge"]))
    task = await workflow.run_task(task.id)
    await answering

    assert task.status == "done" and gh.merged == [task.pr_url] and task.merged_at is not None
    court = [s async for s in Step.objects.filter(task_id=task.id, building_type="courthouse").order_by("id")]
    assert [s.outcome for s in court] == ["fail", "pass"]  # sent back once, then approved
    assert "also test IPv6" in court[0].summary
    workshop_visits = await Step.objects.filter(task_id=task.id, building_type="workshop").acount()
    assert workshop_visits == 2  # the note went back to the coder


async def test_unanswered_review_leaves_the_pr_open_and_rule_off_merges_without_asking(island, sandbox, sandboxes, monkeypatch):
    gh = FakeGitHub(monkeypatch)
    await Island.objects.filter(pk="sunny").aupdate(repo="o/r", rules={"merge_pr": True})
    workflow, client = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="true")
    task = await workflow.run_task((await workflow.submit("sunny", "Nobody looks")).id)
    assert task.status == "done" and task.pr_url and gh.merged == []  # bubble timed out
    assert [e["kind"] for e in client.of_type("bubble")] == ["ready"]

    await Island.objects.filter(pk="sunny").aupdate(rules={"merge_pr": False})
    workflow, client = make_workflow(WritingRunner(), sandboxes=sandboxes, test_command="true")
    task = await workflow.run_task((await workflow.submit("sunny", "Trusted")).id)
    assert gh.merged == [task.pr_url] and client.of_type("bubble") == []


def test_merge_tries_the_methods_the_repo_allows(monkeypatch):
    from mii import github

    tried = []

    def fake_run(args, cwd=None):
        tried.append(args[-1])
        if args[-1] == "--squash":
            raise github.GitHubError("`gh pr merge` failed: Squash merges are not allowed on this repository")
        return ""

    monkeypatch.setattr(github, "_run", fake_run)
    assert github.merge_pull_request("https://github.com/o/r/pull/1") == "merge" and tried == ["--squash", "--merge"]

    monkeypatch.setattr(github, "_run", lambda args, cwd=None: (_ for _ in ()).throw(github.GitHubError("checks failing")))
    with pytest.raises(github.GitHubError, match="checks failing"):
        github.merge_pull_request("https://github.com/o/r/pull/1")


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


async def test_mock_residents_stream_thinking_for_their_task(island):
    workflow, client = make_workflow(MockRunner(work_seconds=(0, 0), bubble_chance=0.0, rng=random.Random(3)))
    task = await workflow.run_task((await workflow.submit("sunny", "Think aloud")).id)
    thoughts = client.of_type("thinking")
    assert thoughts and all(e["task"] == task.id for e in thoughts)
    assert {e["kind"] for e in thoughts} == {"thought", "tool", "reply"}


async def test_bubble_carries_its_task_and_detail(island):
    bubbles = events.Bubbles(events.EventBus())
    asking = asyncio.create_task(
        bubbles.ask("sunny", "carlos", "permission", "Run npm?", 5, ["Allow", "Deny"], "t_1", "npm install zod")
    )
    while not bubbles.open:
        await asyncio.sleep(0.01)
    [shown] = bubbles.for_island("sunny")
    assert shown["task"] == "t_1" and shown["detail"] == "npm install zod"
    bubbles.answer(shown["id"], "Deny")
    assert await asking == "Deny"


async def test_mock_ready_bubble_can_send_work_back(island):
    runner = MockRunner(work_seconds=(0, 0), bubble_chance=1.0, rng=random.Random(1))
    job = make_job("mock", building_type="courthouse", ask=RecordingAsk("Send back: also test IPv6"))
    result = await runner.run(job)
    assert job.ask.asked[0][0] == "ready" and job.ask.asked[0][2] == ["Approve", "Send back"]
    assert result.outcome == "fail" and "also test IPv6" in result.summary


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


# --- Help visits, chat, diary ---------------------------------------------------------


class LineRunner:
    """Replies with fixed text per building type, parsed like a real agent's reply."""

    def __init__(self, **replies: list[str]):
        self.replies = replies
        self.jobs: list = []

    async def run(self, job) -> StepResult:
        from mii.agents import parse_verdict

        self.jobs.append(job)
        planned = self.replies.get(job.building.type, [])
        return parse_verdict(planned.pop(0) if planned else "fine\nVERDICT: PASS")


async def test_a_neighbour_takes_the_workshop_when_the_lab_keeps_failing(island):
    runner = ScriptedRunner(lab=["fail", "fail", "pass"])
    workflow, client = make_workflow(runner)
    task = await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)

    assert task.status == "done"
    # Lab fails twice: Carlos gets one retry of his own, then Jun (his only friend so far) takes the
    # third Workshop turn. The Lab itself is never "helped": its tests are the owner's call.
    assert runner.visited == ["mailbox", "workshop", "lab", "workshop", "lab", "workshop", "lab", "courthouse", "post_office"]
    assert runner.workers[3:6] == ["carlos", "carlos", "jun"]
    chats = [(e["from"], e["to"]) for e in client.of_type("chat")]
    assert chats == [("carlos", "jun"), ("jun", "carlos")]
    assert "came back to the Workshop again" in client.of_type("chat")[0]["text"]
    steps = [s async for s in Step.objects.filter(task_id=task.id).order_by("id")]
    assert steps[5].resident_id == "jun" and steps[5].helped == "carlos" and steps[5].building_type == "workshop"
    assert runner.jobs[5].instructions.startswith("Carlos asked for your help: The package came back")
    assert "The package came back" not in "\n".join(runner.jobs[5].messages)  # not repeated under Messages
    assert ("carlos", "jun") in [(e["a"], e["b"]) for e in client.of_type("friendship")]
    jun_states = [(e["state"], e["building"]) for e in client.of_type("agent_state") if e["agent"] == "jun"]
    assert ("working", "workshop") in jun_states and jun_states[-1] == ("idle", "post_office")
    assert [(e["from"], e["to"]) for e in client.of_type("handoff")][-3:] == [("jun", "carlos"), ("carlos", "sydney"), ("sydney", "jun")]
    assert any("Jun helped Carlos at the Workshop" in note for note in runner.jobs[-1].history)


async def test_help_say_and_remember_lines(island):
    runner = LineRunner(workshop=["Stuck.\nHELP: the auth tests hang\nSAY TO Sydney: heads up\nREMEMBER: tests are slow\nVERDICT: FAIL"])
    workflow, client = make_workflow(runner)
    task = await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)

    assert task.status == "done"
    # An explicit HELP line gets a takeover of the same building right away. No other coder: Jun, the best friend, comes.
    visits = [(j.building.id, j.resident.id) for j in runner.jobs]
    assert visits[:3] == [("mailbox", "jun"), ("workshop", "carlos"), ("workshop", "jun")]
    helper_job = runner.jobs[2]
    assert helper_job.instructions.startswith("Carlos asked for your help: the auth tests hang")
    assert (await Resident.objects.aget(pk="carlos")).notes == "tests are slow"
    sydney_job = next(j for j in runner.jobs if j.resident.id == "sydney")
    assert sydney_job.messages == ["Carlos: heads up"] and "Carlos (coder)" in sydney_job.neighbours
    chats = [(e["from"], e["to"], e["text"]) for e in client.of_type("chat")]
    assert ("carlos", "sydney", "heads up") in chats and ("carlos", "jun", "the auth tests hang") in chats
    summaries = [s.summary async for s in Step.objects.filter(task_id=task.id)]
    assert all("HELP:" not in s and "SAY TO" not in s for s in summaries)


async def test_diary_reaches_the_next_task_and_stays_short(island):
    await Resident.objects.filter(pk="carlos").aupdate(notes="\n".join(f"old note {i}: " + "x" * 40 for i in range(80)))
    runner = LineRunner(workshop=["ok\nREMEMBER: newest\nVERDICT: PASS"])
    workflow, _ = make_workflow(runner)
    await workflow.run_task((await workflow.submit("sunny", "One")).id)
    notes = (await Resident.objects.aget(pk="carlos")).notes
    assert notes.endswith("newest") and "old note 0" not in notes and len(notes) <= 1500
    assert "old note 79" in runner.jobs[1].notes  # the diary was in the prompt


async def test_help_is_capped_per_task(island):
    workflow, _ = make_workflow(ScriptedRunner(lab=["fail"] * 100))
    task = await workflow.run_task((await workflow.submit("sunny", "Impossible")).id)
    assert task.status == "failed"
    assert await Step.objects.filter(task_id=task.id, helped__isnull=False).acount() == 2


async def test_cancel_during_a_help_visit_sets_the_helper_idle(island):
    workflow, _ = make_workflow(ScriptedRunner(delay=0.3, lab=["fail", "fail", "fail"]))
    task = await workflow.submit("sunny", "Slow one")
    run = asyncio.create_task(workflow.run_task(task.id))
    for _ in range(200):  # wait until Jun is working at the Workshop (the takeover, third visit)
        await asyncio.sleep(0.05)
        jun = await Resident.objects.aget(pk="jun")
        if jun.state == "working" and jun.building_id == "workshop":
            break
    else:
        pytest.fail("the help visit never started")
    workflow._cancelling.add(task.id)
    run.cancel()
    await asyncio.wait([run])
    assert (await Task.objects.aget(pk=task.id)).status == "cancelled"
    assert {(await Resident.objects.aget(pk=r)).state for r in ("jun", "carlos")} == {"idle"}


class FlakyJudge:
    """A judge that calls every Lab failure flaky (the rules otherwise)."""

    name = "rules"

    def __getattr__(self, name):
        from mii.judge import RuleJudge

        return getattr(RuleJudge(), name)

    async def flaky(self, output, stat):
        return 1.0


async def test_lab_reruns_a_flaky_test_once(island, sandboxes):
    from mii.insights import island_insights

    # Fails the first time it runs in a worktree, passes the second time.
    workflow, client = make_workflow(ScriptedRunner(), sandboxes=sandboxes, judge=FlakyJudge(),
                                     test_command="test -f .ran || { touch .ran; exit 1; }")
    task = await workflow.run_task((await workflow.submit("sunny", "Flaky")).id)
    assert task.status == "done"
    lab = await Step.objects.aget(task_id=task.id, building_type="lab")
    assert lab.outcome == "pass" and lab.flaky is True and "second run" in lab.summary
    assert any("looks flaky" in e["text"] for e in client.of_type("activity"))
    assert (await island_insights("sunny"))["island"]["flaky_tests"] == 1


async def test_mock_runner_sometimes_asks_for_help_or_chats():
    from conftest import make_job

    asked, said, remembered = 0, 0, 0
    for seed_value in range(40):
        runner = MockRunner(work_seconds=(0, 0), bubble_chance=0.0, rng=random.Random(seed_value))
        job = make_job(building_type="lab")
        job.neighbours = ["Jun (planner)"]
        result = await runner.run(job)
        asked += result.help_request is not None
        said += bool(result.says)
        remembered += bool(result.remember)
    assert asked and said and remembered


async def test_lab_skips_the_flaky_rerun_when_the_step_budget_is_spent(island, sandboxes):
    # The first run eats the whole budget (a hang): no second run, and the step fails cleanly.
    workflow, client = make_workflow(ScriptedRunner(), sandboxes=sandboxes, judge=FlakyJudge(),
                                     test_command="sleep 30", step_timeout=1.5)
    task = await workflow.run_task((await workflow.submit("sunny", "Hangs")).id)
    assert task.status == "failed"
    lab = await Step.objects.filter(task_id=task.id, building_type="lab").afirst()
    assert lab.outcome == "fail" and lab.flaky is False
    assert not any("looks flaky" in e["text"] for e in client.of_type("activity"))
