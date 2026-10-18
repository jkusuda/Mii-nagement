# Answers every bubble a few seconds after it appears, and keeps packages coming
# (one every CAPTURE_EVERY seconds, default 12: real agents work on each, so mind the cost).
import asyncio, json, os, random, websockets, urllib.request
EVERY = float(os.environ.get("CAPTURE_EVERY", "12"))
TITLES = ["Add a dark mode toggle", "Fix the login redirect", "Write tests for the parser", "Speed up the search page",
          "Add a CSV export", "Fix the flaky date test", "Translate the settings page", "Add rate limiting"]
def post_task(title):
    req = urllib.request.Request("http://127.0.0.1:8011/islands/sunny_isle/tasks", method="POST",
        data=json.dumps({"title": title, "description": title}).encode(), headers={"content-type": "application/json"})
    urllib.request.urlopen(req).read()
async def answer(ws, b):
    await asyncio.sleep(random.uniform(3, 6))
    opts = b.get("options") or []
    resp = opts[0] if opts else ("Ship it" if b["kind"] == "ready" else "Go ahead, use your best judgement")
    await ws.send(json.dumps({"type": "answer_bubble", "id": b["id"], "response": resp}))
async def feeder():
    i = 0
    while True:
        post_task(TITLES[i % len(TITLES)]); i += 1
        await asyncio.sleep(EVERY)
async def main():
    asyncio.create_task(feeder())
    async with websockets.connect("ws://127.0.0.1:8011/ws?island=sunny_isle") as ws:
        snap = json.loads(await ws.recv())
        for b in snap["island"].get("bubbles", []):
            asyncio.create_task(answer(ws, b))
        async for raw in ws:
            m = json.loads(raw)
            if m["type"] == "bubble":
                asyncio.create_task(answer(ws, m))
asyncio.run(main())
