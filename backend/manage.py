#!/usr/bin/env python
"""Django's command-line tool. Run from backend/:  uv run python manage.py <command>

migrate            create / update the SQLite tables
makemigrations mii after changing models.py
seed [--reset] [--loop]
watch
"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mii.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
