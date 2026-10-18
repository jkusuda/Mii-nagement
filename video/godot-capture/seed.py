# Seeds the capture island with the video's residents.
import asyncio, json, websockets
RES = [
 dict(name="Jun", job="planner", color="#F2912C", catchphrase="Let's map it out!", brain="mock",
      appearance=dict(skin="#8D5A3B", hair=12, hair_color="#E8C55A", eyes=3, brows=1, nose=0, mouth=2), chattiness=0.9),
 dict(name="Carlos", job="coder", color="#2FB39A", catchphrase="Ship it... carefully!", brain="claude-code",
      appearance=dict(skin="#F7D1B0", hair=27, hair_color="#D9542B", eyes=10, brows=4, nose=3, mouth=7), caution=0.7),
 dict(name="Sydney", job="reviewer", color="#7553DF", catchphrase="Show me the tests.", brain="mock",
      appearance=dict(skin="#F3C9A5", hair=40, hair_color="#5A3220", eyes=30, brows=12, nose=2, mouth=20), caution=0.9, bluntness=0.7),
 dict(name="Kiki", job="coder", color="#F06292", catchphrase="Let me poke at it!", brain="mock",
      appearance=dict(skin="#E0AC80", hair=5, hair_color="#1A1A1A", eyes=20, brows=8, nose=5, mouth=12), creativity=0.8),
]
async def main():
    async with websockets.connect("ws://127.0.0.1:8011/ws?island=sunny_isle", max_size=None) as ws:
        await ws.recv()
        for r in RES:
            await ws.send(json.dumps({"type": "create_resident", "resident": r}))
            while True:
                m = json.loads(await ws.recv())
                if m["type"] in ("resident", "error"):
                    print(m["type"], m.get("resident", {}).get("id"), m.get("resident", {}).get("brain"), m.get("message", ""))
                    break
asyncio.run(main())
