"""Tests for analysis.claims — ClaimsExtractor."""
import json
from unittest.mock import MagicMock, patch

import pytest

from analysis.claims import ClaimsExtractor
from analysis.models import Claim
from tests.conftest import make_chunk, make_document, make_llm_response


def _make_extractor(tenant, project):
    """Bypass __init__ and wire up a ClaimsExtractor with mocked deps."""
    ext = ClaimsExtractor.__new__(ClaimsExtractor)
    ext.tenant = tenant
    ext.project = project
    ext.llm = MagicMock()
    ext.vec_store = MagicMock()
    ext.on_progress = None
    ext.config = {"max_claims_per_chunk": 5}
    ext.max_claims_per_chunk = 5
    return ext


@pytest.mark.django_db
class TestExtractAll:
    def test_creates_claims(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="Doc A")
        make_chunk(tenant, doc, 0, "The sky is blue.")
        make_chunk(tenant, doc, 1, "Water boils at 100C.")

        ext = _make_extractor(tenant, project)
        ext.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(json.dumps({
                "claims": [{"subject": "sky", "predicate": "is", "object": "blue", "raw_text": "The sky is blue."}]
            })),
            make_llm_response(json.dumps({
                "claims": [{"subject": "water", "predicate": "boils at", "object": "100C", "raw_text": "Water boils at 100C."}]
            })),
        ]
        ext.llm.embed.return_value = [[0.1] * 1536, [0.2] * 1536]

        count = ext.extract_all()

        assert count == 2
        assert Claim.objects.filter(project=project).count() == 2
        ext.vec_store.upsert_claims_batch.assert_called_once()

    def test_skips_docs_with_existing_claims(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="Already Done")
        chunk = make_chunk(tenant, doc, 0, "Old claim.")
        Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="x", predicate="y", object_value="z", raw_text="Old claim.",
        )

        ext = _make_extractor(tenant, project)
        count = ext.extract_all()

        assert count == 0
        ext.llm.chat_batch_or_concurrent.assert_not_called()

    def test_no_docs_returns_zero(self, tenant, project, connector):
        ext = _make_extractor(tenant, project)
        assert ext.extract_all() == 0

    def test_malformed_llm_response_skipped(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="Bad LLM")
        make_chunk(tenant, doc, 0, "Some text.")

        ext = _make_extractor(tenant, project)
        ext.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response("not json at all"),
        ]

        count = ext.extract_all()
        assert count == 0
        assert Claim.objects.filter(project=project).count() == 0

    def test_claim_date_parsing(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="Dated")
        make_chunk(tenant, doc, 0, "Policy effective 2024-01-15.")

        ext = _make_extractor(tenant, project)
        ext.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(json.dumps({
                "claims": [{
                    "subject": "policy", "predicate": "effective",
                    "object": "2024-01-15", "date": "2024-01-15",
                    "raw_text": "Policy effective 2024-01-15.",
                }]
            })),
        ]
        ext.llm.embed.return_value = [[0.1] * 1536]

        ext.extract_all()

        claim = Claim.objects.get(project=project)
        assert claim.claim_date is not None
        assert claim.claim_date.isoformat() == "2024-01-15"

    def test_claim_date_invalid(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="Bad Date")
        make_chunk(tenant, doc, 0, "Some fact.")

        ext = _make_extractor(tenant, project)
        ext.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(json.dumps({
                "claims": [{
                    "subject": "x", "predicate": "y",
                    "object": "z", "date": "not-a-date",
                    "raw_text": "Some fact.",
                }]
            })),
        ]
        ext.llm.embed.return_value = [[0.1] * 1536]

        ext.extract_all()

        claim = Claim.objects.get(project=project)
        assert claim.claim_date is None

    def test_none_response_skipped(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="None resp")
        make_chunk(tenant, doc, 0, "Text.")

        ext = _make_extractor(tenant, project)
        ext.llm.chat_batch_or_concurrent.return_value = [None]

        count = ext.extract_all()
        assert count == 0


@pytest.mark.django_db
class TestEmbedClaims:
    def test_calls_vec_store(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="Embed Test")
        chunk = make_chunk(tenant, doc, 0, "Claim text.")
        claim = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="a", predicate="b", object_value="c", raw_text="Claim text.",
        )

        ext = _make_extractor(tenant, project)
        ext.llm.embed.return_value = [[0.1] * 1536]

        ext._embed_claims([claim])

        ext.llm.embed.assert_called_once()
        ext.vec_store.upsert_claims_batch.assert_called_once()
        # Verify has_embedding is set
        claim.refresh_from_db()
        assert claim.has_embedding is True

    def test_empty_claims_noop(self, tenant, project, connector):
        ext = _make_extractor(tenant, project)
        ext._embed_claims([])
        ext.llm.embed.assert_not_called()
