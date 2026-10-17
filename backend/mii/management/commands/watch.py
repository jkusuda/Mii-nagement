"""`manage.py watch [url]`: print the live event stream (debugging)."""

from django.core.management.base import BaseCommand

from mii import watch


class Command(BaseCommand):
    help = "Print every event the server sends over the WebSocket."

    def add_arguments(self, parser):
        parser.add_argument("url", nargs="?", default=watch.URL)

    def handle(self, *args, **options):
        watch.main(options["url"])
