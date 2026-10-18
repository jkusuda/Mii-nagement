"""The packaged Windows app's backend: `python -m mii.desktop` (game/launcher.gd starts it).

The same server as `uvicorn mii.asgi:application`, plus what an app without a terminal
needs: its output goes to MII_LOG_FILE (the previous run's log is kept as .1), and on
Windows the event loop is the one that can start agent processes (see tools/run.sh).
"""

import os
import sys
from pathlib import Path

LOG_KEEP_BYTES = 5_000_000  # a log bigger than this starts over (the old one becomes .1)


def main() -> None:
    log = os.environ.get("MII_LOG_FILE")
    if log:
        path = Path(log)
        if path.exists() and path.stat().st_size > LOG_KEEP_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
        sys.stdout = sys.stderr = open(path, "a", buffering=1, encoding="utf-8", errors="replace")  # noqa: SIM115

    import uvicorn

    uvicorn.run(
        "mii.asgi:application",
        host="127.0.0.1",
        port=int(os.environ.get("MII_PORT", "8765")),
        loop="asyncio:ProactorEventLoop" if os.name == "nt" else "auto",
    )


if __name__ == "__main__":
    main()
