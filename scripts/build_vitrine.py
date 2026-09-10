"""Rend la page vitrine en HTML statique dans `_site/`, pour GitHub Pages.

Pages ne sert que des fichiers statiques : le gabarit Django est donc figé ici,
avec les chiffres du dépôt lus dans `config.yaml`.

Volontairement autonome — ce script ne charge que Django et PyYAML au lieu du
module `score.settings`, pour que le job de publication n'ait pas à installer
spacy, faiss et le reste des dépendances de l'application.
"""

import shutil
import sys
from pathlib import Path

import django
import yaml
from django.conf import settings

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"
STATIC_SRC = ROOT / "dashboard" / "static"

# Ce que le gabarit référence, plus les polices appelées par app.css.
ASSETS = ["css/app.css", "js/ui.js", "js/vitrine.js"]


def configure():
    settings.configure(
        INSTALLED_APPS=["django.contrib.staticfiles", "django.contrib.humanize"],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [ROOT / "vitrine" / "templates"],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
        # Réécrit en chemin relatif plus bas : `urljoin` exige une base absolue.
        STATIC_URL="/static/",
        LANGUAGE_CODE="fr",
        TIME_ZONE="UTC",
        USE_I18N=True,
        USE_TZ=True,
    )
    django.setup()


def main():
    css = STATIC_SRC / "css" / "app.css"
    if not css.exists():
        sys.exit(f"{css} est absent : lancez `npm run build` avant ce script.")

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}

    configure()
    from django.template.loader import render_to_string

    html = render_to_string("vitrine/home.html", {"gh": config.get("vitrine", {})})

    # Pages sert un dépôt de projet sous /<repo>/ : des chemins relatifs à
    # index.html fonctionnent quel que soit ce préfixe, et hors ligne aussi.
    html = html.replace('="/static/', '="static/')

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "static").mkdir(parents=True)

    for asset in ASSETS:
        dest = OUT / "static" / asset
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STATIC_SRC / asset, dest)

    shutil.copytree(STATIC_SRC / "fonts", OUT / "static" / "fonts")
    (OUT / "index.html").write_text(html, encoding="utf-8")

    # Sans ce fichier, Pages passe le site dans Jekyll, qui ignore les dossiers
    # commençant par un souligné et réécrit certains chemins.
    (OUT / ".nojekyll").touch()

    print(f"vitrine rendue dans {OUT.relative_to(ROOT)} ({len(html):,} octets)")


if __name__ == "__main__":
    main()
