"""
Lot 0 du plan « graphe de connaissances » : mesurer la connexité réellement
atteignable par une extraction LLM dédiée, avant d'engager le développement.

La commande ne persiste rien et ne touche pas au pipeline. Elle échantillonne
des fragments, applique KG_TRIPLE_EXTRACTION, normalise les entités comme le
fera la phase de standardisation, puis mesure le graphe obtenu.

Le point de comparaison est le mode « claims » mesuré sur le même corpus :
degré moyen 1,42 — 405 composantes — plus grande composante à 16 %.

Usage:
    python manage.py kg_spike --project <uuid> --chunks 50
    python manage.py kg_spike --project <uuid> --chunks 50 --max-triples 15 --seed 7
"""

import collections
import json
import random
import re
import time

import networkx as nx
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from nsg.stopwords import STOPWORDS_ALL


def normalize(term: str) -> str:
    """Forme canonique d'une entité — même règle que la future standardisation."""
    words = [w for w in re.findall(r"\w+", term.lower(), re.UNICODE) if w not in STOPWORDS_ALL]
    return " ".join(words)


def limit_predicate(predicate: str, max_words: int = 3) -> str:
    words = predicate.split()
    if len(words) <= max_words:
        return predicate
    shortened = words[:max_words]
    if shortened[-1].lower() in STOPWORDS_ALL and len(shortened) > 1:
        shortened = shortened[:-1]
    return " ".join(shortened)


class Command(BaseCommand):
    help = "Mesure la connexité d'un graphe de connaissances extrait par LLM (lot 0, sans persistance)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project", type=str, help="UUID du projet. Par défaut : le projet le mieux fourni."
        )
        parser.add_argument(
            "--cluster",
            type=str,
            help="UUID d'un TopicCluster : n'échantillonne que ses fragments.",
        )
        parser.add_argument(
            "--chunks", type=int, default=50, help="Nombre de fragments échantillonnés."
        )
        parser.add_argument(
            "--max-triples", type=int, default=12, help="Triplets demandés par fragment."
        )
        parser.add_argument(
            "--max-chars", type=int, default=1200, help="Caractères de fragment envoyés au LLM."
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Graine d'échantillonnage, pour rejouer à l'identique.",
        )
        parser.add_argument(
            "--dump", type=str, help="Écrit les triplets bruts dans ce fichier JSON."
        )

    def handle(self, *args, **opts):
        chunks = self._sample_chunks(opts["project"], opts["cluster"], opts["chunks"], opts["seed"])
        self.stdout.write(f"Échantillon : {len(chunks)} fragments")

        triples, usage, elapsed = self._extract(chunks, opts["max_triples"], opts["max_chars"])
        if not triples:
            raise CommandError("Aucun triplet extrait — vérifier la connectivité LLM et le prompt.")

        if opts["dump"]:
            with open(opts["dump"], "w", encoding="utf-8") as fh:
                json.dump(triples, fh, ensure_ascii=False, indent=2)
            self.stdout.write(f"Triplets bruts écrits dans {opts['dump']}")

        self._report(triples, chunks, usage, elapsed)

    # ------------------------------------------------------------------

    def _sample_chunks(self, project_id, cluster_id, count, seed):
        qs = DocumentChunk.objects.filter(document__status=Document.Status.READY)

        if cluster_id:
            from analysis.models import ClusterMembership, TopicCluster

            cluster = TopicCluster.objects.filter(pk=cluster_id).first()
            if cluster is None:
                raise CommandError(f"Cluster {cluster_id} introuvable.")
            member_ids = ClusterMembership.objects.filter(cluster=cluster).values_list(
                "chunk_id", flat=True
            )
            qs = qs.filter(id__in=member_ids)
            self.stdout.write(f"Cluster : {cluster.label} (niveau {cluster.level})")
            ids = list(qs.values_list("id", flat=True))
            if not ids:
                raise CommandError("Aucun fragment dans ce cluster.")
            rng = random.Random(seed)
            picked = rng.sample(ids, min(count, len(ids)))
            return list(DocumentChunk.objects.filter(id__in=picked).select_related("document"))

        if project_id:
            qs = qs.filter(document__project_id=project_id)
        else:
            best = (
                Document.objects.filter(status=Document.Status.READY)
                .values("project_id")
                .annotate(n=Count("id"))
                .order_by("-n")
                .first()
            )
            if not best:
                raise CommandError("Aucun document prêt en base.")
            qs = qs.filter(document__project_id=best["project_id"])
            self.stdout.write(f"Projet retenu : {best['project_id']} ({best['n']} documents)")

        ids = list(qs.values_list("id", flat=True))
        if not ids:
            raise CommandError("Aucun fragment pour ce projet.")

        rng = random.Random(seed)
        picked = rng.sample(ids, min(count, len(ids)))
        return list(DocumentChunk.objects.filter(id__in=picked).select_related("document"))

    def _extract(self, chunks, max_triples, max_chars):
        llm = get_llm_client()
        template = get_prompt("KG_TRIPLE_EXTRACTION")
        prompts = [
            template.format(text=c.content[:max_chars], max_triples=max_triples) for c in chunks
        ]

        self.stdout.write("Appel LLM en cours…")
        t0 = time.monotonic()
        responses = llm.chat_batch_or_concurrent(prompts, json_mode=True)
        elapsed = time.monotonic() - t0

        usage = collections.Counter()
        triples = []
        empty = 0
        for chunk, resp in zip(chunks, responses):
            if not resp:
                empty += 1
                continue
            for key, val in (resp.usage or {}).items():
                if isinstance(val, int):
                    usage[key] += val
            try:
                raw = json.loads(resp.content).get("triples", [])
            except (json.JSONDecodeError, AttributeError, TypeError):
                empty += 1
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                s, p, o = item.get("subject"), item.get("predicate"), item.get("object")
                if not (s and p and o):
                    continue
                triples.append(
                    {
                        "subject": str(s),
                        "predicate": limit_predicate(str(p)),
                        "object": str(o),
                        "chunk": str(chunk.id),
                        "document": str(chunk.document_id),
                    }
                )

        if empty:
            self.stdout.write(self.style.WARNING(f"{empty} fragment(s) sans triplet exploitable"))
        return triples, usage, elapsed

    def _report(self, triples, chunks, usage, elapsed):
        w = self.stdout.write
        freq = collections.Counter()
        edges = []
        for t in triples:
            ns, no = normalize(t["subject"]), normalize(t["object"])
            if not ns or not no or ns == no:
                continue
            freq[ns] += 1
            freq[no] += 1
            edges.append((ns, no))

        w("")
        w("=" * 62)
        w("EXTRACTION")
        w("=" * 62)
        w(f"  triplets                 : {len(triples)}")
        w(f"  triplets / fragment      : {len(triples) / len(chunks):.1f}")
        w(f"  entités distinctes       : {len(freq)}")
        w(
            f"  entités / triplet        : {len(freq) / max(len(triples), 1):.2f}   (plus bas = plus connexe)"
        )
        for k in (2, 3, 5):
            n = sum(1 for v in freq.values() if v >= k)
            w(f"  entités vues >= {k} fois   : {n} ({100 * n / max(len(freq), 1):.0f} %)")

        preds = collections.Counter(t["predicate"] for t in triples)
        w(f"  prédicats distincts      : {len(preds)} pour {len(triples)} triplets")
        w("  top 8 prédicats          : " + ", ".join(f"{p} ×{n}" for p, n in preds.most_common(8)))

        for label, min_freq in (("SANS FILTRE", 1), ("FILTRÉ (entités vues >= 2 fois)", 2)):
            keep = {e for e, v in freq.items() if v >= min_freq}
            kept = [(a, b) for a, b in edges if a in keep and b in keep]
            G = nx.Graph()
            G.add_edges_from(kept)
            w("")
            w("=" * 62)
            w(f"CONNEXITÉ — {label}")
            w("=" * 62)
            if G.number_of_nodes() == 0:
                w("  graphe vide")
                continue
            comps = sorted(nx.connected_components(G), key=len, reverse=True)
            biggest = len(comps[0])
            w(f"  nœuds / arêtes           : {G.number_of_nodes()} / {G.number_of_edges()}")
            w(f"  degré moyen              : {2 * G.number_of_edges() / G.number_of_nodes():.2f}")
            w(f"  composantes              : {len(comps)}")
            w(
                f"  plus grande composante   : {biggest} ({100 * biggest / G.number_of_nodes():.0f} % des nœuds)"
            )
            if biggest >= 3:
                core = G.subgraph(comps[0])
                w(
                    f"  communautés (Louvain)    : {len(nx.community.louvain_communities(core, seed=42))}"
                )

        prompt_tok = usage.get("prompt_tokens", 0)
        completion_tok = usage.get("completion_tokens", 0)
        total_chunks = DocumentChunk.objects.filter(
            document__project_id=chunks[0].document.project_id,
            document__status=Document.Status.READY,
        ).count()
        scale = total_chunks / len(chunks)

        w("")
        w("=" * 62)
        w("COÛT")
        w("=" * 62)
        w(f"  durée échantillon        : {elapsed:.0f} s pour {len(chunks)} fragments")
        w(f"  jetons (prompt/complét.) : {prompt_tok} / {completion_tok}")
        w(f"  corpus complet           : {total_chunks} fragments (×{scale:.0f})")
        w(
            f"  jetons extrapolés        : {int((prompt_tok + completion_tok) * scale):,}".replace(
                ",", " "
            )
        )
        w(f"  durée extrapolée         : {elapsed * scale / 60:.0f} min")
        w("")
        w("Référence mode claims sur ce corpus : degré 1,42 — 405 composantes — plus grande 16 %")
