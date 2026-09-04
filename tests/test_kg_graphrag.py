"""Tests for GraphRAG: feeding knowledge graph relations to the assistant."""

import pytest

from analysis.models import (
    AnalysisJob,
    KGEntity,
    KGRelation,
    KnowledgeGraphRun,
)
from chat.rag_techniques import graph_rag_context
from tests.conftest import make_chunk, make_document


@pytest.fixture
def graph(db, tenant, project, connector):
    job = AnalysisJob.objects.create(tenant=tenant, project=project)
    run = KnowledgeGraphRun.objects.create(tenant=tenant, project=project, analysis_job=job)
    doc = make_document(tenant, project, connector, title="Notice IPSEC")
    make_chunk(tenant, doc, 0, "Couverture santé.")

    def entity(canonical, label, centrality=0.5, aliases=None):
        from analysis.kg.extraction import fold_accents

        names = [canonical, label, *(aliases or [])]
        return KGEntity.objects.create(
            tenant=tenant,
            project=project,
            run=run,
            canonical=canonical,
            label=label,
            aliases=aliases or [],
            search_key=fold_accents(" ".join(names)),
            centrality=centrality,
        )

    ipsec = entity("ipsec", "IPSEC", 0.9)
    conge = entity("conge paternite", "congé de paternité", 0.4)
    duree = entity("dix jours", "10 jours", 0.2)
    garantie = entity("garantie", "garantie", 0.3)

    def relation(subject, obj, predicate, inferred=False, weight=1.0, documents=()):
        rel = KGRelation.objects.create(
            tenant=tenant,
            project=project,
            run=run,
            subject=subject,
            object=obj,
            predicate=predicate,
            inferred=inferred,
            weight=weight,
        )
        if documents:
            rel.documents.set(documents)
        return rel

    relation(ipsec, garantie, "propose", weight=5, documents=[doc])
    relation(conge, duree, "a une durée de", weight=3)
    relation(ipsec, duree, "rembourse sous", inferred=True, weight=9)
    return {"project": project, "run": run, "doc": doc, "job": job}


@pytest.mark.django_db
class TestGraphRagContext:
    def test_builds_context_from_the_knowledge_graph(self, graph):
        context = graph_rag_context("Que propose IPSEC ?", graph["project"])

        assert "IPSEC" in context
        assert "propose" in context
        assert "garantie" in context

    def test_matches_questions_typed_without_accents(self, graph):
        """Users routinely drop accents; search_key is stored folded for that."""
        with_accents = graph_rag_context(
            "Quelle est la durée du congé de paternité ?", graph["project"]
        )
        without = graph_rag_context("Quelle est la duree du conge de paternite ?", graph["project"])

        assert "10 jours" in with_accents
        assert "10 jours" in without

    def test_marks_inferred_relations(self, graph):
        """The assistant must not quote a deduced edge as if it were written."""
        context = graph_rag_context("Que propose IPSEC ?", graph["project"])

        assert "(déduite)" in context

    def test_seeds_from_retrieved_documents(self, graph):
        """Grounded anchoring: relations about the documents actually cited."""
        sources = [{"document_id": str(graph["doc"].id)}]

        context = graph_rag_context("question sans aucun mot du graphe", graph["project"], sources)

        assert "propose" in context

    def test_returns_nothing_when_the_question_matches_no_entity(self, graph):
        assert graph_rag_context("zzzz yyyy xxxx", graph["project"]) == ""

    def test_no_knowledge_graph_falls_back_without_crashing(self, db, project):
        """Projects analysed before the graph existed must keep working."""
        assert graph_rag_context("une question", project) == ""


@pytest.mark.django_db
class TestSeedDiversity:
    def test_a_hub_cannot_swallow_the_whole_budget(self, db, tenant, project, connector):
        """One hub carried 718 edges and drowned out narrower entities."""
        from chat.rag_techniques import MAX_GRAPH_RELATIONS, _spread_over_seeds

        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        run = KnowledgeGraphRun.objects.create(tenant=tenant, project=project, analysis_job=job)
        hub = KGEntity.objects.create(
            tenant=tenant, project=project, run=run, canonical="hub", label="Hub"
        )
        leaves = [
            KGEntity.objects.create(
                tenant=tenant, project=project, run=run, canonical=f"l{i}", label=f"L{i}"
            )
            for i in range(10)
        ]
        candidates = [
            KGRelation.objects.create(
                tenant=tenant,
                project=project,
                run=run,
                subject=hub,
                object=leaf,
                predicate="a pour adresse",
            )
            for leaf in leaves
        ]

        kept = _spread_over_seeds(candidates, {hub.id}, per_seed=3)

        assert len(kept) == 3
        assert len(kept) < MAX_GRAPH_RELATIONS
