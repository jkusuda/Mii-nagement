"""MockRunner: fakes agent work for free, so the whole app runs without any agents."""

import asyncio
import random

from mii.agents import AgentJob, StepResult


class MockRunner:
    """Sleeps, reports a little activity, sometimes raises a bubble, returns a random outcome."""

    # Chance of failing on the first visit, by building type. It halves on each
    # retry, so packages don't bounce forever.
    FAIL_CHANCE = {"lab": 0.4, "courthouse": 0.3}

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
        "permission": ["May I run `npm install`?", "Okay to delete the old helper file?"],
        "tired": ["I've used a lot of my budget on this one. Keep going?"],
        "ready": ["The PR description is ready. Want to take a look?"],
    }

    def __init__(self, work_seconds=(1.5, 4.0), bubble_chance=0.25, rng: random.Random | None = None):
        self.work_seconds = work_seconds
        self.bubble_chance = bubble_chance
        self.rng = rng or random.Random()

    async def run(self, job: AgentJob) -> StepResult:
        await job.report(self.ACTIVITY.get(job.building.type, "Working"))
        await asyncio.sleep(self.rng.uniform(*self.work_seconds))

        note = ""
        if self.rng.random() < self.bubble_chance:
            if job.building.type == "post_office":
                kind = "ready"
            else:
                kind = self.rng.choice(["confused", "struggling", "permission", "tired"])
            options = ["Allow", "Deny"] if kind == "permission" else None
            answer = await job.ask(kind, self.rng.choice(self.BUBBLE_LINES[kind]), options)
            note = f" You said: {answer!r}." if answer else " Nobody answered, so I guessed."

        fail_chance = self.FAIL_CHANCE.get(job.building.type, 0.0) / job.attempt
        if self.rng.random() < fail_chance:
            return StepResult("fail", f"(mock) Found a problem at the {job.building.name}.{note}")
        return StepResult("pass", f"(mock) Done at the {job.building.name}.{note}")
