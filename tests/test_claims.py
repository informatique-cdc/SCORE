"""Tests for claims extraction prompts and structured output parsing."""
import json

from llm.prompt_loader import get_prompt


class TestClaimExtractionPrompt:
    def test_prompt_formatting(self):
        prompt = get_prompt("CLAIM_EXTRACTION").format(
            text="Python 3.12 was released in October 2023.",
            max_claims=5,
        )
        assert "Python 3.12" in prompt
        assert "5" in prompt
        assert "JSON" in prompt

    def test_prompt_requests_json_structure(self):
        prompt = get_prompt("CLAIM_EXTRACTION").format(text="test", max_claims=3)
        assert "subject" in prompt
        assert "predicate" in prompt
        assert "object" in prompt
        assert "qualifiers" in prompt


class TestContradictionPrompt:
    def test_prompt_formatting(self):
        prompt = get_prompt("CONTRADICTION_CHECK").format(
            doc_a_title="API Guide v1",
            doc_a_date="2023-01-15",
            claim_a="The API rate limit is 100 requests per minute",
            context_a="The API rate limit is 100 requests per minute for all users.",
            doc_b_title="API Guide v2",
            doc_b_date="2024-03-01",
            claim_b="The API rate limit is 500 requests per minute",
            context_b="We've increased the API rate limit to 500 requests per minute.",
        )
        assert "API Guide v1" in prompt
        assert "API Guide v2" in prompt
        assert "entailment" in prompt
        assert "contradiction" in prompt
        assert "outdated" in prompt

    def test_prompt_requests_classification(self):
        prompt = get_prompt("CONTRADICTION_CHECK").format(
            doc_a_title="A", doc_a_date="2023-01-01",
            claim_a="X", context_a="X context",
            doc_b_title="B", doc_b_date="2024-01-01",
            claim_b="Y", context_b="Y context",
        )
        assert "classification" in prompt
        assert "confidence" in prompt
        assert "evidence" in prompt


class TestClaimParsing:
    """Test that expected LLM output format parses correctly."""

    def test_parse_claims_json(self):
        raw = json.dumps({
            "claims": [
                {
                    "subject": "Python",
                    "predicate": "was released in version",
                    "object": "3.12",
                    "qualifiers": {"date": "October 2023"},
                    "date": "2023-10-01",
                    "raw_text": "Python 3.12 was released in October 2023.",
                }
            ]
        })
        data = json.loads(raw)
        assert len(data["claims"]) == 1
        claim = data["claims"][0]
        assert claim["subject"] == "Python"
        assert claim["predicate"] == "was released in version"

    def test_parse_contradiction_json(self):
        raw = json.dumps({
            "classification": "outdated",
            "confidence": 0.92,
            "evidence": "Claim B updates the rate limit from 100 to 500.",
            "newer_claim": "B",
            "severity": "high",
        })
        data = json.loads(raw)
        assert data["classification"] == "outdated"
        assert data["confidence"] > 0.9
        assert data["newer_claim"] == "B"
