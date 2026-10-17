"""A tiny ACP agent for tests (run as a subprocess by tests/test_acp.py).

It behaves like a real coding agent in miniature: streams a message, reports a
tool call, asks for permission, reads a file inside the worktree and one
outside it (which the client must refuse), writes hello.txt, and ends with a
VERDICT line. `--needs-login` makes session/new fail with "auth required".
A prompt that says it is the "same session as before" (a resident's later visit)
gets a short reply saying how many prompts this session has had, and
"please hang" makes a prompt hang. Each start adds a line to agent.starts.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from acp import Agent, InitializeResponse, NewSessionResponse, PromptResponse, RequestError, run_agent
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    FileEditToolCallContent,
    PermissionOption,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
)


class FakeAgent(Agent):
    _conn: Client

    def __init__(self, needs_login: bool, hang: bool = False, login_at_prompt: bool = False):
        self.hang = hang
        self.login_at_prompt = login_at_prompt
        self.needs_login = needs_login
        self.prompts: dict[str, int] = {}  # session id -> prompts so far

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        Path("agent.pid").write_text(str(os.getpid()))  # in the cwd the runner gave us (the worktree)
        with open("agent.starts", "a") as starts:
            starts.write(f"{os.getpid()}\n")
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(self, cwd: str, mcp_servers=None, **kwargs: Any) -> NewSessionResponse:
        if self.needs_login:
            raise RequestError.auth_required()
        self.cwd = cwd
        self.gifts = [s.name for s in mcp_servers or []]
        # The models it offers, like Claude Code's config option (one plain, two in a group).
        model = SessionConfigOptionSelect(
            type="select",
            id="model",
            name="Model",
            category="model",
            current_value="default",
            options=[
                SessionConfigSelectGroup(
                    group="claude",
                    name="Claude",
                    options=[
                        SessionConfigSelectOption(value="default", name="Default (Sonnet)"),
                        SessionConfigSelectOption(value="claude-haiku-4-5", name="Haiku", description="Fast"),
                    ],
                )
            ],
        )
        self.model = model
        return NewSessionResponse(session_id=uuid4().hex, config_options=[model])

    async def set_config_option(self, config_id: str, session_id: str, value: Any, **kwargs: Any):
        self.model.current_value = value
        return SetSessionConfigOptionResponse(config_options=[self.model])

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        pass

    async def prompt(self, session_id: str, prompt: list, **kwargs: Any) -> PromptResponse:
        if self.login_at_prompt:
            raise RequestError.auth_required()  # like Claude Code: the session opens, the prompt fails
        if os.environ.get("ANTHROPIC_API_KEY") == "":
            raise RequestError.internal_error({"details": "got a blank ANTHROPIC_API_KEY"})
        text = " ".join(getattr(block, "text", "") for block in prompt)
        if self.hang or "please hang" in text:
            await asyncio.sleep(120)  # a stuck agent: the runner's caller times out and must end us
        self.prompts[session_id] = self.prompts.get(session_id, 0) + 1

        async def say(line: str) -> None:
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk", content=TextContentBlock(type="text", text=line)
                ),
            )

        if "same session as before" in text:  # a resident's later visit: no re-reading needed
            await say(f"Resumed session {session_id[:6]} (prompt {self.prompts[session_id]}).\nVERDICT: PASS")
            return PromptResponse(stop_reason="end_turn")

        # `please ask`: stop to ask the island owner first; the runner prompts again with the answer.
        if "please ask" in text and "island owner answered" not in text and "Nobody answered" not in text:
            await say("I'm not sure which file you mean.\nQUESTION: Should I change hello.py or a new file?")
            return PromptResponse(stop_reason="end_turn")
        if "island owner answered" in text or "Nobody answered" in text:
            await say(f"Thanks! Going with: {text.strip()[:80]}\nVERDICT: PASS")
            return PromptResponse(stop_reason="end_turn")

        await self._conn.session_update(
            session_id=session_id,
            update=AgentThoughtChunk(
                session_update="agent_thought_chunk", content=TextContentBlock(type="text", text="Hmm, hello.py first.")
            ),
        )
        await say(f"Looking at the code (gifts: {','.join(self.gifts) or 'none'}). ")
        await self._conn.session_update(
            session_id=session_id, update=UsageUpdate(session_update="usage_update", used=250, size=1000)
        )
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallStart(session_update="tool_call", tool_call_id="tc1", title="Reading hello.py", kind="read"),
        )
        # Like Claude Code: the details (here a diff) arrive in a later update of the same call.
        diff = FileEditToolCallContent(type="diff", path="hello.py", old_text="a\n", new_text="b\n")
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallProgress(session_update="tool_call_update", tool_call_id="tc1", content=[diff]),
        )
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallProgress(session_update="tool_call_update", tool_call_id="tc1", status="completed"),
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
            tool_call=ToolCallUpdate(
                tool_call_id="tc2", title="Run `npm test`", kind="execute", raw_input={"command": "npm test"}
            ),
            options=[
                PermissionOption(option_id="allow", name="Allow once", kind="allow_once"),
                PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
            ],
        )
        await say(f"permission outcome: {answer.outcome.outcome}. ")
        edit = await self._conn.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(tool_call_id="tc3", title="Edit hello.py", kind="edit"),
            options=[
                PermissionOption(option_id="allow", name="Allow once", kind="allow_once"),
                PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
            ],
        )
        await say(f"edit permission: {getattr(edit.outcome, 'option_id', edit.outcome.outcome)}. ")

        try:
            await self._conn.write_text_file(session_id=session_id, path=f"{self.cwd}/hello.txt", content="hi\n")
            await say("wrote hello.txt. ")
        except RequestError as e:
            await say(f"write refused: {e}. ")

        await say("VERDICT: FAIL" if "please fail" in text else "VERDICT: PASS")
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(
        run_agent(
            FakeAgent(
                needs_login="--needs-login" in sys.argv,
                hang="--hang" in sys.argv,
                login_at_prompt="--login-at-prompt" in sys.argv,
            )
        )
    )
