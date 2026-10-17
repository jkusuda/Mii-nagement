import json

import httpx
from conftest import RecordingAsk, RecordingReport, make_job

from mii.agents.openrouter import API_URL, OpenRouterRunner


def assistant(content=None, tool_calls=None) -> httpx.Response:
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": f"call{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(tool_calls)
        ]
    return httpx.Response(200, json={"choices": [{"message": message}]})


def make_runner(handler) -> OpenRouterRunner:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=API_URL)
    return OpenRouterRunner("sk-or-test", "test/model", http=http)


async def test_openrouter_runner_uses_tools_inside_the_worktree_only(sandbox):
    workdir = sandbox.create_worktree("t_test")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body["model"] == "test/model"
            assert request.headers["Authorization"] == "Bearer sk-or-test"
            return assistant(tool_calls=[("write_file", {"path": "hello.txt", "content": "hi\n"}),
                                         ("read_file", {"path": "/etc/passwd"}),
                                         ("list_files", {"glob": "*.py"})])
        return assistant(content="All done.\nVERDICT: PASS")

    report = RecordingReport()
    result = await make_runner(handler).run(make_job("openrouter", workdir=workdir, report=report, sandbox=sandbox))

    assert result.outcome == "pass" and result.summary == "All done."
    assert (workdir / "hello.txt").read_text() == "hi\n"
    tool_results = [m["content"] for m in requests[1]["messages"] if m["role"] == "tool"]
    assert tool_results[0] == "wrote hello.txt"
    assert "outside the worktree" in tool_results[1]
    assert tool_results[2] == "hello.py"
    assert report.lines == ["Writing hello.txt", "Reading /etc/passwd", "Listing *.py"]


async def test_openrouter_runner_asks_before_running_commands(sandbox):
    workdir = sandbox.create_worktree("t_test")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return assistant(tool_calls=[("run_command", {"command": "echo hello"})])
        body = json.loads(request.content)
        assert "exit code 0\nhello" in body["messages"][-1]["content"]
        return assistant(content="VERDICT: PASS")

    ask = RecordingAsk(answer="Allow")
    result = await make_runner(handler).run(make_job("openrouter", workdir=workdir, ask=ask, sandbox=sandbox))
    assert result.outcome == "pass"
    assert ask.asked == [("permission", "May I run `echo hello`?", ["Allow", "Deny"])]


async def test_openrouter_runner_reports_billing_problems_as_tired(sandbox):
    workdir = sandbox.create_worktree("t_test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})

    ask = RecordingAsk()
    result = await make_runner(handler).run(make_job("openrouter", workdir=workdir, ask=ask, sandbox=sandbox))
    assert result.outcome == "fail" and "Insufficient credits" in result.summary
    assert ask.asked[0][0] == "tired"


async def test_openrouter_runner_needs_a_key():
    result = await OpenRouterRunner(None, "m").run(make_job("openrouter"))
    assert result.outcome == "fail" and "OPENROUTER_API_KEY" in result.summary
