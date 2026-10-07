"""Cohérence entre les fichiers de déploiement et les réglages Django."""

from pathlib import Path

import yaml
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SERVICES_USING_THE_DATABASE = ("web", "celery", "celery-beat")


def _workdir():
    """Répertoire de l'application dans l'image (dernier WORKDIR du Dockerfile)."""
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    return [line.split()[1] for line in lines if line.startswith("WORKDIR")][-1]


def _in_container(path):
    return f"{_workdir()}/{Path(path).relative_to(ROOT).as_posix()}"


def _mounts(service):
    """Volumes d'un service, sous la forme {chemin dans le conteneur: volume}."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    mounts = {}
    for volume in compose["services"][service].get("volumes", []):
        source, target = volume.split(":")[:2]
        mounts[target] = source
    return mounts


def test_compose_mounts_the_data_dir_in_every_service_using_the_database():
    data_dir = _in_container(settings.DATA_DIR)
    for service in SERVICES_USING_THE_DATABASE:
        assert data_dir in _mounts(service), f"{service} does not persist {data_dir}"


def test_compose_services_share_one_database():
    """Le worker doit lire les jobs que le web vient de créer : même volume."""
    data_dir = _in_container(settings.DATA_DIR)
    sources = {_mounts(service).get(data_dir) for service in SERVICES_USING_THE_DATABASE}
    assert len(sources) == 1 and None not in sources
