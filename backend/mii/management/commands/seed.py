"""`manage.py seed`: create the seed island; `--reset` wipes first; `--loop` runs the simulator."""

import asyncio

import httpx
from django.core.management import call_command
from django.core.management.base import BaseCommand

from mii import seed


class Command(BaseCommand):
    help = "Seed the island, optionally run the simulator against a running server."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="wipe the database first (stop the server first)")
        parser.add_argument("--loop", action="store_true", help="keep sending fake tasks to the server")
        parser.add_argument("--url", default="http://localhost:8000", help="server URL for --loop")
        parser.add_argument("--pause", type=float, default=3.0, help="seconds between tasks")

    def handle(self, *args, **options):
        call_command("migrate", interactive=False, verbosity=0)
        island_id = seed.seed(reset=options["reset"])
        if options["loop"]:
            try:
                asyncio.run(seed.simulate(options["url"], island_id, options["pause"]))
            except KeyboardInterrupt:
                pass
            except httpx.ConnectError:
                self.stderr.write(f"could not reach {options['url']}. Start the server first.")
