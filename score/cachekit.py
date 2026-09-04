"""Mémoïsation des composants de page, versionnée par projet.

Les résultats d'une analyse sont figés une fois le pipeline terminé, à l'exception
des statuts de résolution que l'utilisateur bascule depuis l'interface. Plutôt que
de purger des clés une à une, chaque clé embarque ``Project.cache_version`` :
incrémenter ce compteur périme d'un coup tout ce qui a été calculé pour le projet,
et les anciennes entrées s'effacent d'elles-mêmes à l'expiration.

Le compteur vit en base et non dans le cache : le culling de ``DatabaseCache``
peut évincer n'importe quelle clé, y compris un compteur sans expiration, ce qui
le ramènerait à zéro et ferait ressortir des entrées périmées.
"""

from django.core.cache import cache
from django.db.models import F
from django.utils.translation import get_language

DEFAULT_TTL = 60 * 60 * 24


def component_key(name, project, *parts):
    """Clé d'un composant, portée par projet, version et langue active."""
    suffix = ":".join(str(p) for p in parts)
    key = f"sc:{name}:{project.id}:v{project.cache_version}:{get_language()}"
    return f"{key}:{suffix}" if suffix else key


def cached(name, project, builder, *parts, ttl=DEFAULT_TTL):
    """Renvoie la valeur mémoïsée de ``builder()``, en la calculant si besoin.

    ``parts`` complète la clé pour les composants qui varient à l'intérieur d'un
    projet (identifiant de job, horodatage d'un fichier…).
    """
    key = component_key(name, project, *parts)
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, ttl)
    return value


def invalidate_project(project):
    """Périme tous les composants mis en cache pour ce projet."""
    if project is None:
        return
    from tenants.models import Project

    Project.objects.filter(pk=project.pk).update(cache_version=F("cache_version") + 1)
    # L'instance en mémoire porterait encore l'ancienne version : la recaler évite
    # qu'un rendu enchaîné dans la même requête relise l'entrée qu'on vient de périmer.
    project.cache_version += 1
