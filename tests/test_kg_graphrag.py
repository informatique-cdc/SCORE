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

    chunk = make_chunk(tenant, doc, 0, "Couverture santé.")
    # Known to the graph but never returned by vector search: without it,
    # deduplication would mask whether the graph inflates the cited documents.
    graph_only_doc = make_document(tenant, project, connector, title="Barème 2026")

    ipsec = entity("ipsec", "IPSEC", 0.9)
    conge = entity("conge paternite", "congé de paternité", 0.4)
    duree = entity("dix jours", "10 jours", 0.2)
    garantie = entity("garantie", "garantie", 0.3)

    def relation(subject, obj, predicate, inferred=False, weight=1.0, documents=(), evidence=()):
        rel = KGRelation.objects.create(
            tenant=tenant,
            project=project,
            run=run,
            subject=subject,
            object=obj,
            predicate=predicate,
            inferred=inferred,
            weight=weight,
            evidence=list(evidence),
        )
        if documents:
            rel.documents.set(documents)
        return rel

    relation(
        ipsec,
        garantie,
        "propose",
        weight=5,
        documents=[doc],
        evidence=["IPSEC propose une garantie santé."],
    )
    relation(conge, duree, "a une durée de", weight=3)
    relation(ipsec, duree, "rembourse sous", inferred=True, weight=9, documents=[graph_only_doc])
    return {
        "project": project,
        "run": run,
        "doc": doc,
        "graph_only_doc": graph_only_doc,
        "chunk": chunk,
        "job": job,
    }


def prompt_of(trace):
    return trace["prompt"] if trace else ""


@pytest.mark.django_db
class TestGraphRagContext:
    def test_builds_context_from_the_knowledge_graph(self, graph):
        context = prompt_of(graph_rag_context("Que propose IPSEC ?", graph["project"]))

        assert "IPSEC" in context
        assert "propose" in context
        assert "garantie" in context

    def test_matches_questions_typed_without_accents(self, graph):
        """Users routinely drop accents; search_key is stored folded for that."""
        with_accents = prompt_of(
            graph_rag_context("Quelle est la durée du congé de paternité ?", graph["project"])
        )
        without = prompt_of(
            graph_rag_context("Quelle est la duree du conge de paternite ?", graph["project"])
        )

        assert "10 jours" in with_accents
        assert "10 jours" in without

    def test_marks_inferred_relations(self, graph):
        """The assistant must not quote a deduced edge as if it were written."""
        context = prompt_of(graph_rag_context("Que propose IPSEC ?", graph["project"]))

        assert "(déduite)" in context

    def test_seeds_from_retrieved_documents(self, graph):
        """Grounded anchoring: relations about the documents actually cited."""
        sources = [{"document_id": str(graph["doc"].id)}]

        context = prompt_of(
            graph_rag_context("question sans aucun mot du graphe", graph["project"], sources)
        )

        assert "propose" in context

    def test_returns_nothing_when_the_question_matches_no_entity(self, graph):
        assert graph_rag_context("zzzz yyyy xxxx", graph["project"]) is None

    def test_no_knowledge_graph_falls_back_without_crashing(self, db, project):
        """Projects analysed before the graph existed must keep working."""
        assert graph_rag_context("une question", project) is None


@pytest.mark.django_db
class TestGraphTrace:
    """The relations must be readable by the user, not only by the model."""

    def test_relations_carry_their_source_documents_and_snippets(self, graph):
        trace = graph_rag_context("Que propose IPSEC ?", graph["project"])

        proposes = next(r for r in trace["relations"] if r["predicate"] == "propose")
        assert proposes["subject"] == "IPSEC"
        assert proposes["object"] == "garantie"
        assert proposes["evidence"] == ["IPSEC propose une garantie santé."]
        assert proposes["documents"] == [
            {
                "document_id": str(graph["doc"].id),
                "title": "Notice IPSEC",
                "connector_id": str(graph["doc"].connector_id),
            }
        ]

    def test_trace_reports_the_seeds_it_started_from(self, graph):
        trace = graph_rag_context("Que propose IPSEC ?", graph["project"])

        assert "IPSEC" in trace["seeds"]

    def test_inferred_flag_survives_into_the_trace(self, graph):
        trace = graph_rag_context("Que propose IPSEC ?", graph["project"])

        deduced = next(r for r in trace["relations"] if r["predicate"] == "rembourse sous")
        assert deduced["inferred"] is True


@pytest.mark.django_db
class TestGraphTracePayload:
    def test_drops_the_prompt_and_deduplicates_relations(self, graph):
        from chat.rag_techniques import graph_trace_payload

        trace = graph_rag_context("Que propose IPSEC ?", graph["project"])

        payload = graph_trace_payload([trace, trace])

        assert "prompt" not in payload
        assert len(payload["relations"]) == len(trace["relations"])
        assert payload["seeds"] == trace["seeds"]

    def test_empty_without_any_relation(self):
        from chat.rag_techniques import graph_trace_payload

        assert graph_trace_payload([None]) == {}


@pytest.mark.django_db
class TestCitedDocuments:
    def test_the_graph_never_inflates_the_cited_documents(self, graph, tenant):
        """« Documents cités » once showed 49 entries: the graph was folding its own
        documents in, though the model had only ever been given the retrieved
        passages. The graph's documents belong to the trace, not to the sources."""
        from unittest.mock import MagicMock, patch

        from chat.rag import ask_documents

        llm = MagicMock()
        llm.embed_single.return_value = [0.0]
        llm.chat_messages.return_value = MagicMock(content="Réponse.")
        vec_store = MagicMock()
        vec_store.search.return_value = [
            {
                "chunk_id": str(graph["chunk"].id),
                "document_id": str(graph["doc"].id),
                "similarity": 0.8,
            }
        ]

        with (
            patch("chat.rag.get_llm_client", return_value=llm),
            patch("chat.rag.get_vector_store", return_value=vec_store),
        ):
            result = ask_documents(
                "Que propose IPSEC ?", tenant, graph["project"], tools=["graph-rag"]
            )

        cited = [s["document_id"] for s in result["sources"]]
        assert cited == [str(graph["doc"].id)]
        assert str(graph["graph_only_doc"].id) not in cited
        assert result["graph_trace"]["relations"]

    def test_the_graph_documents_remain_reachable_through_the_trace(self, graph, tenant):
        """Removing them from the sources must not make them unreachable."""
        trace = graph_rag_context("Que propose IPSEC ?", graph["project"])

        cited = {d["document_id"] for r in trace["relations"] for d in r["documents"]}
        assert str(graph["doc"].id) in cited
        assert str(graph["graph_only_doc"].id) in cited


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
