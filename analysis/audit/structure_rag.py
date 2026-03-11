"""Axe 2 — Structure RAG: chunk size stats, info density, readability, overlap."""

import collections
import logging
import math
import re

from nsg.stopwords import STOPWORDS_ALL as STOPWORDS

from .base import BaseAuditAxis

logger = logging.getLogger(__name__)


class StructureAxis(BaseAuditAxis):
    axis_key = "structure"
    axis_label = "Structure RAG"

    def analyze(self):
        from ingestion.models import DocumentChunk

        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="ready",
            )
            .select_related("document__connector")
            .values_list(
                "id",
                "content",
                "token_count",
                "document_id",
                "document__title",
                "document__connector__name",
                "chunk_index",
            )
            .order_by("document_id", "chunk_index")
        )

        if not chunks:
            return 100.0, {"total_chunks": 0}, {}, {"message": "Aucun chunk"}

        cfg = self.config
        min_tokens = cfg.get("min_chunk_tokens", 50)
        max_tokens = cfg.get("max_chunk_tokens", 1024)
        optimal_tokens = cfg.get("optimal_chunk_tokens", 512)

        total = len(chunks)
        token_counts = [c[2] or len(c[1].split()) for c in chunks]

        # 1. Size statistics
        mean_tc = sum(token_counts) / total
        variance = sum((t - mean_tc) ** 2 for t in token_counts) / total
        std_tc = math.sqrt(variance)
        too_small = sum(1 for t in token_counts if t < min_tokens)
        too_large = sum(1 for t in token_counts if t > max_tokens)
        outlier_ratio = (too_small + too_large) / total

        # 2. Info density: stopword ratio per chunk
        densities = []
        for _, content, _, _, _, _, _ in chunks:
            words = re.findall(r"\w+", content.lower())
            if words:
                sw_count = sum(1 for w in words if w in STOPWORDS)
                densities.append(1 - sw_count / len(words))
            else:
                densities.append(0)
        avg_density = sum(densities) / len(densities)

        # 3. Readability: sentences/chunk, words/sentence, chars/word
        sentence_counts = []
        words_per_sentence = []
        chars_per_word = []
        for _, content, _, _, _, _, _ in chunks:
            sentences = [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]
            sentence_counts.append(len(sentences))
            words = content.split()
            if sentences:
                words_per_sentence.append(len(words) / max(len(sentences), 1))
            if words:
                chars_per_word.append(sum(len(w) for w in words) / len(words))

        avg_sentences = sum(sentence_counts) / total if sentence_counts else 0
        avg_wps = sum(words_per_sentence) / len(words_per_sentence) if words_per_sentence else 0
        avg_cpw = sum(chars_per_word) / len(chars_per_word) if chars_per_word else 0

        # 4. Overlap between consecutive chunks
        overlaps = self._compute_overlaps(chunks)

        # Scoring
        # Uniformity: low std relative to mean = good
        cv = std_tc / mean_tc if mean_tc > 0 else 1
        uniformity_score = max(0, 100 * (1 - cv))

        # Outlier penalty
        outlier_score = max(0, 100 * (1 - outlier_ratio * 3))

        # Density score
        density_score = min(100, avg_density * 150)

        # Readability score (penalize very long sentences or very short chunks)
        readability_score = 100
        if avg_wps > 30:
            readability_score -= min(40, (avg_wps - 30) * 2)
        if avg_sentences < 2:
            readability_score -= 20
        readability_score = max(0, readability_score)

        score = (
            0.30 * uniformity_score
            + 0.25 * outlier_score
            + 0.25 * density_score
            + 0.20 * readability_score
        )

        metrics = {
            "total_chunks": total,
            "mean_tokens": round(mean_tc, 1),
            "std_tokens": round(std_tc, 1),
            "min_tokens_actual": min(token_counts),
            "max_tokens_actual": max(token_counts),
            "too_small": too_small,
            "too_large": too_large,
            "outlier_ratio": round(outlier_ratio, 4),
            "avg_density": round(avg_density, 4),
            "avg_sentences_per_chunk": round(avg_sentences, 1),
            "avg_words_per_sentence": round(avg_wps, 1),
            "avg_chars_per_word": round(avg_cpw, 1),
            "avg_overlap": round(sum(overlaps) / max(len(overlaps), 1), 4),
            "sub_scores": {
                "uniformity": round(uniformity_score, 1),
                "outliers": round(outlier_score, 1),
                "density": round(density_score, 1),
                "readability": round(readability_score, 1),
            },
        }

        # Chart data
        # Token count histogram
        tc_hist = self._histogram(token_counts, bins=25)

        # Per-source stats
        source_stats = collections.defaultdict(list)
        for i, c in enumerate(chunks):
            source_name = c[5] or "Inconnu"
            source_stats[source_name].append(token_counts[i])

        source_violin = []
        for source, tcs in source_stats.items():
            mean_s = sum(tcs) / len(tcs)
            min_s = min(tcs)
            max_s = max(tcs)
            q1, median, q3 = self._quartiles(tcs)
            source_violin.append(
                {
                    "source": source,
                    "count": len(tcs),
                    "mean": round(mean_s, 1),
                    "min": min_s,
                    "max": max_s,
                    "q1": q1,
                    "median": median,
                    "q3": q3,
                }
            )

        # Scatter: size vs density
        scatter = []
        for i in range(min(500, total)):
            scatter.append(
                {
                    "tokens": token_counts[i],
                    "density": round(densities[i], 3),
                    "doc_title": chunks[i][4][:50],
                }
            )

        chart_data = {
            "token_histogram": tc_hist,
            "source_violin": source_violin,
            "size_density_scatter": scatter,
            "thresholds": {
                "min": min_tokens,
                "max": max_tokens,
                "optimal": optimal_tokens,
            },
        }

        details = {
            "too_small_chunks": [
                {"chunk_id": str(c[0]), "doc_title": c[4][:80], "tokens": token_counts[i]}
                for i, c in enumerate(chunks)
                if token_counts[i] < min_tokens
            ][:30],
            "too_large_chunks": [
                {"chunk_id": str(c[0]), "doc_title": c[4][:80], "tokens": token_counts[i]}
                for i, c in enumerate(chunks)
                if token_counts[i] > max_tokens
            ][:30],
        }

        return score, metrics, chart_data, details

    def _compute_overlaps(self, chunks):
        """Compute token overlap between consecutive chunks of the same document."""
        overlaps = []
        prev_doc = None
        prev_tokens = set()
        for _, content, _, doc_id, _, _, _ in chunks:
            tokens = set(re.findall(r"\w+", content.lower()))
            if doc_id == prev_doc and tokens and prev_tokens:
                intersection = len(tokens & prev_tokens)
                union = len(tokens | prev_tokens)
                overlaps.append(intersection / union if union else 0)
            prev_doc = doc_id
            prev_tokens = tokens
        return overlaps

    def _histogram(self, values, bins=20):
        if not values:
            return []
        mn, mx = min(values), max(values)
        if mn == mx:
            return [{"bin_start": mn, "bin_end": mx, "count": len(values)}]
        step = (mx - mn) / bins
        result = []
        for i in range(bins):
            lo = mn + i * step
            hi = mn + (i + 1) * step
            cnt = (
                sum(1 for v in values if lo <= v < hi)
                if i < bins - 1
                else sum(1 for v in values if lo <= v <= hi)
            )
            result.append({"bin_start": round(lo, 1), "bin_end": round(hi, 1), "count": cnt})
        return result

    def _quartiles(self, values):
        s = sorted(values)
        n = len(s)
        if n == 0:
            return 0, 0, 0
        q1 = s[n // 4]
        median = s[n // 2]
        q3 = s[3 * n // 4]
        return q1, median, q3
