"""Turn a task request into a queued task (POST /islands/{id}/tasks and the `create_task` message).

The request is `{"title": ..., "description": ...}` or `{"issue": 42}` (a GitHub
issue of the island's repo, fetched through `gh`), optionally with `attachments`:
`[{"name": "spec.md", "content": "<base64>"}]`. The engine queues the task and
emits the `task` event; the package starts at the island's mailbox.
"""

import asyncio
import base64
import binascii
import re
from typing import TYPE_CHECKING

from django.db.models import QuerySet

from mii import github
from mii.models import Event, Island, Task

if TYPE_CHECKING:
    from mii.engine import WorkflowEngine


MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024  # each file
MAX_ATTACHMENTS_TOTAL = 8 * 1024 * 1024  # all of a task's files together
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


class TaskError(ValueError):
    """The request can't become a task. `status` is the HTTP status a view should answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def submit_task(workflow: "WorkflowEngine", island: Island, data: object) -> Task:
    """Validate `data` and queue the task on `island`. Raises TaskError."""
    if not isinstance(data, dict):
        raise TaskError("the task must be a JSON object")
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "")
    issue = data.get("issue")
    if issue is not None:
        if not island.repo:
            raise TaskError("this island has no GitHub repo")
        try:
            issue = int(issue)
        except (ValueError, TypeError):
            raise TaskError("issue must be a number")
        try:
            title, description = await asyncio.to_thread(github.fetch_issue, island.repo, issue)
        except github.GitHubError as e:
            raise TaskError(str(e), 502)
    if not title:
        raise TaskError("give a title or an issue number")
    files = parse_attachments(data.get("attachments"))
    return await workflow.submit(island.id, title[:500], description, issue_number=issue, attachments=files)


def safe_file_name(name: str) -> str:
    """A plain file name for an attachment: no folders, no leading dots, only simple characters.
    "../../etc/passwd" -> "passwd", "my notes?.md" -> "my notes_.md". Empty when nothing is left."""
    base = re.split(r"[\\/]", name)[-1]
    return UNSAFE_NAME_CHARS.sub("_", base).lstrip(". ").strip()[:120]


def parse_attachments(raw: object) -> list[tuple[str, bytes]]:
    """Check a request's `attachments` and decode them: [(file name, bytes)]. Raises TaskError.
    Names are made safe and unique; sizes are capped (MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS_TOTAL)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TaskError("attachments must be a list of {name, content}")
    if len(raw) > MAX_ATTACHMENTS:
        raise TaskError(f"at most {MAX_ATTACHMENTS} attachments")
    files: list[tuple[str, bytes]] = []
    total = 0
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise TaskError("each attachment needs a name and base64 content")
        name = safe_file_name(str(item.get("name") or ""))
        if not name:
            raise TaskError(f"not a usable file name: {item.get('name')!r}")
        try:
            data = base64.b64decode(item["content"], validate=True)
        except (binascii.Error, ValueError):
            raise TaskError(f"{name}: content is not base64")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise TaskError(f"{name} is too big (at most {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB a file)")
        total += len(data)
        if total > MAX_ATTACHMENTS_TOTAL:
            raise TaskError(f"attachments are too big together (at most {MAX_ATTACHMENTS_TOTAL // (1024 * 1024)} MB)")
        taken = {n for n, _ in files}
        stem, dot, ext = name.rpartition(".") if "." in name else (name, "", "")
        n = 2
        while name in taken:
            name = f"{stem}-{n}{dot}{ext}"
            n += 1
        files.append((name, data))
    return files


async def task_window(task_id: str, running_too: bool = False) -> QuerySet | None:
    """The island's stored events from the task's `in_progress` event to its last `task` event.

    Events aren't tagged with a task, so this is everything the island sent in that window.
    `running_too` leaves the end open for a task that hasn't finished. None while it is queued.
    """
    marks = Event.objects.filter(type="task", payload__id=task_id).order_by("id")
    first = await marks.filter(payload__status="in_progress").afirst()
    if first is None:
        return None
    rows = Event.objects.filter(island_id=first.island_id, id__gte=first.id)
    last = await marks.alast()
    if last.payload.get("status") in ("done", "failed", "cancelled") or not running_too:
        rows = rows.filter(id__lte=last.id)
    return rows
