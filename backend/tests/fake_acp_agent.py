"""A tiny ACP agent for tests (run as a subprocess by tests/test_acp.py).

It behaves like a real coding agent in miniature: streams a message, reports a
tool call, asks for permission, reads a file inside the worktree and one
outside it (which the client must refuse), writes hello.txt, and ends with a
VERDICT line. `--needs-login` makes session/new fail with "auth required".
"""

import asyncio
import sys
from typing import Any
from uuid import uuid4

from acp import Agent, InitializeResponse, NewSessionResponse, PromptResponse, RequestError, run_agent
from acp.interfaces import Client
from acp.schema import AgentMessageChunk, PermissionOption, TextContentBlock, ToolCallStart, ToolCallUpdate


class FakeAgent(Agent):
    _conn: Client

    def __init__(self, needs_login: bool):
        self.needs_login = needs_login

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(self, cwd: str, **kwargs: Any) -> NewSessionResponse:
        if self.needs_login:
            raise RequestError.auth_required()
        self.cwd = cwd
        return NewSessionResponse(session_id=uuid4().hex)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        pass

    async def prompt(self, session_id: str, prompt: list, **kwargs: Any) -> PromptResponse:
        text = " ".join(getattr(block, "text", "") for block in prompt)

        async def say(line: str) -> None:
            await self._conn.session_update(session_id=session_id, update=AgentMessageChunk(session_update="agent_message_chunk", content=TextContentBlock(type="text", text=line)))

        await say("Looking at the code. ")
        await self._conn.session_update(
            session_id=session_id, update=ToolCallStart(session_update="tool_call", tool_call_id="tc1", title="Reading hello.py", kind="read")
        )
        inside = await self._conn.read_text_file(session_id=session_id, path=f"{self.cwd}/hello.py")
        await say(f"hello.py has {len(inside.content)} chars. ")

        try:
            await self._conn.read_text_file(session_id=session_id, path="/etc/passwd")
            await say("OUTSIDE READ ALLOWED. ")
        except RequestError:
            await say("outside read refused. ")

        answer = await self._conn.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(tool_call_id="tc2", title="Run `npm test`", kind="execute"),
            options=[
                PermissionOption(option_id="allow", name="Allow once", kind="allow_once"),
                PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
            ],
        )
        await say(f"permission outcome: {answer.outcome.outcome}. ")

        try:
            await self._conn.write_text_file(session_id=session_id, path=f"{self.cwd}/hello.txt", content="hi\n")
            await say("wrote hello.txt. ")
        except RequestError as e:
            await say(f"write refused: {e}. ")

        await say("VERDICT: FAIL" if "please fail" in text else "VERDICT: PASS")
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(FakeAgent(needs_login="--needs-login" in sys.argv)))
