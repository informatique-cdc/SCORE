"""Axe 4 — Cohérence interne: terminology variants, key-value conflicts, entities."""
import collections
import logging
import re
from difflib import SequenceMatcher

from nsg.stopwords import get_stopwords_for_sklearn

from .base import BaseAuditAxis

logger = logging.getLogger(__name__)

# Key-value extraction patterns
KV_PATTERNS = {
    "sla": re.compile(r"SLA\s*[:=]\s*([^\n,;]{3,50})", re.I),
    "version": re.compile(r"version\s*[:=]\s*([^\n,;]{1,30})", re.I),
    "port": re.compile(r"port\s*[:=]\s*(\d{2,5})", re.I),
    "url": re.compile(r"(?:url|endpoint|uri)\s*[:=]\s*(https?://[^\s,;\"']{5,200})", re.I),
    "date": re.compile(r"(?:date|échéance|deadline)\s*[:=]\s*(\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})", re.I),
    "timeout": re.compile(r"timeout\s*[:=]\s*(\d+\s*(?:ms|s|sec|min)?)", re.I),
    "limit": re.compile(r"(?:limit|max|maximum)\s*[:=]\s*(\d[\d\s]*\w*)", re.I),
}

# Entity extraction
ENTITY_PATTERNS = {
    "date": re.compile(r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\b"),
    "version": re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?(?:-\w+)?)\b"),
    "url": re.compile(r"(https?://[^\s<>\"']{5,200})"),
    "ip": re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"),
}


class CoherenceAxis(BaseAuditAxis):
    axis_key = "coherence"
    axis_label = "Cohérence interne"

    def analyze(self):
        from ingestion.models import DocumentChunk

        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="ready",
            )
            .select_related("document")
            .values_list("id", "content", "document_id", "document__title")
        )

        if len(chunks) < 3:
            return 100.0, {"total_chunks": len(chunks)}, {}, {"message": "Trop peu de chunks"}

        cfg = self.config
        min_freq = cfg.get("min_term_frequency", 3)
        lev_threshold = cfg.get("levenshtein_threshold", 0.85)

        # 1. Extract key terms per document
        doc_terms = self._extract_doc_terms(chunks, min_freq)

        # 2. Detect terminology variants via stemming + SequenceMatcher
        variants = self._detect_variants(doc_terms, lev_threshold)

        # 3. Key-value conflict detection
        kv_conflicts = self._detect_kv_conflicts(chunks)

        # 4. Entity consistency check
        entity_conflicts = self._detect_entity_conflicts(chunks)

        # Score computation
        total_docs = len(set(c[2] for c in chunks))
        conflict_count = sum(len(v["conflicting_values"]) for v in kv_conflicts)
        conflict_ratio = conflict_count / max(total_docs, 1)
        conflict_score = max(0, 100 * (1 - conflict_ratio * 5))

        variant_groups = len(variants)
        term_consistency = max(0, 100 - variant_groups * 2)

        entity_conflict_count = sum(len(e.get("values", [])) - 1 for e in entity_conflicts if len(e.get("values", [])) > 1)
        entity_score = max(0, 100 * (1 - entity_conflict_count / max(total_docs * 3, 1)))

        score = (
            0.40 * conflict_score
            + 0.30 * term_consistency
            + 0.30 * entity_score
        )

        metrics = {
            "total_chunks": len(chunks),
            "total_docs": total_docs,
            "kv_conflict_count": conflict_count,
            "kv_conflict_keys": len(kv_conflicts),
            "terminology_variant_groups": variant_groups,
            "entity_conflicts": entity_conflict_count,
            "sub_scores": {
                "kv_conflicts": round(conflict_score, 1),
                "terminology": round(term_consistency, 1),
                "entities": round(entity_score, 1),
            },
        }

        # Chart data
        # Top conflicting keys bar chart
        conflict_bar = [
            {"key": kv["key"], "conflict_count": len(kv["conflicting_values"]), "values": kv["conflicting_values"][:5]}
            for kv in kv_conflicts[:20]
        ]

        # Variant groups (sankey-like: canonical → variants)
        variant_chart = [
            {"canonical": v["canonical"], "variants": v["variants"][:10], "doc_count": v["doc_count"]}
            for v in variants[:30]
        ]

        chart_data = {
            "conflict_bar": conflict_bar,
            "variant_groups": variant_chart,
            "entity_summary": self._entity_summary(entity_conflicts),
        }

        details = {
            "kv_conflicts": kv_conflicts[:50],
            "terminology_variants": variants[:50],
            "entity_conflicts": entity_conflicts[:50],
        }

        return score, metrics, chart_data, details

    def _extract_doc_terms(self, chunks, min_freq):
        """Extract TF-IDF top terms per document."""
        from sklearn.feature_extraction.text import TfidfVectorizer

        # Group text by document
        doc_texts = collections.defaultdict(list)
        for _, content, doc_id, _ in chunks:
            doc_texts[doc_id].append(content)

        doc_ids = list(doc_texts.keys())
        texts = [" ".join(doc_texts[did]) for did in doc_ids]

        if len(texts) < 2:
            return {}

        vectorizer = TfidfVectorizer(max_features=5000, ngram_range=(1, 1), min_df=1, max_df=0.95, stop_words=get_stopwords_for_sklearn())
        tfidf = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()

        doc_terms = {}
        for i, did in enumerate(doc_ids):
            row = tfidf[i].toarray().flatten()
            top_indices = row.argsort()[-20:][::-1]
            terms = [str(feature_names[j]) for j in top_indices if row[j] > 0]
            doc_terms[did] = terms

        return doc_terms

    def _detect_variants(self, doc_terms, threshold):
        """Detect terminology variants using stemming and string similarity."""
        try:
            from nltk.stem.snowball import SnowballStemmer
            stemmer = SnowballStemmer("french")
        except ImportError:
            # Fallback: simple lowering
            stemmer = None

        # Collect all terms across docs
        all_terms = collections.Counter()
        for terms in doc_terms.values():
            all_terms.update(terms)

        # Group by stem
        stem_groups = collections.defaultdict(set)
        for term in all_terms:
            if stemmer:
                stem = stemmer.stem(term)
            else:
                stem = term.lower()[:6]
            stem_groups[stem].add(term)

        # Find groups with variants (more than 1 surface form, similar strings)
        variants = []
        for stem, terms in stem_groups.items():
            if len(terms) < 2:
                continue
            terms_list = sorted(terms)
            # Check pairwise similarity
            is_variant = False
            for i in range(len(terms_list)):
                for j in range(i + 1, len(terms_list)):
                    ratio = SequenceMatcher(None, terms_list[i], terms_list[j]).ratio()
                    if ratio >= threshold and terms_list[i] != terms_list[j]:
                        is_variant = True
                        break
                if is_variant:
                    break

            if is_variant:
                canonical = max(terms, key=lambda t: all_terms[t])
                others = [t for t in terms if t != canonical]
                doc_count = sum(
                    1 for doc_terms_list in doc_terms.values()
                    if any(t in doc_terms_list for t in terms)
                )
                variants.append({
                    "canonical": canonical,
                    "variants": others,
                    "doc_count": doc_count,
                })

        return sorted(variants, key=lambda v: v["doc_count"], reverse=True)

    def _detect_kv_conflicts(self, chunks):
        """Detect conflicting key=value pairs across chunks."""
        # key → {value: [doc_ids]}
        kv_map = collections.defaultdict(lambda: collections.defaultdict(set))

        for _, content, doc_id, _ in chunks:
            for key, pattern in KV_PATTERNS.items():
                matches = pattern.findall(content)
                for val in matches:
                    normalized = val.strip().lower()
                    kv_map[key][normalized].add(str(doc_id))

        conflicts = []
        for key, values in kv_map.items():
            if len(values) > 1:
                conflicting = [
                    {"value": val, "doc_count": len(docs), "doc_ids": list(docs)[:5]}
                    for val, docs in sorted(values.items(), key=lambda x: -len(x[1]))
                ]
                conflicts.append({
                    "key": key,
                    "conflicting_values": conflicting,
                    "total_values": len(values),
                })

        return sorted(conflicts, key=lambda c: c["total_values"], reverse=True)

    def _detect_entity_conflicts(self, chunks):
        """Detect inconsistent entities across the corpus."""
        entity_map = collections.defaultdict(lambda: collections.defaultdict(set))

        for _, content, doc_id, _ in chunks:
            for etype, pattern in ENTITY_PATTERNS.items():
                matches = pattern.findall(content)
                for val in matches:
                    entity_map[etype][val].add(str(doc_id))

        results = []
        for etype, values in entity_map.items():
            if len(values) > 0:
                results.append({
                    "entity_type": etype,
                    "unique_values": len(values),
                    "values": [
                        {"value": val, "doc_count": len(docs)}
                        for val, docs in sorted(values.items(), key=lambda x: -len(x[1]))[:20]
                    ],
                })

        return results

    def _entity_summary(self, entity_conflicts):
        """Summarize entity data for charts."""
        return [
            {"type": e["entity_type"], "unique_values": e["unique_values"]}
            for e in entity_conflicts
        ]
