"""The judge: small typed decisions the engine asks about a step.

Two judges with the same methods, so the engine never cares which one it has:

    RuleJudge   today's behaviour in plain Python. The default; never touches the network.
    JevJudge    asks TypeSafe's Jev model, a fast classifier that answers typed questions
                (pick one option, a score, or a yes/no probability) about a state dict in
                about 100-500 ms, and falls back to RuleJudge on any error or timeout.

The judge never does the work and never decides permissions (house keys and ✋ bubbles
are the safety story; a probability is not). It decides flavour and small calls:

    mood         how a resident feels right after a step (the `mood` event)
    verdict      pass or fail when an agent forgot its VERDICT line
    flaky        does a Lab failure look flaky, so the tests get one more run
    tip          which Insights tip fits the numbers
    needs_help   is a resident stuck enough that a neighbour should come over
    pick_helper  which free neighbour should come

A `typesafe_api_key` secret (TYPESAFE_API_KEY) switches JevJudge on; the `judge`
setting ("auto" / "off") can switch it off again (runtime.reload()).

API (verified 2026-10-17): POST https://api.typesafe.ai/v1/systemone with
{"model", "state", "questions": {id: {"type": "choice"|"score"|"noul", "instructions", "criteria"?}}}
-> {"answers": {id: {"type", "choice"|"score"|"noul", "confidence"?, "probabilities"?}}}.
"""

import logging
from dataclasses import dataclass, replace

import httpx

from mii.agents import AgentJob, StepResult
from mii.models import Building, Resident, Task

log = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT = 4.0  # seconds; past this we use the rule answer
VERDICT_FAIL_BELOW = 0.4  # "did the agent finish?" probability under this = fail
HELP_AT = 0.6  # "would a neighbour unblock this?" probability at or above this = ask for help
STATE_CHARS = 2000  # most text we send per field (Jev reads the state, it doesn't need all of it)

# The moods of docs/protocol.md, described for the "how do they feel" question.
MOODS = {
    "happy": "pleased: the step went well",
    "focused": "calm and concentrating; nothing special happened",
    "confused": "unsure what was wanted; had to guess or ask",
    "frustrated": "annoyed: things kept failing or got sent back",
    "tired": "worn out: ran out of time, budget, or patience",
    "proud": "delighted: finished something hard or important",
}



def natural_job(building_type: str) -> str | None:
    """The job that normally works at this building type (residents.JOB_BUILDINGS), the first helper we look for."""
    from mii.residents import JOB_BUILDINGS  # here, not at the top: residents.py imports the engine, which imports us

    return next((job for job, buildings in JOB_BUILDINGS.items() if building_type in buildings), None)


@dataclass
class HelpRequest:
    """A resident asking for a hand at a building."""

    asker: Resident
    building: Building
    task: Task
    text: str
    friendship: dict[str, float]  # candidate resident id -> friendship score with the asker


class RuleJudge:
    """The rules the engine used before there was a judge. Free, offline, deterministic."""

    name = "rules"

    async def mood(self, resident: Resident, building: Building, result: StepResult, attempt: int,
                   timed_out: bool, bubbles_raised: int) -> str:
        if timed_out:
            return "tired"
        return "happy" if result.outcome == "pass" else "frustrated"

    async def verdict(self, job: AgentJob, result: StepResult) -> StepResult:
        """No VERDICT line: the rules trust the agent (a pass), as parse_verdict always did."""
        return result

    async def flaky(self, test_output: str, diff_stat: str) -> float:
        return 0.0

    async def tip(self, stats: dict, candidates: dict[str, str]) -> str:
        """The first candidate: insights.tip_candidates lists them in the old first-match order."""
        return next(iter(candidates.values()))

    async def needs_help(self, job: AgentJob, result: StepResult) -> bool:
        """Stuck = failed again at the same building, or failed after a 💦 bubble."""
        return result.outcome == "fail" and (job.attempt >= 2 or job.struggled)

    async def pick_helper(self, request: HelpRequest, candidates: list[Resident]) -> Resident | None:
        """Someone whose job fits the building; else the asker's best friend; else nobody."""
        wanted = natural_job(request.building.type)
        for r in candidates:
            if r.job == wanted:
                return r
        friends = [r for r in candidates if request.friendship.get(r.id, 0.0) > 0]
        if friends:
            return max(friends, key=lambda r: request.friendship[r.id])
        return None


class JevJudge(RuleJudge):
    """Asks Jev, one HTTPS call per decision. Any trouble -> the RuleJudge answer."""

    name = "jev"

    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.http = http  # tests inject a client with a MockTransport
        self._warned = False

    async def _ask(self, state: dict, questions: dict) -> dict | None:
        """POST one state + questions. Returns the `answers` dict, or None when Jev can't be used."""
        http = self.http or httpx.AsyncClient(timeout=TIMEOUT)
        try:
            response = await http.post(
                API_URL, json={"model": MODEL, "state": state, "questions": questions},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            answers = response.json()["answers"]
            if not isinstance(answers, dict):
                raise ValueError("answers is not an object")
            self._warned = False
            return answers
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            if not self._warned:  # once, not on every step
                log.warning("Jev is unavailable (%s); using the rule-based judge until it is back", e)
                self._warned = True
            else:
                log.debug("Jev call failed: %s", e)
            return None
        finally:
            if self.http is None:
                await http.aclose()

    async def mood(self, resident, building, result, attempt, timed_out, bubbles_raised):
        state = {
            "resident": {"name": resident.name, "job": resident.job, "bluntness": resident.bluntness,
                         "caution": resident.caution, "chattiness": resident.chattiness, "creativity": resident.creativity},
            "building": {"name": building.name, "type": building.type},
            "step": {"outcome": result.outcome, "attempt": attempt, "timed_out": timed_out,
                     "bubbles_raised": bubbles_raised, "summary": _tail(result.summary)},
        }
        answers = await self._ask(state, {"mood": _choice("How does this resident feel right after this step?", MOODS)})
        choice = _choice_of(answers, "mood")
        if choice in MOODS:
            return choice
        return await super().mood(resident, building, result, attempt, timed_out, bubbles_raised)

    async def verdict(self, job, result):
        state = {"instructions": _tail(job.instructions), "reply": _tail(result.summary)}
        question = _noul("Did the agent complete what the instructions asked for, with no unresolved failure?")
        done = _noul_of(await self._ask(state, {"done": question}), "done")
        if done is None:
            return await super().verdict(job, result)
        outcome = "fail" if done < VERDICT_FAIL_BELOW else "pass"
        return replace(result, outcome=outcome, summary=f"{result.summary} (no verdict line; the judge said {outcome})".strip())

    async def flaky(self, test_output, diff_stat):
        state = {"test_output": _tail(test_output), "changes": _tail(diff_stat)}
        question = _noul("Is this test failure flaky or environmental (timeouts, network, ports, ordering, "
                         "missing tools) rather than caused by the code changes?")
        value = _noul_of(await self._ask(state, {"flaky": question}), "flaky")
        return value if value is not None else await super().flaky(test_output, diff_stat)

    async def tip(self, stats, candidates):
        if len(candidates) <= 1:
            return await super().tip(stats, candidates)
        question = _choice("Which suggestion is the most useful for the island owner, given these numbers?", candidates)
        choice = _choice_of(await self._ask({"stats": stats}, {"tip": question}), "tip")
        return candidates[choice] if choice in candidates else await super().tip(stats, candidates)

    async def needs_help(self, job, result):
        if result.outcome != "fail":
            return False
        state = {"instructions": _tail(job.instructions), "reply": _tail(result.summary),
                 "attempt": job.attempt, "struggled": job.struggled}
        question = _noul("Would a teammate coming over to help likely get this unblocked?")
        value = _noul_of(await self._ask(state, {"help": question}), "help")
        return value >= HELP_AT if value is not None else await super().needs_help(job, result)

    async def pick_helper(self, request, candidates):
        if not candidates:
            return None
        criteria = {
            r.id: f"{r.name}: {r.job}, {r.state}, friendship with {request.asker.name} {request.friendship.get(r.id, 0.0):.2f}"
            for r in candidates
        }
        criteria["nobody"] = "nobody fits; leave it"
        state = {
            "request": request.text, "task": request.task.title,
            "building": {"name": request.building.name, "type": request.building.type},
            "asker": {"name": request.asker.name, "job": request.asker.job},
        }
        question = _choice("Who should come over and help at this building? Prefer a job that fits the building.", criteria)
        choice = _choice_of(await self._ask(state, {"helper": question}), "helper")
        if choice == "nobody":
            return None
        for r in candidates:
            if r.id == choice:
                return r
        return await super().pick_helper(request, candidates)


# --- Small helpers ------------------------------------------------------------------


def _choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def _noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def _choice_of(answers: dict | None, key: str) -> str | None:
    try:
        return str(answers[key]["choice"])
    except (TypeError, KeyError):
        return None


def _noul_of(answers: dict | None, key: str) -> float | None:
    try:
        return min(max(float(answers[key]["noul"]), 0.0), 1.0)
    except (TypeError, KeyError, ValueError):
        return None


def _tail(text: str | None, chars: int = STATE_CHARS) -> str:
    text = text or ""
    return text if len(text) <= chars else "…" + text[-chars:]
