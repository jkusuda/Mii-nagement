"""Database tables (Django models) + converting rows to protocol dicts.

Each class is one SQLite table, each instance one row. Django creates and
updates the tables through migrations:

    uv run python manage.py makemigrations mii   # after changing a model
    uv run python manage.py migrate              # apply (the server also does this at startup)

The engine is async, so it uses Django's async ORM methods (`aget`, `asave`,
`acreate`, `async for`). Table models don't validate values: residents.py checks
what the game sends, and personality.py clamps the sliders when it reads them.
"""

from datetime import UTC, datetime

from django.db import models

from mii.island_map import default_map


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- Tables -----------------------------------------------------------------

# House keys: ACP tool kind group -> "yes" | "ask" | "no" (docs/protocol.md; KEY_FOR_KIND in
# agents/acp.py). `tools` covers the gifts' MCP tools. `master` ("yes" | "no") is the resident's
# own master key: every door opens and nothing is ever asked (permissions, reviews, questions);
# the other keys are ignored while it is "yes". The game's copy: HOUSE_KEYS in resident_data.gd.
DEFAULT_KEYS = {"master": "no", "read": "yes", "edit": "ask", "execute": "ask", "fetch": "no", "tools": "ask"}


def default_keys() -> dict:
    return dict(DEFAULT_KEYS)


class Island(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    name = models.CharField(max_length=200)
    repo = models.CharField(max_length=200, null=True, blank=True)  # GitHub "owner/name"
    repo_path = models.CharField(max_length=1000, null=True, blank=True)  # the local git repo (see islands.py)
    branch = models.CharField(max_length=200, default="main")  # residents branch off from here
    plan = models.CharField(max_length=50, default="classic")  # the town plan its steps match, or "custom"
    # House rules (vocab.RULES), True = ask me first: merge_pr (Courthouse approval),
    # install_packages and shell (tighten the residents' house keys).
    rules = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=utcnow)  # "Day 1" in the game
    # Land tiles, pond, plants, season: see island_map.py. {} = the classic island.
    map = models.JSONField(default=dict)


class Resident(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="residents")
    name = models.CharField(max_length=100)
    job = models.CharField(max_length=50)  # planner | coder | reviewer

    # Personality sliders, each 0.0-1.0 (see personality.py)
    bluntness = models.FloatField(default=0.5)
    caution = models.FloatField(default=0.5)
    chattiness = models.FloatField(default=0.5)
    creativity = models.FloatField(default=0.5)

    brain = models.CharField(max_length=50)  # claude-code | codex | gemini | openrouter; no default
    model = models.CharField(max_length=200, null=True, blank=True)  # model override; None = the brain's default
    catchphrase = models.CharField(max_length=200, blank=True, default="")
    color = models.CharField(max_length=20, default="#FFFFFF")

    # Set in the resident creator (see docs/mii-customization.md):
    # keys = what the agent may do without asking (honoured by the ACP runner), gifts = extra MCP server ids.
    keys = models.JSONField(default=default_keys)
    gifts = models.JSONField(default=list)
    # How their Mii looks (skin, hair, eyes, ...). Only the game reads it; see MiiParts in the game.
    appearance = models.JSONField(default=dict, blank=True)

    # Live state, shown in the game
    state = models.CharField(max_length=20, default="idle")  # idle | walking | working | sleeping
    building = models.ForeignKey("Building", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    mood = models.CharField(max_length=20, default="happy")
    # How full the agent's context window is, 0.0-1.0 (from ACP usage updates). None = unknown.
    notebook = models.FloatField(null=True, blank=True)
    # The last `activity` line the agent reported, so a client that connects later still sees it.
    activity = models.TextField(blank=True, default="")
    # The diary: `REMEMBER:` lines from the resident's own replies, oldest first (engine.NOTES_CHARS cap).
    notes = models.TextField(blank=True, default="")


class Building(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="buildings")
    type = models.CharField(max_length=50)  # mailbox | workshop | lab | courthouse | post_office
    name = models.CharField(max_length=100)
    x = models.IntegerField()  # the top-left tile of its lot on the island grid (islands.py)
    y = models.IntegerField()
    facing = models.IntegerField(default=0)  # where the door looks: 0 south, 1 east, 2 north, 3 west
    resident = models.ForeignKey(Resident, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class Road(models.Model):
    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="roads")
    from_building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="+")
    to_building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="+")
    condition = models.CharField(max_length=20, default="always")  # always | pass | fail


class Task(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default="queued")  # queued | in_progress | done | failed | cancelled
    # Where the package is now, and who is carrying it.
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    agent = models.ForeignKey(Resident, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issue_number = models.IntegerField(null=True, blank=True)  # the GitHub issue it came from
    branch = models.CharField(max_length=200, null=True, blank=True)  # mii/<task_id>, in the task's worktree
    pr_url = models.CharField(max_length=500, null=True, blank=True)  # set by the post office
    merged_at = models.DateTimeField(null=True, blank=True)  # when the PR was merged (insights.py asks `gh`)
    created_at = models.DateTimeField(default=utcnow)
    finished_at = models.DateTimeField(null=True, blank=True)  # when it became done / failed


class Attachment(models.Model):
    """A file sent with a task (POST /islands/{id}/tasks `attachments`). Copied into the
    task's worktree when it starts (sandbox.Sandbox.add_attachments); sizes are capped in tasks.py."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    name = models.CharField(max_length=120)  # a plain file name, made safe by tasks.safe_file_name
    data = models.BinaryField()


class Step(models.Model):
    """One building visit by one resident. The source data for insights.py.

    Plain strings instead of foreign keys, so history survives residents and
    buildings being renamed or removed.
    """

    island_id = models.CharField(max_length=64, db_index=True)
    task_id = models.CharField(max_length=64, db_index=True)
    building_id = models.CharField(max_length=64)
    building_type = models.CharField(max_length=50)
    resident_id = models.CharField(max_length=64)
    brain = models.CharField(max_length=50)
    outcome = models.CharField(max_length=10, blank=True, default="")  # pass | fail
    summary = models.TextField(blank=True, default="")  # what the resident reported (plan, commit, review...)
    timed_out = models.BooleanField(default=False)
    bubbles_raised = models.IntegerField(default=0)
    helped = models.CharField(max_length=64, null=True, blank=True)  # a help visit: the resident this step helped
    started_at = models.DateTimeField(default=utcnow)
    finished_at = models.DateTimeField(null=True, blank=True)

    @property
    def seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class Friendship(models.Model):
    """The friendship meter: how often two residents hand packages to each other.

    One row per pair, with the two ids sorted so (a, b) and (b, a) are the same
    row. Plain strings, like Step, so history survives a resident leaving.
    """

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="friendships")
    a = models.CharField(max_length=64)
    b = models.CharField(max_length=64)
    handoffs = models.IntegerField(default=0)
    updated_at = models.DateTimeField(default=utcnow)

    class Meta:
        unique_together = [("island", "a", "b")]

    @property
    def score(self) -> float:
        """0.0-1.0: grows with every handoff, fast at first (5 → 0.56, 10 → 0.80, 20 → 0.96)."""
        return round(1 - 0.85**self.handoffs, 3)


class Setting(models.Model):
    """One user setting (see prefs.py). Secrets are never stored here."""

    key = models.CharField(primary_key=True, max_length=50)
    value = models.JSONField(null=True)


class BrainUsage(models.Model):
    """What residents used of one brain (agent service) on one day, for the usage pills
    (usage.py). Only what the agents report: tokens per turn, and the session's cost."""

    brain = models.CharField(max_length=50)
    day = models.DateField()
    turns = models.IntegerField(default=0)  # prompts answered
    tokens = models.BigIntegerField(default=0)  # 0 when the agent doesn't report them
    cost = models.FloatField(default=0.0)  # US dollars; 0 when the agent doesn't report it

    class Meta:
        unique_together = [("brain", "day")]


class Event(models.Model):
    """Every event sent to clients, kept for history (News, a task's events)."""

    island_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=50, db_index=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=utcnow)


# --- Converting rows to protocol dicts (see docs/protocol.md) ---------------


def resident_to_dict(r: Resident) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "job": r.job,
        "brain": r.brain,
        "model": r.model,
        "color": r.color,
        "catchphrase": r.catchphrase,
        "bluntness": r.bluntness,
        "caution": r.caution,
        "chattiness": r.chattiness,
        "creativity": r.creativity,
        "keys": {**DEFAULT_KEYS, **(r.keys or {})},  # residents saved before a key existed get its default
        "gifts": r.gifts,
        "appearance": r.appearance,
        "state": r.state,
        "building": r.building_id,
        "mood": r.mood,
        "notebook": r.notebook,
        "activity": r.activity,
    }


def island_to_dict(i: Island) -> dict:
    """The island's header fields: the top of the snapshot, the `island` event, the rows of GET /islands."""
    return {
        "id": i.id,
        "name": i.name,
        "repo": i.repo,
        "path": i.repo_path,
        "branch": i.branch,
        "plan": i.plan,
        "rules": i.rules,
        "created_at": i.created_at.isoformat(),
    }


def friendship_to_dict(f: Friendship) -> dict:
    return {"a": f.a, "b": f.b, "handoffs": f.handoffs, "score": f.score}


def task_to_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "building": t.building_id,
        "agent": t.agent_id,
        "pr_url": t.pr_url,
        "merged": t.merged_at is not None,  # its pull request was merged on GitHub
        "issue": t.issue_number,
    }


def task_detail_dict(t: Task, steps: list[Step]) -> dict:
    """A task for `GET /islands/{id}/tasks`: the protocol shape plus what only HTTP clients need."""
    return {
        **task_to_dict(t),
        "description": t.description,
        "branch": t.branch,
        "created_at": t.created_at.isoformat(),
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "steps": [step_to_dict(s) for s in steps],
    }


def step_to_dict(s: Step) -> dict:
    return {
        "building": s.building_id,
        "building_type": s.building_type,
        "resident": s.resident_id,
        "brain": s.brain,
        "outcome": s.outcome or None,  # None while the step is still running
        "summary": s.summary,
        "timed_out": s.timed_out,
        "bubbles_raised": s.bubbles_raised,
        "helped": s.helped,
        "started_at": s.started_at.isoformat(),
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        "seconds": s.seconds,
    }


def building_to_dict(b: Building) -> dict:
    return {
        "id": b.id,
        "type": b.type,
        "name": b.name,
        "x": b.x,
        "y": b.y,
        "facing": b.facing,
        "resident": b.resident_id,
    }


def road_to_dict(r: Road) -> dict:
    return {"from": r.from_building_id, "to": r.to_building_id, "condition": r.condition}


async def island_layout(island_id: str) -> dict:
    """The island's buildings and roads (the `layout` event, and part of the snapshot)."""
    return {
        "buildings": [building_to_dict(b) async for b in Building.objects.filter(island_id=island_id).order_by("id")],
        "roads": [road_to_dict(r) async for r in Road.objects.filter(island_id=island_id).order_by("id")],
    }


async def island_snapshot(island_id: str, open_bubbles: list[dict]) -> dict:
    """Full island state for the `snapshot` event. Raises Island.DoesNotExist."""
    from mii import weather  # here, not at the top: weather.py reads these tables

    island = await Island.objects.aget(pk=island_id)
    return {
        **island_to_dict(island),
        "server_time": utcnow().isoformat(),
        "weather": await weather.current(island_id),
        "map": island.map or default_map(),
        **await island_layout(island_id),
        "residents": [resident_to_dict(r) async for r in Resident.objects.filter(island_id=island_id).order_by("id")],
        "bubbles": open_bubbles,
        "tasks": [
            task_to_dict(t)
            async for t in Task.objects.filter(island_id=island_id, status__in=["queued", "in_progress"]).order_by(
                "created_at"
            )
        ],
        "friendships": [
            friendship_to_dict(f) async for f in Friendship.objects.filter(island_id=island_id).order_by("a", "b")
        ],
    }
