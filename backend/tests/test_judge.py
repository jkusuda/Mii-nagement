"""The judge: the rules reproduce the old engine behaviour; Jev is asked over HTTP and falls back."""

import json
import logging

import httpx
from conftest import make_job

from mii import judge
from mii.agents import StepResult
from mii.judge import HelpRequest, JevJudge, RuleJudge
from mii.models import Building, Resident, Task


def resident(id: str, job: str, state: str = "idle") -> Resident:
    return Resident(id=id, island_id="sunny", name=id.title(), job=job, state=state)


def request(asker: Resident, building_type: str = "lab", friendship: dict | None = None) -> HelpRequest:
    building = Building(id=building_type, island_id="sunny", type=building_type, name=building_type.title(), x=0, y=0)
    return HelpRequest(asker, building, Task(id="t_1", island_id="sunny", title="Fix it"), "help?", friendship or {})


# --- Rules ------------------------------------------------------------------------


async def test_rule_judge_is_the_old_behaviour():
    rules = RuleJudge()
    job = make_job()
    assert await rules.mood(job.resident, job.building, StepResult("pass"), 1, False, 0) == "happy"
    assert await rules.mood(job.resident, job.building, StepResult("fail"), 1, False, 0) == "frustrated"
    assert await rules.mood(job.resident, job.building, StepResult("fail"), 1, True, 0) == "tired"
    assert (await rules.verdict(job, StepResult("pass", "x", explicit=False))).outcome == "pass"
    assert await rules.flaky("Error: timeout", "") == 0.0
    assert await rules.tip({}, {"bouncy": "first", "steady": "second"}) == "first"


async def test_rule_judge_needs_help_from_the_third_visit():
    rules = RuleJudge()
    workshop = make_job(building_type="workshop").building
    assert not await rules.needs_help(workshop, 1, "")  # first visit
    assert not await rules.needs_help(workshop, 2, "Carlos at the Lab: FAIL.")  # the owner's own retry
    assert await rules.needs_help(workshop, 3, "Carlos at the Lab: FAIL.")  # a neighbour takes the next turn


async def test_rule_judge_picks_the_fitting_job_then_the_best_friend():
    rules = RuleJudge()
    carlos, jun, sydney, mo = (
        resident("carlos", "coder"),
        resident("jun", "planner"),
        resident("sydney", "reviewer"),
        resident("mo", "coder"),
    )
    assert await rules.pick_helper(request(carlos, "lab"), [jun, sydney, mo]) is mo  # a coder for the Lab
    friends = {"jun": 0.2, "sydney": 0.6}
    assert await rules.pick_helper(request(carlos, "lab", friends), [jun, sydney]) is sydney  # else the best friend
    assert await rules.pick_helper(request(carlos, "lab"), [jun, sydney]) is None  # strangers stay home
    assert await rules.pick_helper(request(carlos, "lab"), []) is None


# --- Jev --------------------------------------------------------------------------


def jev(answers: dict | None = None, status: int = 200, raise_error: Exception | None = None):
    """A JevJudge talking to a fake TypeSafe. Returns (judge, the requests it made)."""
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"url": str(req.url), "auth": req.headers.get("authorization"), "body": json.loads(req.content)})
        if raise_error:
            raise raise_error
        return httpx.Response(status, json={"model": "jev-1.13.0", "answers": answers or {}, "usage": {}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JevJudge("k-123", http=http), seen


async def test_jev_judge_asks_typesafe_and_reads_the_answers():
    j, seen = jev({"mood": {"type": "choice", "choice": "proud", "confidence": 0.9, "probabilities": {"proud": 0.9}}})
    job = make_job()
    assert await j.mood(job.resident, job.building, StepResult("pass", "Shipped it!"), 1, False, 0) == "proud"
    assert seen[0]["url"] == judge.API_URL and seen[0]["auth"] == "Bearer k-123"
    body = seen[0]["body"]
    assert body["model"] == judge.MODEL and body["questions"]["mood"]["type"] == "choice"
    assert set(body["questions"]["mood"]["criteria"]) == set(judge.MOODS)
    assert body["state"]["step"]["summary"] == "Shipped it!"

    j, _ = jev({"done": {"type": "noul", "noul": 0.1}})
    result = await j.verdict(job, StepResult("pass", "I gave up.", explicit=False))
    assert result.outcome == "fail" and result.explicit is False and "judge" in result.summary

    j, _ = jev({"flaky": {"type": "noul", "noul": 0.93}})
    assert await j.flaky("Error: connection refused on port 5432", "1 file changed") == 0.93

    j, seen = jev({"tip": {"type": "choice", "choice": "lab"}})
    assert await j.tip({"tasks_done": 3}, {"bouncy": "A", "lab": "B", "steady": "C"}) == "B"
    assert (
        await j.tip({"tasks_done": 0}, {"quiet": "only one"}) == "only one" and len(seen) == 1
    )  # no call for one candidate

    j, seen = jev({"help": {"type": "noul", "noul": 0.8}})
    assert await j.needs_help(job.building, 2, "Carlos at the Lab: FAIL. 3 tests red")
    assert seen[-1]["body"]["state"]["visit"] == 2
    j, _ = jev({"help": {"type": "noul", "noul": 0.2}})
    assert not await j.needs_help(job.building, 2, "Carlos at the Lab: FAIL.")

    jun, sydney = resident("jun", "planner"), resident("sydney", "reviewer")
    j, seen = jev({"helper": {"type": "choice", "choice": "sydney"}})
    assert await j.pick_helper(request(resident("carlos", "coder")), [jun, sydney]) is sydney
    assert "nobody" in seen[0]["body"]["questions"]["helper"]["criteria"]
    j, _ = jev({"helper": {"type": "choice", "choice": "nobody"}})
    assert await j.pick_helper(request(resident("carlos", "coder")), [jun, sydney]) is None


async def test_jev_judge_falls_back_to_the_rules(caplog):
    job = make_job()
    with caplog.at_level(logging.WARNING, logger="mii.judge"):
        j, _ = jev(status=500)
        assert await j.mood(job.resident, job.building, StepResult("pass"), 1, False, 0) == "happy"
        assert await j.flaky("boom", "") == 0.0
        j2, _ = jev(raise_error=httpx.ConnectTimeout("slow"))
        assert (await j2.verdict(job, StepResult("pass", "?", explicit=False))).outcome == "pass"
        j3, _ = jev({"mood": {"type": "choice", "choice": "ecstatic"}})  # not one of ours
        assert await j3.mood(job.resident, job.building, StepResult("fail"), 1, False, 0) == "frustrated"
        j4, _ = jev(status=429)
        assert await j4.needs_help(job.building, 2, "x") is False
    assert [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "mii.judge"]
    assert (
        sum(
            1
            for r in caplog.records
            if r.levelno == logging.WARNING and "Jev is unavailable" in r.message and r.name == "mii.judge"
        )
        == 3
    )
    # ... one per judge instance (j, j2, j4); the second failure of the same judge is only logged at DEBUG
    j5, _ = jev(status=500)
    caplog.clear()
    await j5.flaky("a", "")
    await j5.flaky("b", "")
    assert sum(1 for r in caplog.records if r.levelno == logging.WARNING) == 1
