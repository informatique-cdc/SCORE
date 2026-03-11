"""Axe 5 — Retrievability: BM25 index, query generation, IR evaluation."""

import collections
import logging
import re

from .base import BaseAuditAxis

logger = logging.getLogger(__name__)


class RetrievabilityAxis(BaseAuditAxis):
    axis_key = "retrievability"
    axis_label = "Retrievability"

    def analyze(self):
        from ingestion.models import Document, DocumentChunk

        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="ready",
            )
            .select_related("document")
            .values_list("id", "content", "document_id", "document__title", "heading_path")
        )

        docs = list(
            Document.objects.filter(project=self.project, status="ready").values_list(
                "id", "title", "path"
            )
        )

        if len(chunks) < 5:
            return 100.0, {"total_chunks": len(chunks)}, {}, {"message": "Trop peu de chunks"}

        cfg = self.config
        top_k = cfg.get("bm25_top_k", 10)
        queries_per_doc = cfg.get("queries_per_doc", 3)
        recall_k_values = cfg.get("recall_k_values", [1, 3, 5, 10, 20])

        # Build BM25 index
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning("rank-bm25 not installed, skipping retrievability")
            return 50.0, {}, {}, {"message": "rank-bm25 non installé"}

        chunk_doc_map = {}  # chunk_index → doc_id
        tokenized_corpus = []
        for i, (cid, content, doc_id, _, _) in enumerate(chunks):
            tokens = re.findall(r"\w+", content.lower())
            tokenized_corpus.append(tokens)
            chunk_doc_map[i] = doc_id

        bm25 = BM25Okapi(tokenized_corpus)

        # Generate candidate queries (no LLM)
        queries = self._generate_queries(docs, chunks, queries_per_doc)

        if not queries:
            return 50.0, {"total_queries": 0}, {}, {"message": "Aucune requête générée"}

        # Evaluate
        max_k = max(recall_k_values) if recall_k_values else 20
        eval_results = []
        zero_results = 0
        reciprocal_ranks = []

        for query_text, expected_doc_id in queries:
            tokens = re.findall(r"\w+", query_text.lower())
            if not tokens:
                continue
            scores = bm25.get_scores(tokens)
            ranked_indices = scores.argsort()[::-1][:max_k]

            # Check if any results
            top_scores = [scores[i] for i in ranked_indices[:top_k]]
            if not any(s > 0 for s in top_scores):
                zero_results += 1
                eval_results.append(
                    {
                        "query": query_text[:100],
                        "expected_doc": str(expected_doc_id),
                        "found_at": -1,
                        "recalls": {str(k): 0 for k in recall_k_values},
                    }
                )
                reciprocal_ranks.append(0)
                continue

            # Find rank of expected doc
            found_at = -1
            for rank, idx in enumerate(ranked_indices):
                if chunk_doc_map.get(idx) == expected_doc_id:
                    found_at = rank + 1  # 1-indexed
                    break

            recalls = {}
            for k in recall_k_values:
                top_k_docs = set(chunk_doc_map.get(idx) for idx in ranked_indices[:k])
                recalls[str(k)] = 1 if expected_doc_id in top_k_docs else 0

            rr = 1.0 / found_at if found_at > 0 else 0
            reciprocal_ranks.append(rr)

            eval_results.append(
                {
                    "query": query_text[:100],
                    "expected_doc": str(expected_doc_id),
                    "found_at": found_at,
                    "recalls": recalls,
                }
            )

        total_queries = len(eval_results)
        if total_queries == 0:
            return 50.0, {"total_queries": 0}, {}, {"message": "Évaluation impossible"}

        # Aggregate metrics
        mrr = sum(reciprocal_ranks) / total_queries
        zero_ratio = zero_results / total_queries

        recall_at_k = {}
        for k in recall_k_values:
            recall_at_k[str(k)] = (
                sum(r["recalls"].get(str(k), 0) for r in eval_results) / total_queries
            )

        # Diversity: how many unique docs appear in top-10 across all queries
        all_top10_docs = set()
        for query_text, _ in queries[: len(eval_results)]:
            tokens = re.findall(r"\w+", query_text.lower())
            if tokens:
                scores = bm25.get_scores(tokens)
                for idx in scores.argsort()[::-1][:10]:
                    if scores[idx] > 0:
                        all_top10_docs.add(chunk_doc_map.get(idx))
        total_docs = len(set(d[0] for d in docs))
        diversity = len(all_top10_docs) / max(total_docs, 1)

        # Score
        score = (
            0.35 * mrr * 100
            + 0.30 * recall_at_k.get("10", 0) * 100
            + 0.20 * (1 - zero_ratio) * 100
            + 0.15 * min(diversity, 1.0) * 100
        )

        metrics = {
            "total_chunks": len(chunks),
            "total_docs": total_docs,
            "total_queries": total_queries,
            "mrr": round(mrr, 4),
            "zero_result_ratio": round(zero_ratio, 4),
            "diversity": round(diversity, 4),
            "recall_at_k": {k: round(v, 4) for k, v in recall_at_k.items()},
            "sub_scores": {
                "mrr": round(mrr * 100, 1),
                "recall_10": round(recall_at_k.get("10", 0) * 100, 1),
                "zero_results": round((1 - zero_ratio) * 100, 1),
                "diversity": round(min(diversity, 1.0) * 100, 1),
            },
        }

        # Chart data
        # Recall@k curve
        recall_curve = [
            {"k": int(k), "recall": round(v, 4)}
            for k, v in sorted(recall_at_k.items(), key=lambda x: int(x[0]))
        ]

        # Results per query histogram
        found_ranks = [r["found_at"] for r in eval_results if r["found_at"] > 0]
        rank_hist = self._histogram(found_ranks, bins=max_k) if found_ranks else []

        # Source hit/miss bar
        doc_hit_count = collections.Counter()
        doc_miss_count = collections.Counter()
        for r in eval_results:
            did = r["expected_doc"]
            if r["found_at"] > 0 and r["found_at"] <= top_k:
                doc_hit_count[did] += 1
            else:
                doc_miss_count[did] += 1

        # Top zero-result queries
        zero_result_queries = [r["query"] for r in eval_results if r["found_at"] == -1][:20]

        chart_data = {
            "recall_curve": recall_curve,
            "rank_histogram": rank_hist,
            "zero_result_queries": zero_result_queries,
            "doc_hit_miss": {
                "hits": len(doc_hit_count),
                "misses": len(doc_miss_count),
                "total": total_docs,
            },
        }

        details = {
            "eval_results": eval_results[:100],
            "zero_result_queries": zero_result_queries,
        }

        return score, metrics, chart_data, details

    def _generate_queries(self, docs, chunks, queries_per_doc):
        """Generate candidate queries from titles, heading_paths, and top bigrams."""
        from sklearn.feature_extraction.text import TfidfVectorizer

        queries = []

        # 1. Document titles as queries
        for doc_id, title, _ in docs:
            if title and len(title.strip()) > 3:
                queries.append((title.strip(), doc_id))

        # 2. Heading paths as queries
        for _, _, doc_id, _, heading_path in chunks:
            if heading_path and len(heading_path.strip()) > 3:
                queries.append((heading_path.strip(), doc_id))

        # 3. Top bigrams per doc via TF-IDF
        doc_texts = collections.defaultdict(list)
        for _, content, doc_id, _, _ in chunks:
            doc_texts[doc_id].append(content)

        if len(doc_texts) >= 2:
            doc_ids = list(doc_texts.keys())
            texts = [" ".join(doc_texts[did]) for did in doc_ids]

            try:
                vectorizer = TfidfVectorizer(
                    ngram_range=(2, 2),
                    max_features=5000,
                    min_df=1,
                    max_df=0.9,
                )
                tfidf = vectorizer.fit_transform(texts)
                feature_names = vectorizer.get_feature_names_out()

                for i, did in enumerate(doc_ids):
                    row = tfidf[i].toarray().flatten()
                    top_idx = row.argsort()[-queries_per_doc:][::-1]
                    for j in top_idx:
                        if row[j] > 0:
                            queries.append((str(feature_names[j]), did))
            except ValueError:
                pass  # Not enough data for bigrams

        # Deduplicate and limit
        seen = set()
        unique_queries = []
        for q, did in queries:
            key = (q.lower(), did)
            if key not in seen:
                seen.add(key)
                unique_queries.append((q, did))

        return unique_queries[:500]

    def _histogram(self, values, bins=20):
        if not values:
            return []
        mn, mx = min(values), max(values)
        if mn == mx:
            return [{"bin_start": mn, "bin_end": mx, "count": len(values)}]
        step = max(1, (mx - mn) / bins)
        result = []
        for i in range(bins):
            lo = mn + i * step
            hi = mn + (i + 1) * step
            cnt = (
                sum(1 for v in values if lo <= v < hi)
                if i < bins - 1
                else sum(1 for v in values if lo <= v <= hi)
            )
            if cnt > 0 or i == 0:
                result.append({"bin_start": round(lo, 1), "bin_end": round(hi, 1), "count": cnt})
        return result
