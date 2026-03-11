"""
Hallucination risk detection for RAG systems.

Identifies document-level risk factors that can cause LLM hallucinations
when documents are used as RAG context:

  1. Acronym detection:
     a. Undefined acronyms (used without expansion in the document)
     b. Ambiguous acronyms (multiple known expansions)
     c. Cross-document conflicting acronyms (same acronym, different meanings)
  2. Jargon without context: domain-specific terms used without definition
  3. Hedging language: uncertain phrases the LLM may present as facts
  4. Implicit knowledge: references to concepts/entities never defined

Outputs a ranked list of hallucination risk items with severity and risk scores.
"""

import collections
import logging
import re

from django.conf import settings

from analysis.models import HallucinationReport
from ingestion.models import Document, DocumentChunk

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────

# Matches uppercase acronyms (2-8 chars), excluding common English words
ACRONYM_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7})\b")

# Matches parenthetical definitions: "term (ACRONYM)" or "ACRONYM (full term)"
EXPANSION_PAREN_RE = re.compile(
    r"(?:"
    r"([A-Z][A-Z0-9]{1,7})\s*\(([^)]{3,80})\)"  # ACRONYM (expansion)
    r"|"
    r"([^(]{3,80})\s*\(([A-Z][A-Z0-9]{1,7})\)"  # expansion (ACRONYM)
    r")"
)

# Common non-acronym uppercase tokens to exclude
ACRONYM_EXCLUSIONS = {
    "THE",
    "AND",
    "FOR",
    "NOT",
    "BUT",
    "ALL",
    "ARE",
    "WAS",
    "HAS",
    "HIS",
    "HER",
    "HIM",
    "ITS",
    "OUR",
    "WHO",
    "HOW",
    "WHY",
    "CAN",
    "MAY",
    "DES",
    "LES",
    "UNE",
    "EST",
    "PAR",
    "SUR",
    "AUX",
    "CES",
    "SON",
    "SES",
    "NOS",
    "VOS",
    "QUI",
    "QUE",
    "DANS",
    "AVEC",
    "POUR",
    "PLUS",
    "SANS",
    "SOUS",
    "TOUT",
    "MISE",
    "NOTE",
    "NULL",
    "TRUE",
    "HTTP",
    "HTTPS",
    "HTML",
    "JSON",
    "YAML",
    "TODO",
    "INFO",
    "NONE",
    "PDF",
    "CSV",
    "SQL",
    "URL",
    "URI",
    "XML",
    "SSH",
    "FTP",
    "DNS",
}

# Hedging language patterns (French and English)
HEDGING_PATTERNS = [
    re.compile(r"\b(?:il semble(?:rait)?|il para[iî]t(?:rait)?)\b", re.I),
    re.compile(r"\b(?:probablement|vraisemblablement|peut-être|éventuellement)\b", re.I),
    re.compile(r"\b(?:on pense que|on estime que|il est possible que)\b", re.I),
    re.compile(r"\b(?:environ|approximativement|à peu près|autour de)\b", re.I),
    re.compile(r"\b(?:dans certains cas|dans la plupart des cas|en général)\b", re.I),
    re.compile(r"\b(?:it (?:seems?|appears?)|(?:it is )?(?:likely|possible|probable))\b", re.I),
    re.compile(r"\b(?:approximately|roughly|about|around)\b", re.I),
    re.compile(r"\b(?:might|could|may|should)\s+(?:be|have|cause)\b", re.I),
    re.compile(r"\b(?:it is believed|it is thought|it is assumed)\b", re.I),
    re.compile(r"\b(?:in some cases|in most cases|generally|typically)\b", re.I),
]


class HallucinationDetector:
    """Detect hallucination risk factors in the document corpus."""

    def __init__(self, tenant, analysis_job, project, on_progress=None, config=None):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.on_progress = on_progress
        self.config = (
            config if config is not None else settings.ANALYSIS_CONFIG.get("hallucination", {})
        )
        self.min_acronym_freq = self.config.get("min_acronym_frequency", 2)
        self.jargon_tfidf_threshold = self.config.get("jargon_tfidf_threshold", 0.15)
        self.hedging_density_threshold = self.config.get("hedging_density_threshold", 0.02)
        self.max_items = self.config.get("max_items_per_type", 50)

    def run(self) -> list[HallucinationReport]:
        """Run all hallucination risk detection strategies."""
        logger.info(
            "Starting hallucination risk detection for tenant=%s",
            self.tenant.slug,
        )

        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status=Document.Status.READY,
            )
            .select_related("document")
            .values_list("id", "content", "document_id", "document__title")
        )

        if not chunks:
            return []

        doc_texts = collections.defaultdict(list)
        doc_titles = {}
        for chunk_id, content, doc_id, doc_title in chunks:
            doc_texts[str(doc_id)].append(content)
            doc_titles[str(doc_id)] = doc_title

        logger.info(
            "[hallucination] %d chunks across %d documents",
            len(chunks),
            len(doc_texts),
        )

        reports = []

        # Strategy 1: Acronym analysis
        logger.info("[hallucination] Strategy 1/4: Acronym analysis...")
        acronym_reports = self._detect_acronym_risks(doc_texts, doc_titles)
        reports.extend(acronym_reports)
        logger.info(
            "[hallucination] Strategy 1/4 done: %d acronym risks",
            len(acronym_reports),
        )

        if self.on_progress:
            self.on_progress(1, 4)

        # Strategy 2: Jargon without context
        logger.info("[hallucination] Strategy 2/4: Jargon without context...")
        jargon_reports = self._detect_jargon_risks(doc_texts, doc_titles)
        reports.extend(jargon_reports)
        logger.info(
            "[hallucination] Strategy 2/4 done: %d jargon risks",
            len(jargon_reports),
        )

        if self.on_progress:
            self.on_progress(2, 4)

        # Strategy 3: Hedging language
        logger.info("[hallucination] Strategy 3/4: Hedging language...")
        hedging_reports = self._detect_hedging_risks(doc_texts, doc_titles)
        reports.extend(hedging_reports)
        logger.info(
            "[hallucination] Strategy 3/4 done: %d hedging risks",
            len(hedging_reports),
        )

        if self.on_progress:
            self.on_progress(3, 4)

        # Strategy 4: Implicit knowledge gaps
        logger.info("[hallucination] Strategy 4/4: Implicit knowledge gaps...")
        implicit_reports = self._detect_implicit_knowledge(doc_texts, doc_titles)
        reports.extend(implicit_reports)
        logger.info(
            "[hallucination] Strategy 4/4 done: %d implicit knowledge risks",
            len(implicit_reports),
        )

        if self.on_progress:
            self.on_progress(4, 4)

        logger.info("Hallucination risk detection found %d items", len(reports))
        return reports

    # ── Strategy 1: Acronym analysis ──────────────────────────────────

    def _detect_acronym_risks(self, doc_texts, doc_titles):
        """Detect undefined, ambiguous, and conflicting acronyms."""
        # Phase 1: Extract all acronyms and their expansions per document
        doc_acronyms = {}  # {doc_id: {acronym: count}}
        doc_expansions = {}  # {doc_id: {acronym: expansion}}
        corpus_acronyms = collections.Counter()
        corpus_expansions = collections.defaultdict(
            lambda: collections.defaultdict(set)
        )  # {acronym: {expansion: {doc_ids}}}

        for doc_id, text_parts in doc_texts.items():
            full_text = " ".join(text_parts)
            acronyms = self._extract_acronyms(full_text)
            expansions = self._extract_expansions(full_text)

            doc_acronyms[doc_id] = acronyms
            doc_expansions[doc_id] = expansions

            for acr, count in acronyms.items():
                corpus_acronyms[acr] += count
                if acr in expansions:
                    corpus_expansions[acr][expansions[acr].lower()].add(doc_id)

        reports = []

        # Phase 2: Identify risks
        for acr, total_count in corpus_acronyms.most_common():
            if total_count < self.min_acronym_freq:
                continue

            if acr in ACRONYM_EXCLUSIONS:
                continue

            # Find which docs use this acronym
            docs_using = [doc_id for doc_id, acrs in doc_acronyms.items() if acr in acrs]
            # Find which docs define this acronym
            docs_defining = [doc_id for doc_id, exps in doc_expansions.items() if acr in exps]
            # Find which docs use it without defining it
            docs_undefined = [d for d in docs_using if d not in docs_defining]

            # Known expansions across corpus
            all_expansions = corpus_expansions.get(acr, {})
            expansion_list = [
                {
                    "expansion": exp,
                    "doc_ids": list(dids)[:5],
                    "doc_titles": [doc_titles.get(d, "") for d in list(dids)[:5]],
                }
                for exp, dids in all_expansions.items()
            ]

            # Case A: Conflicting acronym (multiple different expansions)
            if len(all_expansions) > 1:
                risk_score = min(1.0, 0.6 + 0.1 * len(all_expansions))
                report = HallucinationReport.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    risk_type=HallucinationReport.RiskType.CONFLICTING_ACRONYM,
                    title=f"Acronyme contradictoire : {acr}",
                    description=(
                        f"L'acronyme « {acr} » a {len(all_expansions)} définitions "
                        f"différentes dans le corpus. Cela peut induire le RAG en erreur "
                        f"lors de la récupération de passages."
                    ),
                    severity="high",
                    term=acr,
                    expansions=expansion_list,
                    doc_count=len(docs_using),
                    risk_score=risk_score,
                    evidence={
                        "total_occurrences": total_count,
                        "docs_using": docs_using[:10],
                        "docs_defining": docs_defining[:10],
                        "expansion_count": len(all_expansions),
                    },
                )
                reports.append(report)
                if len(reports) >= self.max_items:
                    break
                continue

            # Case B: Undefined acronym (used but never defined anywhere)
            if not docs_defining and len(docs_using) >= 1:
                risk_score = min(1.0, 0.3 + 0.05 * len(docs_using))
                report = HallucinationReport.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    risk_type=HallucinationReport.RiskType.UNDEFINED_ACRONYM,
                    title=f"Acronyme non défini : {acr}",
                    description=(
                        f"L'acronyme « {acr} » est utilisé dans {len(docs_using)} "
                        f"document(s) mais n'est jamais défini. Le LLM pourrait "
                        f"halluciner une signification incorrecte."
                    ),
                    severity="medium" if len(docs_using) < 5 else "high",
                    term=acr,
                    expansions=[],
                    doc_count=len(docs_using),
                    risk_score=risk_score,
                    evidence={
                        "total_occurrences": total_count,
                        "docs_using": docs_using[:10],
                        "doc_titles": [doc_titles.get(d, "") for d in docs_using[:10]],
                    },
                )
                reports.append(report)
                if len(reports) >= self.max_items:
                    break
                continue

            # Case C: Ambiguous acronym (defined in some docs, undefined in many)
            if docs_undefined and len(docs_undefined) > len(docs_defining):
                ratio = len(docs_undefined) / len(docs_using)
                risk_score = min(1.0, 0.2 + ratio * 0.5)
                report = HallucinationReport.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    risk_type=HallucinationReport.RiskType.AMBIGUOUS_ACRONYM,
                    title=f"Acronyme partiellement défini : {acr}",
                    description=(
                        f"L'acronyme « {acr} » est défini dans {len(docs_defining)} "
                        f"document(s) mais utilisé sans définition dans "
                        f"{len(docs_undefined)} document(s)."
                    ),
                    severity="low" if ratio < 0.5 else "medium",
                    term=acr,
                    expansions=expansion_list,
                    doc_count=len(docs_using),
                    risk_score=risk_score,
                    evidence={
                        "total_occurrences": total_count,
                        "docs_using": docs_using[:10],
                        "docs_defining": docs_defining[:10],
                        "docs_undefined": docs_undefined[:10],
                        "undefined_ratio": round(ratio, 2),
                    },
                )
                reports.append(report)
                if len(reports) >= self.max_items:
                    break

        return reports

    def _extract_acronyms(self, text):
        """Extract acronyms and their frequency from text."""
        acronyms = collections.Counter()
        for match in ACRONYM_RE.finditer(text):
            acr = match.group(1)
            if len(acr) >= 2 and acr not in ACRONYM_EXCLUSIONS:
                acronyms[acr] += 1
        return acronyms

    def _extract_expansions(self, text):
        """Extract acronym-expansion pairs from parenthetical definitions."""
        expansions = {}
        for match in EXPANSION_PAREN_RE.finditer(text):
            if match.group(1) and match.group(2):
                # ACRONYM (expansion)
                acr = match.group(1)
                exp = match.group(2).strip()
                if self._validate_expansion(acr, exp):
                    expansions[acr] = exp
            elif match.group(3) and match.group(4):
                # expansion (ACRONYM)
                exp = match.group(3).strip()
                acr = match.group(4)
                if self._validate_expansion(acr, exp):
                    expansions[acr] = exp
        return expansions

    def _validate_expansion(self, acronym, expansion):
        """Check that an expansion plausibly matches its acronym."""
        # Basic validation: expansion should have at least as many words as
        # the acronym has letters, and the initial letters should roughly match
        words = expansion.split()
        if len(words) < 2:
            return False
        # Check if first letters of words match acronym letters
        initials = "".join(w[0].upper() for w in words if w)
        # Allow partial match (at least half the letters)
        matches = sum(1 for a, b in zip(acronym, initials) if a == b)
        return matches >= len(acronym) * 0.5

    # ── Strategy 2: Jargon without context ────────────────────────────

    def _detect_jargon_risks(self, doc_texts, doc_titles):
        """Detect domain-specific terms used without definition or context."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError:
            logger.warning("scikit-learn not available, skipping jargon detection")
            return []

        from nsg.stopwords import get_stopwords_for_sklearn

        # Build corpus-level TF-IDF
        doc_ids = list(doc_texts.keys())
        texts = [" ".join(parts) for parts in doc_texts.values()]

        if len(texts) < 2:
            return []

        vectorizer = TfidfVectorizer(
            max_features=3000,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.8,
            stop_words=get_stopwords_for_sklearn(),
        )

        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
        except ValueError:
            return []

        feature_names = vectorizer.get_feature_names_out()

        # Compute corpus-wide average TF-IDF per term
        mean_tfidf = tfidf_matrix.mean(axis=0).A1
        # Terms with high average TF-IDF are domain-specific
        high_tfidf_indices = [
            i for i, val in enumerate(mean_tfidf) if val >= self.jargon_tfidf_threshold
        ]

        if not high_tfidf_indices:
            return []

        # For each high-TF-IDF term, check if it's defined/explained
        # A term is "defined" if the document contains patterns like:
        # "X is ...", "X means ...", "X : ...", "X désigne ..."
        definition_patterns = [
            re.compile(r"\b{term}\b\s+(?:est|signifie|désigne|représente|correspond)\s", re.I),
            re.compile(r"\b{term}\b\s*[:=]\s*", re.I),
            re.compile(r"\b{term}\b\s*\([^)]+\)", re.I),
        ]

        reports = []
        sorted_indices = sorted(high_tfidf_indices, key=lambda i: -mean_tfidf[i])

        for idx in sorted_indices[: self.max_items * 2]:
            term = str(feature_names[idx])
            if len(term) < 4 or term.isnumeric():
                continue

            # Check which docs use this term
            col = tfidf_matrix.getcol(idx).toarray().flatten()
            docs_with_term = [doc_ids[i] for i, val in enumerate(col) if val > 0]

            if len(docs_with_term) < 2:
                continue

            # Check which docs define it
            docs_defining = []
            for doc_id in docs_with_term:
                full_text = " ".join(doc_texts[doc_id])
                for pat_template in definition_patterns:
                    pat = re.compile(
                        pat_template.pattern.replace("{term}", re.escape(term)),
                        pat_template.flags,
                    )
                    if pat.search(full_text):
                        docs_defining.append(doc_id)
                        break

            docs_undefined = [d for d in docs_with_term if d not in docs_defining]

            if not docs_undefined or len(docs_undefined) < 2:
                continue

            undefined_ratio = len(docs_undefined) / len(docs_with_term)
            if undefined_ratio < 0.5:
                continue

            risk_score = min(1.0, 0.2 + undefined_ratio * 0.4 + mean_tfidf[idx] * 2)

            report = HallucinationReport.objects.create(
                tenant=self.tenant,
                project=self.project,
                analysis_job=self.job,
                risk_type=HallucinationReport.RiskType.JARGON_NO_CONTEXT,
                title=f"Jargon sans contexte : {term}",
                description=(
                    f"Le terme spécialisé « {term} » est utilisé dans "
                    f"{len(docs_with_term)} documents mais n'est défini que dans "
                    f"{len(docs_defining)} d'entre eux. Le RAG pourrait récupérer "
                    f"des passages contenant ce terme sans contexte suffisant."
                ),
                severity="low" if undefined_ratio < 0.7 else "medium",
                term=term,
                doc_count=len(docs_with_term),
                risk_score=round(risk_score, 3),
                evidence={
                    "tfidf_score": round(float(mean_tfidf[idx]), 4),
                    "docs_with_term": docs_with_term[:10],
                    "docs_defining": docs_defining[:10],
                    "docs_undefined": docs_undefined[:10],
                    "undefined_ratio": round(undefined_ratio, 2),
                },
            )
            reports.append(report)

            if len(reports) >= self.max_items:
                break

        return reports

    # ── Strategy 3: Hedging language ──────────────────────────────────

    def _detect_hedging_risks(self, doc_texts, doc_titles):
        """Detect documents with high density of hedging/uncertain language."""
        reports = []

        doc_hedging = []
        for doc_id, text_parts in doc_texts.items():
            full_text = " ".join(text_parts)
            word_count = len(full_text.split())
            if word_count < 50:
                continue

            hedging_matches = []
            for pattern in HEDGING_PATTERNS:
                for match in pattern.finditer(full_text):
                    hedging_matches.append(
                        {
                            "phrase": match.group(0),
                            "position": match.start(),
                            "context": full_text[max(0, match.start() - 40) : match.end() + 40],
                        }
                    )

            density = len(hedging_matches) / word_count if word_count > 0 else 0
            if density >= self.hedging_density_threshold and len(hedging_matches) >= 3:
                doc_hedging.append((doc_id, density, hedging_matches))

        # Sort by density
        doc_hedging.sort(key=lambda x: -x[1])

        for doc_id, density, matches in doc_hedging[: self.max_items]:
            risk_score = min(1.0, density / self.hedging_density_threshold * 0.5)

            unique_phrases = list({m["phrase"].lower() for m in matches})

            report = HallucinationReport.objects.create(
                tenant=self.tenant,
                project=self.project,
                analysis_job=self.job,
                risk_type=HallucinationReport.RiskType.HEDGING_LANGUAGE,
                title=f"Langage incertain : {doc_titles.get(doc_id, 'Document')}",
                description=(
                    f"Le document « {doc_titles.get(doc_id, '')} » contient "
                    f"{len(matches)} expression(s) de langage incertain "
                    f"(densité : {density:.1%}). Le LLM pourrait présenter ces "
                    f"informations incertaines comme des faits établis."
                ),
                severity="low" if density < 0.04 else "medium",
                term=", ".join(unique_phrases[:5]),
                document_id=doc_id,
                doc_count=1,
                risk_score=round(risk_score, 3),
                evidence={
                    "hedging_count": len(matches),
                    "density": round(density, 4),
                    "unique_phrases": unique_phrases[:20],
                    "examples": [m["context"] for m in matches[:10]],
                },
            )
            reports.append(report)

        return reports

    # ── Strategy 4: Implicit knowledge gaps ───────────────────────────

    def _detect_implicit_knowledge(self, doc_texts, doc_titles):
        """Detect references to concepts/entities that are never defined."""
        # Look for patterns indicating implicit knowledge:
        # - "le/la/les [term] mentionné(e)(s)" without prior mention
        # - "comme décrit dans..." without a reference
        # - Dangling references: "ce processus", "cette procédure" used
        #   without a clear antecedent

        implicit_patterns = [
            re.compile(
                r"\b(?:le|la|les)\s+(\w{4,})\s+(?:mentionné|précité|susmentionné|ci-dessus)",
                re.I,
            ),
            re.compile(
                r"\b(?:comme|tel que)\s+(?:décrit|défini|mentionné|prévu)\s+(?:dans|par|au)\b",
                re.I,
            ),
            re.compile(
                r"\b(?:cf\.|voir|se référer à|se reporter à)\s+(.{3,60}?)(?:\.|$)",
                re.I,
            ),
            re.compile(
                r"\b(?:ce|cette|ces)\s+(?:processus|procédure|méthode|outil|système|module|service|composant|application)\b",
                re.I,
            ),
        ]

        reports = []
        doc_scores = []

        for doc_id, text_parts in doc_texts.items():
            full_text = " ".join(text_parts)
            word_count = len(full_text.split())
            if word_count < 50:
                continue

            implicit_matches = []
            for pattern in implicit_patterns:
                for match in pattern.finditer(full_text):
                    implicit_matches.append(
                        {
                            "phrase": match.group(0)[:100],
                            "context": full_text[max(0, match.start() - 50) : match.end() + 50][
                                :200
                            ],
                        }
                    )

            if len(implicit_matches) >= 3:
                density = len(implicit_matches) / word_count
                doc_scores.append((doc_id, density, implicit_matches))

        doc_scores.sort(key=lambda x: -x[1])

        for doc_id, density, matches in doc_scores[: self.max_items]:
            risk_score = min(1.0, 0.2 + density * 20)
            unique_phrases = list({m["phrase"][:60] for m in matches})

            report = HallucinationReport.objects.create(
                tenant=self.tenant,
                project=self.project,
                analysis_job=self.job,
                risk_type=HallucinationReport.RiskType.IMPLICIT_KNOWLEDGE,
                title=(f"Connaissances implicites : {doc_titles.get(doc_id, 'Document')}"),
                description=(
                    f"Le document « {doc_titles.get(doc_id, '')} » contient "
                    f"{len(matches)} référence(s) à des connaissances supposées "
                    f"connues (renvois, mentions implicites). Le RAG pourrait "
                    f"récupérer ces passages sans le contexte nécessaire."
                ),
                severity="low" if len(matches) < 5 else "medium",
                term=", ".join(unique_phrases[:3]),
                document_id=doc_id,
                doc_count=1,
                risk_score=round(risk_score, 3),
                evidence={
                    "implicit_count": len(matches),
                    "density": round(density, 4),
                    "examples": [m["context"] for m in matches[:10]],
                    "unique_phrases": unique_phrases[:20],
                },
            )
            reports.append(report)

        return reports
