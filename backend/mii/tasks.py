"""Turn a task request into a queued task (POST /islands/{id}/tasks and the `create_task` message).

The request is `{"title": ..., "description": ...}` or `{"issue": 42}` (a GitHub
issue of the island's repo, fetched through `gh`). The engine queues the task and
emits the `task` event; the package starts at the island's mailbox.
"""

import asyncio
from typing import TYPE_CHECKING

from mii import github
from mii.models import Island, Task

if TYPE_CHECKING:
    from mii.engine import WorkflowEngine


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
    return await workflow.submit(island.id, title[:500], description, issue_number=issue)
