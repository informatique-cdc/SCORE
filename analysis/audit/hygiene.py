"""Axe 1 — Hygiène du corpus: dedup, near-dup, boilerplate, language, PII."""
import collections
import hashlib
import logging
import re

from .base import BaseAuditAxis

logger = logging.getLogger(__name__)

# PII / secret patterns
PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone_fr": re.compile(r"\b(?:\+33|0)\s*[1-9](?:[\s.-]*\d{2}){4}\b"),
    "phone_intl": re.compile(r"\b\+\d{1,3}[\s.-]?\d{4,14}\b"),
    "api_key": re.compile(r"\b(?:sk|pk|api[_-]?key)[_-]?[A-Za-z0-9]{20,}\b", re.I),
    "ip_address": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "secret_generic": re.compile(
        r"(?:password|secret|token|credentials?)\s*[:=]\s*\S+", re.I
    ),
}


class HygieneAxis(BaseAuditAxis):
    axis_key = "hygiene"
    axis_label = "Hygiène du corpus"

    def analyze(self):
        from ingestion.models import Document, DocumentChunk

        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="ready",
            )
            .select_related("document")
            .values_list("id", "content", "content_hash", "document_id", "document__title")
        )

        docs = list(
            Document.objects.filter(project=self.project, status="ready")
            .values_list("id", "title", "content_hash")
        )

        if not chunks:
            return 100.0, {"total_chunks": 0}, {}, {"message": "Aucun chunk à analyser"}

        total_chunks = len(chunks)
        total_docs = len(docs)

        # 1. Exact dedup via content_hash
        hash_counts = collections.Counter(c[2] for c in chunks)
        exact_dup_count = sum(v - 1 for v in hash_counts.values() if v > 1)
        exact_dup_ratio = exact_dup_count / total_chunks if total_chunks else 0

        # 2. Near-dup via MinHash LSH
        neardup_pairs, neardup_ratio = self._neardup_analysis(chunks)

        # 3. Boilerplate detection
        boilerplate_lines, boilerplate_ratio = self._boilerplate_analysis(chunks)

        # 4. Language homogeneity
        lang_dist, lang_score = self._language_analysis(docs, chunks)

        # 5. PII / secrets
        pii_findings, pii_ratio = self._pii_analysis(chunks)

        # Score computation
        cfg = self.config
        uniqueness_score = max(0, 100 * (1 - exact_dup_ratio * 5))
        neardup_score = max(0, 100 * (1 - neardup_ratio * 3))
        boilerplate_score = max(0, 100 * (1 - boilerplate_ratio * 3))
        pii_score = max(0, 100 * (1 - pii_ratio * 10))

        score = (
            0.30 * uniqueness_score
            + 0.20 * neardup_score
            + 0.20 * boilerplate_score
            + 0.15 * lang_score
            + 0.15 * pii_score
        )

        metrics = {
            "total_chunks": total_chunks,
            "total_docs": total_docs,
            "exact_duplicates": exact_dup_count,
            "exact_dup_ratio": round(exact_dup_ratio, 4),
            "neardup_pairs": len(neardup_pairs),
            "neardup_ratio": round(neardup_ratio, 4),
            "boilerplate_lines": len(boilerplate_lines),
            "boilerplate_ratio": round(boilerplate_ratio, 4),
            "language_distribution": lang_dist,
            "language_homogeneity": round(lang_score, 1),
            "pii_findings_count": len(pii_findings),
            "pii_ratio": round(pii_ratio, 4),
            "sub_scores": {
                "uniqueness": round(uniqueness_score, 1),
                "neardup": round(neardup_score, 1),
                "boilerplate": round(boilerplate_score, 1),
                "language": round(lang_score, 1),
                "pii": round(pii_score, 1),
            },
        }

        # Chart data
        # Chunk length distribution
        lengths = [len(c[1]) for c in chunks]
        length_bins = self._histogram(lengths, bins=20)

        # Dup distribution per document
        doc_dup_counts = collections.Counter()
        for h, cnt in hash_counts.items():
            if cnt > 1:
                for c in chunks:
                    if c[2] == h:
                        doc_dup_counts[str(c[3])] += 1

        dup_bar = [
            {"doc_id": did, "count": cnt}
            for did, cnt in doc_dup_counts.most_common(20)
        ]

        chart_data = {
            "length_histogram": length_bins,
            "dup_distribution": dup_bar,
            "language_pie": [
                {"language": lang, "count": cnt} for lang, cnt in lang_dist.items()
            ],
            "pii_by_type": self._pii_by_type(pii_findings),
        }

        details = {
            "exact_dup_hashes": [
                {"hash": h, "count": c} for h, c in hash_counts.most_common(20) if c > 1
            ],
            "neardup_pairs": neardup_pairs[:50],
            "boilerplate_lines": boilerplate_lines[:30],
            "pii_findings": pii_findings[:50],
        }

        return score, metrics, chart_data, details

    def _neardup_analysis(self, chunks):
        """MinHash LSH near-duplicate detection."""
        try:
            from datasketch import MinHash, MinHashLSH
        except ImportError:
            logger.warning("datasketch not installed, skipping near-dup analysis")
            return [], 0.0

        cfg = self.config
        num_perm = cfg.get("minhash_num_perm", 128)
        threshold = cfg.get("neardup_jaccard_threshold", 0.5)

        # Sample if too many chunks
        sample = chunks[:2000]

        minhashes = {}
        for chunk_id, content, _, doc_id, _ in sample:
            m = MinHash(num_perm=num_perm)
            # 3-word shingles
            words = content.lower().split()
            for i in range(len(words) - 2):
                shingle = " ".join(words[i : i + 3])
                m.update(shingle.encode("utf-8"))
            minhashes[str(chunk_id)] = (m, str(doc_id))

        lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        for cid, (mh, _) in minhashes.items():
            try:
                lsh.insert(cid, mh)
            except ValueError:
                pass  # duplicate key

        pairs = []
        seen = set()
        for cid, (mh, doc_id) in minhashes.items():
            results = lsh.query(mh)
            for r in results:
                if r != cid:
                    pair_key = tuple(sorted([cid, r]))
                    if pair_key not in seen:
                        seen.add(pair_key)
                        pairs.append({
                            "chunk_a": cid,
                            "chunk_b": r,
                            "doc_a": doc_id,
                            "doc_b": minhashes[r][1],
                        })

        neardup_ratio = len(pairs) / max(len(sample), 1)
        return pairs, neardup_ratio

    def _boilerplate_analysis(self, chunks):
        """Detect boilerplate lines appearing in many documents."""
        threshold = self.config.get("boilerplate_freq_threshold", 0.3)
        doc_ids = set(c[3] for c in chunks)
        total_docs = max(len(doc_ids), 1)

        # Count normalized line frequency across documents
        line_doc_count = collections.Counter()
        for _, content, _, doc_id, _ in chunks:
            seen_in_doc = set()
            for line in content.split("\n"):
                normalized = line.strip().lower()
                if len(normalized) > 10 and normalized not in seen_in_doc:
                    seen_in_doc.add(normalized)
                    line_doc_count[normalized] += 1

        boilerplate = [
            {"line": line[:200], "doc_count": cnt, "ratio": round(cnt / total_docs, 3)}
            for line, cnt in line_doc_count.most_common(50)
            if cnt / total_docs > threshold
        ]

        total_boilerplate_occurrences = sum(
            cnt for _, cnt in line_doc_count.items() if cnt / total_docs > threshold
        )
        total_lines = sum(len(c[1].split("\n")) for c in chunks)
        ratio = total_boilerplate_occurrences / max(total_lines, 1)

        return boilerplate, ratio

    def _language_analysis(self, docs, chunks):
        """Detect language distribution across documents."""
        try:
            import langid
        except ImportError:
            logger.warning("langid not installed, skipping language analysis")
            return {"unknown": len(docs)}, 50.0

        lang_counts = collections.Counter()
        # Sample up to 200 chunks for language detection
        sample = chunks[:200]
        for _, content, _, _, _ in sample:
            text = content[:500]
            if len(text.strip()) < 20:
                continue
            lang, _ = langid.classify(text)
            lang_counts[lang] += 1

        if not lang_counts:
            return {"unknown": len(docs)}, 50.0

        total = sum(lang_counts.values())
        dominant_ratio = lang_counts.most_common(1)[0][1] / total
        # Score: 100 if all same lang, drops with fragmentation
        lang_score = min(100, dominant_ratio * 100)

        return dict(lang_counts), lang_score

    def _pii_analysis(self, chunks):
        """Scan for PII and secrets."""
        findings = []
        total_with_pii = 0
        for chunk_id, content, _, doc_id, doc_title in chunks[:1000]:
            chunk_pii = []
            for pii_type, pattern in PII_PATTERNS.items():
                matches = pattern.findall(content)
                if matches:
                    chunk_pii.append({
                        "type": pii_type,
                        "count": len(matches),
                        "sample": matches[0][:50] + "..." if len(matches[0]) > 50 else matches[0],
                    })
            if chunk_pii:
                total_with_pii += 1
                findings.append({
                    "chunk_id": str(chunk_id),
                    "doc_id": str(doc_id),
                    "doc_title": doc_title[:100],
                    "pii_types": chunk_pii,
                })

        pii_ratio = total_with_pii / max(len(chunks[:1000]), 1)
        return findings, pii_ratio

    def _pii_by_type(self, findings):
        """Aggregate PII counts by type for chart."""
        type_counts = collections.Counter()
        for f in findings:
            for p in f["pii_types"]:
                type_counts[p["type"]] += p["count"]
        return [{"type": t, "count": c} for t, c in type_counts.most_common()]

    def _histogram(self, values, bins=20):
        """Simple histogram binning."""
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
            cnt = sum(1 for v in values if lo <= v < hi) if i < bins - 1 else sum(1 for v in values if lo <= v <= hi)
            result.append({"bin_start": round(lo, 1), "bin_end": round(hi, 1), "count": cnt})
        return result
