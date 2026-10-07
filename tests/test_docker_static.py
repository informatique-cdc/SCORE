"""L'image Docker doit collecter ses fichiers statiques au build.

En production (DEBUG=False) le stockage à manifeste refuse de rendre tout gabarit
dont un ``{% static %}`` manque au manifeste : si collectstatic échoue au build,
chaque page répond 500.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)


def _collectstatic_step():
    """L'instruction RUN du Dockerfile qui lance collectstatic, continuations jointes."""
    dockerfile = (ROOT / "Dockerfile").read_text().replace("\\\n", " ")
    return next(
        line.removeprefix("RUN").strip()
        for line in dockerfile.splitlines()
        if line.startswith("RUN") and "collectstatic" in line
    )


def test_collectstatic_failure_is_not_silenced():
    step = _collectstatic_step()
    assert "|| true" not in step
    assert "2>/dev/null" not in step


def test_collectstatic_runs_with_the_build_environment():
    """Le build n'a ni .env (exclu par .dockerignore) ni variables d'exécution."""
    if (ROOT / ".env").exists():
        pytest.skip("a local .env would provide what the image build does not have")
    step = _collectstatic_step()
    build_env = dict(re.findall(r"(?:^|\s)([A-Z][A-Z0-9_]*)=(\S+)", step))
    result = subprocess.run(
        [sys.executable, "manage.py", "collectstatic", "--noinput", "--dry-run"],
        cwd=ROOT,
        env={"PATH": os.environ.get("PATH", ""), **build_env},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-1000:]
