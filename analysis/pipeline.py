"""Pipeline orchestration functions for analysis and audit phases.

Extracted from analysis/tasks.py to reduce file size.
"""

import logging

from django.utils import timezone

from analysis.constants import AUDIT_AXIS_LABELS
from analysis.tasks import (
    ANALYSIS_PHASE_ORDER,
    UNIFIED_PROGRESS,
    _build_effective_config,
    _cleanup_phase,
    _collect_existing_stats,
    _make_progress_cb,
    _update_phase,
)
from score.scoring import grade as _audit_grade

logger = logging.getLogger(__name__)


def run_analysis_phases(job, collector=None, resume_from=None):
    """Run the LLM analysis phases. Updates progress 0-55%.

    If *resume_from* is set to an analysis phase value (e.g. ``"gaps"``),
    phases before it are skipped and the resume target is cleaned up
    before re-running.
    """
    from django.conf import settings

    from analysis.claims import ClaimsExtractor
    from analysis.clustering import TopicClusterEngine
    from analysis.contradictions import ContradictionDetector
    from analysis.duplicates import DuplicateDetector
    from analysis.gaps import GapDetector
    from analysis.models import AnalysisJob, Claim, DuplicateGroup, TopicCluster
    from ingestion.models import Document

    tenant = job.tenant
    project = job.project
    effective = _build_effective_config(job)

    resume_idx = ANALYSIS_PHASE_ORDER.index(resume_from) if resume_from else 0

    def _should_skip(phase_key):
        return ANALYSIS_PHASE_ORDER.index(phase_key) < resume_idx

    def _is_resume_target(phase_key):
        return resume_from and ANALYSIS_PHASE_ORDER.index(phase_key) == resume_idx

    doc_count = Document.objects.filter(project=project, status=Document.Status.READY).count()

    # ------------------------------------------------------------------
    # Phases 1 & 2: Duplicate detection + Claims extraction (parallel)
    # ------------------------------------------------------------------
    skip_dup = _should_skip("duplicates")
    skip_claims = _should_skip("claims")
    run_dup = not skip_dup
    run_claims = not skip_claims

    if skip_dup and skip_claims:
        logger.info("Phases 1-2 skipped (resume): duplicates + claims")
        if collector:
            existing_dup = DuplicateGroup.objects.filter(analysis_job=job).count()
            collector.start_phase(
                "duplicates", "Détection des doublons", sort_order=0, items_in=doc_count
            )
            collector.end_phase(items_out=existing_dup, status="skipped")
            existing_claims = Claim.objects.filter(project=project).count()
            collector.start_phase(
                "claims", "Extraction des affirmations", sort_order=1, items_in=doc_count
            )
            collector.end_phase(items_out=existing_claims, status="skipped")
    elif run_dup and run_claims:
        import time as _time
        from concurrent.futures import ThreadPoolExecutor

        import django.db

        from analysis.trace import PhaseEventBuffer
        from llm.client import get_llm_client
        from vectorstore.store import get_vector_store

        if _is_resume_target("duplicates"):
            _cleanup_phase(job, "duplicates")
        if _is_resume_target("claims"):
            _cleanup_phase(job, "claims")

        _update_phase(job, AnalysisJob.Phase.DUPLICATES, UNIFIED_PROGRESS["duplicates"])

        dup_buffer = PhaseEventBuffer()
        claims_buffer = PhaseEventBuffer()
        llm_client = get_llm_client()
        vec_store = get_vector_store()

        dup_result = {"groups": None, "error": None, "duration": 0.0}
        claims_result = {"count": None, "error": None, "duration": 0.0}

        dup_progress_cb = _make_progress_cb(job.pk, "Vérification LLM des doublons")

        def _run_duplicates():
            django.db.connections.close_all()
            llm_client._trace_local.trace = dup_buffer
            vec_store._trace_local.trace = dup_buffer
            t0 = _time.monotonic()
            try:
                detector = DuplicateDetector(
                    tenant,
                    job,
                    project,
                    on_progress=dup_progress_cb,
                    config=effective.get("duplicate"),
                )
                dup_result["groups"] = detector.run()
            except Exception as exc:
                dup_result["error"] = exc
            finally:
                dup_result["duration"] = _time.monotonic() - t0
                llm_client._trace_local.trace = None
                vec_store._trace_local.trace = None

        claims_progress_cb = _make_progress_cb(job.pk, "Extraction des affirmations")

        def _run_claims():
            django.db.connections.close_all()
            llm_client._trace_local.trace = claims_buffer
            vec_store._trace_local.trace = claims_buffer
            t0 = _time.monotonic()
            try:
                extractor = ClaimsExtractor(
                    tenant,
                    project,
                    on_progress=claims_progress_cb,
                    config=effective.get("contradiction"),
                )
                claims_result["count"] = extractor.extract_all()
            except Exception as exc:
                claims_result["error"] = exc
            finally:
                claims_result["duration"] = _time.monotonic() - t0
                llm_client._trace_local.trace = None
                vec_store._trace_local.trace = None

        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(_run_duplicates)
            pool.submit(_run_claims)

        if collector:
            collector.start_phase(
                "duplicates", "Détection des doublons", sort_order=0, items_in=doc_count
            )
            dup_buffer.replay_into(collector)
        if dup_result["error"]:
            logger.exception("Phase 1 failed: %s", dup_result["error"])
            if collector:
                collector.end_phase(status="failed", error_message=str(dup_result["error"])[:500])
            raise dup_result["error"]
        else:
            logger.info("Phase 1 complete: %d duplicate groups", len(dup_result["groups"]))
            if collector:
                collector.end_phase(items_out=len(dup_result["groups"]))

        if collector:
            collector.start_phase(
                "claims", "Extraction des affirmations", sort_order=1, items_in=doc_count
            )
            claims_buffer.replay_into(collector)
        if claims_result["error"]:
            logger.exception("Phase 2 failed: %s", claims_result["error"])
            if collector:
                collector.end_phase(
                    status="failed", error_message=str(claims_result["error"])[:500]
                )
            raise claims_result["error"]
        else:
            logger.info("Phase 2 complete: %d claims extracted", claims_result["count"])
            if collector:
                collector.end_phase(items_out=claims_result["count"])
    else:
        if skip_dup:
            logger.info("Phase 1 skipped (resume): duplicates")
            if collector:
                existing = DuplicateGroup.objects.filter(analysis_job=job).count()
                collector.start_phase(
                    "duplicates", "Détection des doublons", sort_order=0, items_in=doc_count
                )
                collector.end_phase(items_out=existing, status="skipped")
        else:
            if _is_resume_target("duplicates"):
                _cleanup_phase(job, "duplicates")
            _update_phase(job, AnalysisJob.Phase.DUPLICATES, UNIFIED_PROGRESS["duplicates"])
            if collector:
                collector.start_phase(
                    "duplicates", "Détection des doublons", sort_order=0, items_in=doc_count
                )
            try:
                dup_cb = _make_progress_cb(job.pk, "Vérification LLM des doublons")
                detector = DuplicateDetector(
                    tenant, job, project, on_progress=dup_cb, config=effective.get("duplicate")
                )
                dup_groups = detector.run()
                logger.info("Phase 1 complete: %d duplicate groups", len(dup_groups))
                if collector:
                    collector.end_phase(items_out=len(dup_groups))
            except Exception as exc:
                if collector:
                    collector.end_phase(status="failed", error_message=str(exc)[:500])
                raise

        if skip_claims:
            logger.info("Phase 2 skipped (resume): claims")
            if collector:
                existing = Claim.objects.filter(project=project).count()
                collector.start_phase(
                    "claims", "Extraction des affirmations", sort_order=1, items_in=doc_count
                )
                collector.end_phase(items_out=existing, status="skipped")
        else:
            if _is_resume_target("claims"):
                _cleanup_phase(job, "claims")
            _update_phase(job, AnalysisJob.Phase.CLAIMS, UNIFIED_PROGRESS["claims"])
            if collector:
                collector.start_phase(
                    "claims", "Extraction des affirmations", sort_order=1, items_in=doc_count
                )
            try:
                claims_cb = _make_progress_cb(job.pk, "Extraction des affirmations")
                extractor = ClaimsExtractor(
                    tenant, project, on_progress=claims_cb, config=effective.get("contradiction")
                )
                claims_count = extractor.extract_all()
                logger.info("Phase 2 complete: %d claims extracted", claims_count)
                if collector:
                    collector.end_phase(items_out=claims_count)
            except Exception as exc:
                if collector:
                    collector.end_phase(status="failed", error_message=str(exc)[:500])
                raise

    # ------------------------------------------------------------------
    # Phase 3: Semantic graph construction
    # ------------------------------------------------------------------
    nsg = None
    sg_config = settings.SEMANTIC_GRAPH_CONFIG
    if _should_skip("semantic_graph"):
        logger.info("Phase 3 skipped (resume): semantic_graph")
        if collector:
            collector.start_phase("semantic_graph", "Graphe sémantique", sort_order=2, items_in=0)
            collector.end_phase(status="skipped")
    elif sg_config.get("enabled", False):
        if _is_resume_target("semantic_graph"):
            _cleanup_phase(job, "semantic_graph")
        claim_count = Claim.objects.filter(project=project).count()
        _update_phase(job, AnalysisJob.Phase.SEMANTIC_GRAPH, UNIFIED_PROGRESS["semantic_graph"])
        if collector:
            collector.start_phase(
                "semantic_graph", "Graphe sémantique", sort_order=2, items_in=claim_count
            )
        try:
            from analysis.semantic_graph import ProjectGraphBuilder

            graph_builder = ProjectGraphBuilder(tenant, job, project)
            nsg = graph_builder.run()
            logger.info(
                "Phase 3 complete: semantic graph built (%d nodes)", nsg.graph.number_of_nodes()
            )
            if collector:
                collector.end_phase(items_out=0)
        except ImportError as exc:
            logger.warning("Phase 3 skipped: %s", exc)
            if collector:
                collector.end_phase(status="skipped", error_message=str(exc)[:500])
        except Exception as exc:
            if collector:
                collector.end_phase(status="failed", error_message=str(exc)[:500])
            raise
    else:
        logger.info("Phase 3 skipped: semantic graph disabled")
        if collector:
            collector.start_phase("semantic_graph", "Graphe sémantique", sort_order=2, items_in=0)
            collector.end_phase(status="skipped")

    if nsg is None and sg_config.get("enabled", False) and not _should_skip("gaps"):
        from analysis.semantic_graph import load_graph

        nsg = load_graph(str(project.id))
        if nsg:
            logger.info("Loaded semantic graph from disk for resumed pipeline")

    # ------------------------------------------------------------------
    # Phase 4: Topic clustering
    # ------------------------------------------------------------------
    if _should_skip("clustering"):
        logger.info("Phase 4 skipped (resume): clustering")
        if collector:
            existing = TopicCluster.objects.filter(analysis_job=job).count()
            collector.start_phase("clustering", "Clustering thématique", sort_order=3, items_in=0)
            collector.end_phase(items_out=existing, status="skipped")
    else:
        if _is_resume_target("clustering"):
            _cleanup_phase(job, "clustering")
        _update_phase(job, AnalysisJob.Phase.CLUSTERING, UNIFIED_PROGRESS["clustering"])
        if collector:
            collector.start_phase("clustering", "Clustering thématique", sort_order=3, items_in=0)
        try:
            clustering_cb = _make_progress_cb(job.pk, "Résumés des clusters")
            cluster_engine = TopicClusterEngine(
                tenant, job, project, on_progress=clustering_cb, config=effective.get("clustering")
            )
            clusters = cluster_engine.run()
            logger.info("Phase 4 complete: %d clusters created", len(clusters))
            if collector:
                collector.end_phase(items_out=len(clusters))
        except Exception as exc:
            if collector:
                collector.end_phase(status="failed", error_message=str(exc)[:500])
            raise

    # ------------------------------------------------------------------
    # Phase 5: Gap detection
    # ------------------------------------------------------------------
    if _should_skip("gaps"):
        logger.info("Phase 5 skipped (resume): gaps")
        if collector:
            from analysis.models import GapReport

            existing = GapReport.objects.filter(analysis_job=job).count()
            collector.start_phase("gaps", "Détection des lacunes", sort_order=4, items_in=0)
            collector.end_phase(items_out=existing, status="skipped")
    else:
        if _is_resume_target("gaps"):
            _cleanup_phase(job, "gaps")
        _update_phase(job, AnalysisJob.Phase.GAPS, UNIFIED_PROGRESS["gaps"])
        cluster_count = TopicCluster.objects.filter(analysis_job=job).count()
        if collector:
            collector.start_phase(
                "gaps", "Détection des lacunes", sort_order=4, items_in=cluster_count
            )
        try:
            gaps_cb = _make_progress_cb(job.pk, "Détection des lacunes")
            gap_detector = GapDetector(
                tenant,
                job,
                project,
                nsg=nsg,
                on_progress=gaps_cb,
                config=effective.get("gap_detection"),
            )
            gaps = gap_detector.run()
            logger.info("Phase 5 complete: %d gaps detected", len(gaps))
            if collector:
                collector.end_phase(items_out=len(gaps))
        except Exception as exc:
            if collector:
                collector.end_phase(status="failed", error_message=str(exc)[:500])
            raise

    # ------------------------------------------------------------------
    # Phase 6: Tree
    # ------------------------------------------------------------------
    if _should_skip("tree"):
        logger.info("Phase 6 skipped (resume): tree")
        if collector:
            collector.start_phase("tree", "Index arborescent", sort_order=5, items_in=0)
            collector.end_phase(items_out=0, status="skipped")
    else:
        if _is_resume_target("tree"):
            _cleanup_phase(job, "tree")
        _update_phase(job, AnalysisJob.Phase.TREE, UNIFIED_PROGRESS["tree"])
        cluster_count = TopicCluster.objects.filter(analysis_job=job).count()
        if collector:
            collector.start_phase("tree", "Index arborescent", sort_order=5, items_in=cluster_count)
            collector.end_phase(items_out=0)

    # ------------------------------------------------------------------
    # Phase 7: Contradiction detection
    # ------------------------------------------------------------------
    if _should_skip("contradictions"):
        logger.info("Phase 7 skipped (resume): contradictions")
        if collector:
            from analysis.models import ContradictionPair

            existing = ContradictionPair.objects.filter(analysis_job=job).count()
            collector.start_phase(
                "contradictions", "Détection des contradictions", sort_order=6, items_in=0
            )
            collector.end_phase(items_out=existing, status="skipped")
    else:
        if _is_resume_target("contradictions"):
            _cleanup_phase(job, "contradictions")
        _update_phase(job, AnalysisJob.Phase.CONTRADICTIONS, UNIFIED_PROGRESS["contradictions"])
        claim_count = Claim.objects.filter(project=project).count()
        if collector:
            collector.start_phase(
                "contradictions", "Détection des contradictions", sort_order=6, items_in=claim_count
            )
        try:
            contra_cb = _make_progress_cb(job.pk, "Classification des paires")
            contra_detector = ContradictionDetector(
                tenant, job, project, on_progress=contra_cb, config=effective.get("contradiction")
            )
            contradictions = contra_detector.run()
            logger.info("Phase 7 complete: %d contradictions found", len(contradictions))
            if collector:
                collector.end_phase(items_out=len(contradictions))
        except Exception as exc:
            if collector:
                collector.end_phase(status="failed", error_message=str(exc)[:500])
            raise

    # ------------------------------------------------------------------
    # Phase 8: Hallucination risk detection
    # ------------------------------------------------------------------
    if _should_skip("hallucination"):
        logger.info("Phase 8 skipped (resume): hallucination")
        if collector:
            from analysis.models import HallucinationReport

            existing = HallucinationReport.objects.filter(analysis_job=job).count()
            collector.start_phase(
                "hallucination", "Détection des risques d'hallucination", sort_order=7, items_in=0
            )
            collector.end_phase(items_out=existing, status="skipped")
    else:
        if _is_resume_target("hallucination"):
            _cleanup_phase(job, "hallucination")
        _update_phase(job, AnalysisJob.Phase.HALLUCINATION, UNIFIED_PROGRESS["hallucination"])
        if collector:
            collector.start_phase(
                "hallucination",
                "Détection des risques d'hallucination",
                sort_order=7,
                items_in=doc_count,
            )
        try:
            from analysis.hallucination import HallucinationDetector

            hallu_cb = _make_progress_cb(job.pk, "Détection des risques d'hallucination")
            hallu_detector = HallucinationDetector(
                tenant,
                job,
                project,
                on_progress=hallu_cb,
                config=effective.get("hallucination"),
            )
            hallu_reports = hallu_detector.run()
            logger.info("Phase 8 complete: %d hallucination risks detected", len(hallu_reports))
            if collector:
                collector.end_phase(items_out=len(hallu_reports))
        except Exception as exc:
            if collector:
                collector.end_phase(status="failed", error_message=str(exc)[:500])
            raise

    return _collect_existing_stats(job)


def run_audit_phases(job, collector=None, resume_from=None):
    """Run the 6 audit axes concurrently, creating a linked AuditJob.

    Audit axes are CPU-bound (no LLM/vector calls) and independent of each
    other, so they run in parallel via ThreadPoolExecutor.  Updates progress
    60-93%.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from importlib import import_module

    from django.conf import settings

    from analysis.audit.runner import AXIS_ORDER
    from analysis.models import AnalysisJob, AuditAxisResult, AuditJob

    if resume_from:
        audit_job = AuditJob.objects.filter(analysis_job=job).order_by("-created_at").first()
    else:
        audit_job = None

    if audit_job is None:
        audit_job = AuditJob.objects.create(
            tenant=job.tenant,
            project=job.project,
            analysis_job=job,
            status=AuditJob.Status.RUNNING,
            started_at=timezone.now(),
        )
    else:
        audit_job.status = AuditJob.Status.RUNNING
        audit_job.save(update_fields=["status"])

    completed_axes = set()
    if resume_from:
        completed_axes = set(
            AuditAxisResult.objects.filter(audit_job=audit_job).values_list("axis", flat=True)
        )

    resume_axis_key = None
    if resume_from:
        resume_axis_key = resume_from.removeprefix("audit_")

    project = job.project
    audit_cfg = settings.APP_CONFIG.get("audit", {})
    weights = audit_cfg.get("axis_weights", {})

    weighted_sum = 0.0
    total_weight = 0.0

    axes_to_run: list[tuple[int, str, str, str]] = []
    for idx, (axis_key, module_path, class_name) in enumerate(AXIS_ORDER):
        if axis_key in completed_axes and axis_key != resume_axis_key:
            existing_result = AuditAxisResult.objects.filter(
                audit_job=audit_job,
                axis=axis_key,
            ).first()
            score = existing_result.score if existing_result else 0
            w = weights.get(axis_key, 1.0 / 6)
            weighted_sum += score * w
            total_weight += w

            logger.info("Audit axis %s skipped (resume): score=%.1f", axis_key, score)
            if collector:
                collector.start_phase(
                    f"audit_{axis_key}",
                    AUDIT_AXIS_LABELS.get(axis_key, axis_key),
                    sort_order=7 + idx,
                )
                collector.end_phase(items_out=1, status="skipped")
        else:
            if axis_key == resume_axis_key:
                AuditAxisResult.objects.filter(audit_job=audit_job, axis=axis_key).delete()
            axes_to_run.append((idx, axis_key, module_path, class_name))

    if not axes_to_run:
        overall = weighted_sum / total_weight if total_weight > 0 else 0
        audit_job.overall_score = round(overall, 1)
        audit_job.overall_grade = _audit_grade(overall)
        audit_job.current_axis = AuditJob.Axis.DONE
        audit_job.progress_pct = 100
        audit_job.status = AuditJob.Status.COMPLETED
        audit_job.completed_at = timezone.now()
        audit_job.save()
        return audit_job

    _update_phase(job, AnalysisJob.Phase.AUDIT_HYGIENE, UNIFIED_PROGRESS["audit_hygiene"])

    def _run_single_axis(idx, axis_key, module_path, class_name):
        import django

        django.db.connections.close_all()
        mod = import_module(module_path)
        axis_cls = getattr(mod, class_name)
        axis = axis_cls(project, audit_job, config=audit_cfg.get(axis_key, {}))
        return axis.execute()

    axis_results: dict[str, tuple] = {}
    first_error = None

    with ThreadPoolExecutor(max_workers=len(axes_to_run)) as pool:
        future_to_axis = {
            pool.submit(_run_single_axis, idx, axis_key, module_path, class_name): (idx, axis_key)
            for idx, axis_key, module_path, class_name in axes_to_run
        }

        for future in as_completed(future_to_axis):
            idx, axis_key = future_to_axis[future]
            try:
                result = future.result()
                axis_results[axis_key] = result
                score, _metrics, _chart, _details, duration = result
                logger.info(
                    "Audit axis %s complete: score=%.1f duration=%.1fs", axis_key, score, duration
                )
            except Exception as exc:
                logger.exception("Audit axis %s failed: %s", axis_key, exc)
                axis_results[axis_key] = exc
                if first_error is None:
                    first_error = exc

    for idx, axis_key, module_path, class_name in axes_to_run:
        result = axis_results.get(axis_key)

        if collector:
            collector.start_phase(
                f"audit_{axis_key}",
                AUDIT_AXIS_LABELS.get(axis_key, axis_key),
                sort_order=7 + idx,
            )

        if isinstance(result, Exception):
            if collector:
                collector.end_phase(status="failed", error_message=str(result)[:500])
            continue

        if result is None:
            if collector:
                collector.end_phase(status="failed", error_message="No result returned")
            continue

        score, metrics, chart_data, details, duration = result

        AuditAxisResult.objects.update_or_create(
            audit_job=audit_job,
            axis=axis_key,
            defaults={
                "tenant": job.tenant,
                "project": project,
                "score": score,
                "metrics": metrics,
                "chart_data": chart_data,
                "details": details,
                "duration_seconds": duration,
            },
        )

        if collector:
            collector.end_phase(items_out=1)

        w = weights.get(axis_key, 1.0 / 6)
        weighted_sum += score * w
        total_weight += w

    if first_error is not None:
        raise first_error

    _update_phase(job, AnalysisJob.Phase.AUDIT_GOVERNANCE, UNIFIED_PROGRESS["audit_governance"])

    overall = weighted_sum / total_weight if total_weight > 0 else 0
    audit_job.overall_score = round(overall, 1)
    audit_job.overall_grade = _audit_grade(overall)
    audit_job.current_axis = AuditJob.Axis.DONE
    audit_job.progress_pct = 100
    audit_job.status = AuditJob.Status.COMPLETED
    audit_job.completed_at = timezone.now()
    audit_job.save()

    return audit_job
