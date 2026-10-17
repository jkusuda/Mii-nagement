"""MockRunner: fakes agent work for free, so the whole app runs without any agents."""

import asyncio
import random

from mii.agents import AgentJob, StepResult
from mii.vocab import SEND_BACK


class MockRunner:
    """Sleeps, reports a little activity, sometimes raises a bubble, returns a random outcome."""

    # Chance of failing on the first visit, by building type. It halves on each
    # retry, so packages don't bounce forever.
    FAIL_CHANCE = {"workshop": 0.15, "lab": 0.4, "courthouse": 0.3}  # a Workshop fail may come with a HELP line

    ACTIVITY = {
        "mailbox": "Reading the request",
        "workshop": "Writing code",
        "lab": "Running the tests",
        "courthouse": "Reviewing the diff",
        "post_office": "Writing the PR description",
    }

    BUBBLE_LINES = {
        "confused": ["Should this handle empty input too?", "Which file is this about?"],
        "struggling": ["This test keeps failing and I'm not sure why...", "I've tried three fixes. Hint?"],
        "permission": [
            "May I borrow the toolbox key? I'd like to install a package.",
            "Okay to delete the old helper file?",
        ],
        "tired": ["I've used a lot of my budget on this one. Keep going?"],
        "ready": ["All clear! I checked it and it looks good to me. Want a look?"],
    }
    # What each permission line asks for, shown in the popup's dark box.
    PERMISSION_DETAILS = {
        "May I borrow the toolbox key? I'd like to install a package.": "npm install zod@3.23.8",
        "Okay to delete the old helper file?": "rm src/old_helpers.js",
    }
    # Options per bubble kind. A "ready" answer may carry a note: "Send back: also test IPv6".
    OPTIONS = {"permission": ["Allow once", "Always allow", "Reject"], "ready": ["Approve", SEND_BACK]}

    # Island life, for free: what a mock resident asks of, says to, or remembers about the others.
    HELP_LINES = ["I can't get these tests green. Can someone look?", "This plan doesn't match the code I see. Help?"]
    SAY_LINES = [
        "Heads up: I touched the config loader.",
        "Thanks for the plan, it helped!",
        "Your tests are slow, you know.",
    ]
    REMEMBER_LINES = [
        "This repo's tests are slow; run the small suite first.",
        "The owner likes short PR descriptions.",
    ]

    # Fake thoughts, so the game's "thinking" dropdown has something in mock mode too.
    THOUGHTS = {
        "mailbox": "Let me read the request carefully and split it into small steps. ",
        "workshop": "I'll start with the smallest change that could work, then tidy up. ",
        "lab": "Running the suite. If anything flakes I'll run it once more to be sure. ",
        "courthouse": "Checking the diff: naming, edge cases, and whether the tests cover it. ",
        "post_office": "Writing a short PR title and a description of what changed and why. ",
    }

    def __init__(
        self,
        work_seconds: tuple[float, float] = (1.5, 4.0),
        bubble_chance: float = 0.25,
        rng: random.Random | None = None,
    ):
        self.work_seconds = work_seconds
        self.bubble_chance = bubble_chance
        self.rng = rng or random.Random()

    async def run(self, job: AgentJob) -> StepResult:
        await job.report(self.ACTIVITY.get(job.building.type, "Working"))
        if job.think:
            await job.think("thought", self.THOUGHTS.get(job.building.type, "Thinking it over. "))
            await job.think("tool", self.ACTIVITY.get(job.building.type, "Working"))
        await asyncio.sleep(self.rng.uniform(*self.work_seconds))
        if job.think:
            await job.think("reply", "(mock) All done here. ")

        note = ""
        if self.rng.random() < self.bubble_chance:
            if job.building.type in ("courthouse", "post_office"):
                kind = "ready"
            else:
                kind = self.rng.choice(["confused", "struggling", "permission", "tired"])
            text = self.rng.choice(self.BUBBLE_LINES[kind])
            answer = await job.ask(kind, text, self.OPTIONS.get(kind), self.PERMISSION_DETAILS.get(text))
            note = f" You said: {answer!r}." if answer else " Nobody answered, so I guessed."
            if kind == "ready" and answer and answer.startswith(SEND_BACK):
                return StepResult("fail", f"(mock) Sent back from the {job.building.name}.{note}")

        fail_chance = self.FAIL_CHANCE.get(job.building.type, 0.0) / job.attempt
        if self.rng.random() < fail_chance:
            result = StepResult("fail", f"(mock) Found a problem at the {job.building.name}.{note}")
        else:
            result = StepResult("pass", f"(mock) Done at the {job.building.name}.{note}")
        return self._chatter(job, result)

    def _chatter(self, job: AgentJob, result: StepResult) -> StepResult:
        """Sometimes ask a neighbour for help (on a fail), leave a note, or write in the diary."""
        if result.outcome == "fail" and self.rng.random() < 0.5:
            result.help_request = self.rng.choice(self.HELP_LINES)
        if job.neighbours and self.rng.random() < 0.3:
            name = self.rng.choice(job.neighbours).split(" (")[0]
            result.says = [(name, self.rng.choice(self.SAY_LINES))]
        if self.rng.random() < 0.2:
            result.remember = [self.rng.choice(self.REMEMBER_LINES)]
        return result
