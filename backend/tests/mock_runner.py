"""MockRunner: a test fake that does pretend agent work (activity, bubbles, random outcomes)."""

import asyncio
import random

from mii.agents import AgentJob, StepResult, permission_for
from mii.gifts import GIFTS, key_for
from mii.vocab import ATTACHMENTS_DIR, SEND_BACK


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
    # What each permission line asks for (shown in the popup's dark box), and the house key it needs.
    PERMISSION_DETAILS = {
        "May I borrow the toolbox key? I'd like to install a package.": "npm install zod@3.23.8",
        "Okay to delete the old helper file?": "rm src/old_helpers.js",
    }
    PERMISSION_KEYS = {
        "May I borrow the toolbox key? I'd like to install a package.": "execute",
        "Okay to delete the old helper file?": "edit",
    }
    GIFT_CHANCE = 0.5  # how often a resident with gifts uses one during a step
    # Options per bubble kind. A "ready" answer may carry a note: "Send back: also test IPv6".
    OPTIONS = {"permission": ["Just this once", "Always", "Not now"], "ready": ["Approve", SEND_BACK]}

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

    # What a mock resident says when you talk to them at a building (talk.py). {topic} is
    # the start of what you said.
    TALK_LINES = {
        "mailbox": ["Ooh, a letter? I'd plan that as three small steps.", "About {topic}... I'd read it twice."],
        "workshop": ["I'd start with the smallest change that works.", "Hmm, {topic}? I'd poke at the code first."],
        "lab": ["If it isn't tested, it isn't done!", "I'd write a test for {topic} before anything else."],
        "courthouse": ["I'll be honest with you: keep it simple.", "On {topic}, I'd want a clear test, small diff."],
        "post_office": ["Short PR, clear title, happy reviewers.", "I'd write {topic} up in two lines and ship it."],
    }

    # Fake tool calls (title, sort, detail), so the dropdown's summary and details have something to show.
    TOOLS = {
        "mailbox": [
            ("Read README.md", "read", "README.md"),
            ('Search for "login"', "search", '{\n "pattern": "login",\n "path": "src"\n}'),
        ],
        "workshop": [
            ("Read src/app.py", "read", "src/app.py"),
            (
                "Edit src/app.py",
                "edit",
                "--- src/app.py\n+++ src/app.py\n@@ -1,2 +1,2 @@\n-print('helo')\n+print('hello')",
            ),
        ],
        "lab": [("Run the tests", "execute", "$ pytest -q")],
        "courthouse": [("Read the diff", "execute", "$ git diff --stat main")],
        "post_office": [("Read the diff", "execute", "$ git log --oneline -5")],
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
        if job.chat is not None:
            return await self._talk(job)
        await job.report(self.ACTIVITY.get(job.building.type, "Working"))
        if job.think:
            await job.think("thought", self.THOUGHTS.get(job.building.type, "Thinking it over. "))
            if job.building.type == "mailbox":  # the planner looks at what the owner attached
                for name in job.attachments:
                    path = f"{ATTACHMENTS_DIR}/{name}"
                    await job.think("tool", f"Read {path}", tool="read", detail=path)
            fallback = [(self.ACTIVITY.get(job.building.type, "Working"), "other", "")]
            for title, sort, detail in self.TOOLS.get(job.building.type, fallback):
                await job.think("tool", title, tool=sort, detail=detail)
        note = await self._use_a_gift(job)
        await asyncio.sleep(self.rng.uniform(*self.work_seconds))
        if job.think:
            await job.think("reply", "(mock) All done here. ")

        if self.rng.random() < self.bubble_chance:
            if job.building.type in ("courthouse", "post_office"):
                kind = "ready"
            else:
                kind = self.rng.choice(["confused", "struggling", "permission", "tired"])
            text = self.rng.choice(self.BUBBLE_LINES[kind])
            detail = self.PERMISSION_DETAILS.get(text)
            key = permission_for(job, self.PERMISSION_KEYS[text], detail or "") if kind == "permission" else "ask"
            if key == "yes":
                note += f" My house key let me `{detail}`."
            elif key == "no":
                note += f" My house key said no to `{detail}`, so I left it."
            else:
                answer = await job.ask(kind, text, self.OPTIONS.get(kind), detail)
                note += f" You said: {answer!r}." if answer else " Nobody answered, so I guessed."
                if kind == "ready" and answer and answer.startswith(SEND_BACK):
                    return StepResult("fail", f"(mock) Sent back from the {job.building.name}.{note}")

        fail_chance = self.FAIL_CHANCE.get(job.building.type, 0.0) / job.attempt
        if self.rng.random() < fail_chance:
            result = StepResult("fail", f"(mock) Found a problem at the {job.building.name}.{note}")
        else:
            result = StepResult("pass", f"(mock) Done at the {job.building.name}.{note}")
        return self._chatter(job, result)

    async def _use_a_gift(self, job: AgentJob) -> str:
        """Sometimes use one of the resident's gifts, as its house key allows (gifts.key_for). Returns a note."""
        gifts = list(getattr(job.resident, "gifts", None) or [])
        if not gifts or job.chat is not None or self.rng.random() >= self.GIFT_CHANCE:
            return ""
        gift = self.rng.choice(gifts)
        label = GIFTS.get(gift, {}).get("label", gift)
        key = permission_for(job, key_for(gift), gift)  # the Browser follows "Use the internet", ...
        if key == "ask":
            answer = await job.ask("permission", f"May I use my {label} gift?", self.OPTIONS["permission"], gift)
            key = "yes" if answer in ("Just this once", "Always") else "no"
        if key == "no":
            return f" Left my {label} gift alone."
        if job.think:
            await job.think("tool", f"{label}: look something up", tool="other", detail=f"(mock) the {gift} MCP server")
        return f" Used my {label} gift."

    async def _talk(self, job: AgentJob) -> StepResult:
        """A chat: a short pause, one tool line, an in-character answer streamed a few words
        at a time (like a real brain's reply). No bubbles, never fails."""
        if job.think:
            await job.think("tool", "Looking around the " + job.building.name)
        await asyncio.sleep(self.rng.uniform(0.4, 1.0))
        topic = " ".join(job.chat.split()[:5]).rstrip("?!.,") or "that"
        line = self.rng.choice(self.TALK_LINES.get(job.building.type, ["Hmm, {topic}? Let me think."]))
        reply = f"(mock) {line.format(topic=topic)}"
        if job.resident.catchphrase:
            reply += f" {job.resident.catchphrase}"
        if job.think:
            words = reply.split(" ")
            for i in range(0, len(words), 3):
                await job.think("reply", " ".join(words[i : i + 3]) + " ")
                await asyncio.sleep(0.08)
        return StepResult("pass", reply)

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
