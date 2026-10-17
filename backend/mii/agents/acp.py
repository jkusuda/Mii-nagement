"""AcpRunner: runs a resident as a real coding agent over the Agent Client Protocol.

ACP (https://agentclientprotocol.com) is an open, JSON-RPC-over-stdio protocol
between a *client* (us) and a coding *agent* (Claude Code, Codex, Gemini CLI).
We start the agent as a child process, send it a prompt, and it streams back
progress, asks permission for risky actions, and can ask us to read/write files.

Each brain authenticates the way its CLI already does: the user's subscription
login, or an API key in the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY /
GEMINI_API_KEY), which the child process inherits.

Python SDK: `agent-client-protocol` on PyPI (https://agentclientprotocol.github.io/python-sdk/).
TODO(mvp): `uv add agent-client-protocol`, then read its client docs before building run().
"""

import shutil

from mii.agents import AgentJob, StepResult
from mii.sandbox import Sandbox

# brain -> how to start its ACP agent, and which API key it can use instead of a login.
# Adding another ACP agent = one more entry here.
BRAINS = {
    "claude-code": {
        "command": ["npx", "-y", "@zed-industries/claude-code-acp"],  # Zed's adapter for Claude Code
        "needs": "claude",  # CLI that must be installed and logged in (or use the API key)
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "codex": {
        "command": ["npx", "-y", "@zed-industries/codex-acp"],  # Zed's adapter for Codex
        "needs": "codex",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gemini": {
        "command": ["gemini", "--acp"],  # Gemini CLI speaks ACP natively
        "needs": "gemini",
        "api_key_env": "GEMINI_API_KEY",
    },
}


def available_brains() -> dict[str, bool]:
    """Which brains can run on this machine (their CLI is installed). Used by GET /brains."""
    # TODO(mvp): also check that the CLI is logged in (or its API key is set), and that npx exists.
    return {"mock": True} | {brain: shutil.which(spec["needs"]) is not None for brain, spec in BRAINS.items()}


class AcpRunner:
    """One brain's runner. A fresh agent process per step, so nothing leaks between steps."""

    def __init__(self, brain: str, sandbox: Sandbox | None):
        self.brain = brain
        self.spec = BRAINS[brain]
        self.sandbox = sandbox

    async def run(self, job: AgentJob) -> StepResult:
        # TODO(mvp): build the ACP client. The flow for one step:
        #   1. sandbox.check_workdir(job.workdir), then start self.spec["command"] as a child
        #      process with cwd=job.workdir. Turn on the agent's own safety setting
        #      (Claude Code: ask before acting; Codex: workspace-write sandbox; Gemini: --sandbox).
        #   2. ACP handshake: `initialize` (advertise fs read/write support), then `session/new`
        #      with cwd=job.workdir. Set job.resident.model if the agent supports choosing one.
        #   3. `session/prompt` with: build_system_prompt(job.resident) + job.instructions +
        #      job.history (ACP has no system-prompt field, so personality goes first).
        #   4. While it runs, handle what the agent sends:
        #        - session/update (progress)      -> await job.report("Reading auth.py")
        #        - session/request_permission     -> await job.ask("permission", text, option_names)
        #                                            and reply with the chosen option
        #        - fs/read_text_file, fs/write_text_file -> only if sandbox.is_inside(path, job.workdir)
        #        - usage limit / auth error       -> job.ask("tired", ...) and fail the step
        #   5. When the prompt finishes, read the final text: "VERDICT: FAIL" -> fail, else pass.
        #   6. Always close the process (the engine's step timeout cancels us if it hangs).
        return StepResult("fail", f"The {self.brain} brain isn't built yet (agents/acp.py).")
