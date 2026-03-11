#!/usr/bin/env python
"""Django management script for SCORE."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "score.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
