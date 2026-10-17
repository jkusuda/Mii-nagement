"""The judge: small typed decisions the engine asks about a step.

Three judges with the same methods, so the engine never cares which one it has:

    RuleJudge    plain Python rules. The default; never touches the network.
    JevJudge     asks TypeSafe's Jev model, a fast classifier that answers typed questions
                 (pick one option, a score, or a yes/no probability) about a state dict in
                 about 100-500 ms, and falls back to RuleJudge on any error or timeout.
    GeminiJudge  asks the same questions to Gemini with structured output (a JSON schema).

The two model judges share AskingJudge: it builds the questions and reads the answers;
each subclass only implements `_ask` (the contract is in AskingJudge's docstring).

The judge never does the work and never decides permissions (house keys and ✋ bubbles
are the safety story; a probability is not). It decides flavour and small calls:

    mood         how a resident feels right after a step (the `mood` event)
    verdict      pass or fail when an agent forgot its VERDICT line
    flaky        does a Lab failure look flaky, so the tests get one more run
    tip          which Insights tip fits the numbers
    needs_help   is a package bouncing back enough that a neighbour should take the next turn
    pick_helper  which free neighbour should come

The `judge` setting picks one (runtime.reload()): "off" = rules; "jev" = Jev when a
TypeSafe key is set; "gemini" = Gemini when a Gemini key is set; "auto" (the default) = Jev,
else Gemini, else rules. `judge_model` is the Gemini model.

API: POST https://api.typesafe.ai/v1/systemone with
{"model", "state", "questions": {id: {"type": "choice"|"score"|"noul", "instructions", "criteria"?}}}
-> {"answers": {id: {"type", "choice"|"score"|"noul", "confidence"?, "probabilities"?}}}.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, replace

import httpx
from google import genai

from mii.agents import AgentJob, StepResult
from mii.models import Building, Resident, Task
from mii.vocab import JOB_BUILDINGS

log = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT = 4.0  # seconds; past this we use the rule answer
GEMINI_MODEL = "gemini-2.5-flash"  # the default `judge_model`
GEMINI_TIMEOUT = 6.0  # Gemini takes 0.5-2 s per decision
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
    """The job that normally works at this building type, the first helper we look for."""
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
    """Fixed rules. Free, offline, deterministic."""

    name = "rules"

    async def aclose(self) -> None:
        """Model judges close their connection here (runtime.reload swaps judges)."""

    async def mood(
        self,
        resident: Resident,
        building: Building,
        result: StepResult,
        attempt: int,
        timed_out: bool,
        bubbles_raised: int,
    ) -> str:
        if timed_out:
            return "tired"
        return "happy" if result.outcome == "pass" else "frustrated"

    async def verdict(self, job: AgentJob, result: StepResult) -> StepResult:
        """No VERDICT line: the rules trust the agent (a pass)."""
        return result

    async def flaky(self, test_output: str, diff_stat: str) -> float:
        return 0.0

    async def tip(self, stats: dict, candidates: dict[str, str]) -> str:
        """The first candidate: insights.tip_candidates lists them best rule first."""
        return next(iter(candidates.values()))

    async def needs_help(self, building: Building, attempt: int, last_note: str) -> bool:
        """A package is back at this building for the `attempt`-th time (`last_note`: what sent it
        back). The owner gets one retry of their own; from the third visit a neighbour takes over."""
        return attempt >= 3

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


class AskingJudge(RuleJudge):
    """A judge that asks a model typed questions. Subclasses implement `_ask`; each method here
    builds a state + questions, reads the answer, and falls back to the RuleJudge answer.

    The `_ask` contract (Jev's shape): questions {id: {"type": "choice"|"noul", "instructions",
    "criteria"?}} -> {id: {"choice": key}} or {id: {"noul": 0..1}}, or None when the model
    can't be used (warn once through `_unavailable`, then the rules take over).
    """

    label = "the model"  # for the log
    _warned = False

    async def _ask(self, state: dict, questions: dict) -> dict | None:
        raise NotImplementedError

    def _unavailable(self, error: Exception) -> None:
        if not self._warned:  # once, not on every step
            log.warning("%s is unavailable (%s); using the rule-based judge until it is back", self.label, error)
            self._warned = True
        else:
            log.debug("%s call failed: %s", self.label, error)

    async def mood(self, resident, building, result, attempt, timed_out, bubbles_raised):
        state = {
            "resident": {
                "name": resident.name,
                "job": resident.job,
                "bluntness": resident.bluntness,
                "caution": resident.caution,
                "chattiness": resident.chattiness,
                "creativity": resident.creativity,
            },
            "building": {"name": building.name, "type": building.type},
            "step": {
                "outcome": result.outcome,
                "attempt": attempt,
                "timed_out": timed_out,
                "bubbles_raised": bubbles_raised,
                "summary": _tail(result.summary),
            },
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
        return replace(
            result, outcome=outcome, summary=f"{result.summary} (no verdict line; the judge said {outcome})".strip()
        )

    async def flaky(self, test_output, diff_stat):
        state = {"test_output": _tail(test_output), "changes": _tail(diff_stat)}
        question = _noul(
            "Is this test failure flaky or environmental (timeouts, network, ports, ordering, "
            "missing tools) rather than caused by the code changes?"
        )
        value = _noul_of(await self._ask(state, {"flaky": question}), "flaky")
        return value if value is not None else await super().flaky(test_output, diff_stat)

    async def tip(self, stats, candidates):
        if len(candidates) <= 1:
            return await super().tip(stats, candidates)
        question = _choice("Which suggestion is the most useful for the island owner, given these numbers?", candidates)
        choice = _choice_of(await self._ask({"stats": stats}, {"tip": question}), "tip")
        return candidates[choice] if choice in candidates else await super().tip(stats, candidates)

    async def needs_help(self, building, attempt, last_note):
        state = {
            "building": {"name": building.name, "type": building.type},
            "visit": attempt,
            "what_sent_it_back": _tail(last_note),
        }
        question = _noul(
            "The same resident has failed to get this package past the next check; would a "
            "different teammate taking this turn likely get it unblocked?"
        )
        value = _noul_of(await self._ask(state, {"help": question}), "help")
        return value >= HELP_AT if value is not None else await super().needs_help(building, attempt, last_note)

    async def pick_helper(self, request, candidates):
        if not candidates:
            return None
        asker = request.asker.name
        criteria = {
            r.id: f"{r.name}: {r.job}, {r.state}, friendship with {asker} {request.friendship.get(r.id, 0.0):.2f}"
            for r in candidates
        }
        criteria["nobody"] = "nobody fits; leave it"
        state = {
            "request": request.text,
            "task": request.task.title,
            "building": {"name": request.building.name, "type": request.building.type},
            "asker": {"name": request.asker.name, "job": request.asker.job},
        }
        question = _choice(
            "Who should come over and help at this building? Prefer a job that fits the building.", criteria
        )
        choice = _choice_of(await self._ask(state, {"helper": question}), "helper")
        if choice == "nobody":
            return None
        for r in candidates:
            if r.id == choice:
                return r
        return await super().pick_helper(request, candidates)


class JevJudge(AskingJudge):
    """Asks Jev, one HTTPS call per decision. Any trouble -> the RuleJudge answer."""

    name = "jev"
    label = "Jev"

    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT)  # one connection, reused (tests inject a MockTransport)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _ask(self, state: dict, questions: dict) -> dict | None:
        """POST one state + questions. Returns the `answers` dict, or None when Jev can't be used."""
        try:
            response = await self.http.post(
                API_URL,
                json={"model": MODEL, "state": state, "questions": questions},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            answers = response.json()["answers"]
            if not isinstance(answers, dict):
                raise ValueError("answers is not an object")
            self._warned = False
            return answers
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self._unavailable(e)
            return None


class GeminiJudge(AskingJudge):
    """Asks Gemini through structured output: a JSON schema with one property per question
    (an enum over the choice keys, or a number for a probability). Slower than Jev
    (0.5-2 s), so the timeout is longer. Any trouble -> the RuleJudge answer."""

    name = "gemini"
    label = "Gemini"

    def __init__(self, api_key: str, model: str = GEMINI_MODEL, client=None):
        self.model = model
        self.client = client or genai.Client(api_key=api_key)  # tests inject a fake client

    async def aclose(self) -> None:
        close = getattr(self.client.aio, "aclose", None)
        if close is not None:
            await close()

    async def _ask(self, state: dict, questions: dict) -> dict | None:
        properties = {}
        lines = []
        for key, q in questions.items():
            if q["type"] == "choice":
                properties[key] = {"type": "STRING", "enum": list(q["criteria"])}
                options = "; ".join(f"{option} = {meaning}" for option, meaning in q["criteria"].items())
                lines.append(f"- {key}: {q['instructions']} Answer with one of: {options}.")
            else:
                properties[key] = {"type": "NUMBER"}
                lines.append(f"- {key}: {q['instructions']} Answer with a probability from 0 to 1.")
        prompt = (
            "You judge small decisions in a game about a team of coding agents. Read the state and "
            "answer each question.\n\nState:\n"
            + json.dumps(state, ensure_ascii=False)
            + "\n\nQuestions:\n"
            + "\n".join(lines)
        )
        config = {
            "temperature": 0,
            "response_mime_type": "application/json",
            "response_schema": {"type": "OBJECT", "properties": properties, "required": list(properties)},
        }
        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(model=self.model, contents=prompt, config=config),
                GEMINI_TIMEOUT,
            )
            raw = json.loads(response.text)
            if not isinstance(raw, dict):
                raise ValueError("the answer is not an object")
            answers = {}
            for key, q in questions.items():
                if key not in raw:
                    continue
                if q["type"] == "choice":
                    answers[key] = {"choice": str(raw[key])}
                else:
                    answers[key] = {"noul": min(max(float(raw[key]), 0.0), 1.0)}
            self._warned = False
            return answers
        except Exception as e:  # network, quota, timeout, malformed JSON: the rules answer instead
            self._unavailable(e)
            return None


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
