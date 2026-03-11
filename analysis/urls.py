from django.urls import path
from . import views
from . import views_audit

urlpatterns = [
    path("", views.analysis_list, name="analysis-list"),
    path("run/", views.analysis_run, name="analysis-run"),
    path("_jobs/", views.analysis_jobs_partial, name="analysis-jobs-partial"),
    path("<uuid:pk>/", views.analysis_detail, name="analysis-detail"),
    path("<uuid:pk>/retry/", views.analysis_retry, name="analysis-retry"),
    path("<uuid:pk>/cancel/", views.analysis_cancel, name="analysis-cancel"),
    path("<uuid:pk>/delete/", views.analysis_delete, name="analysis-delete"),
    path("<uuid:pk>/duplicates/", views.duplicates_report, name="analysis-duplicates"),
    path("<uuid:pk>/contradictions/", views.contradictions_report, name="analysis-contradictions"),
    path(
        "<uuid:pk>/contradictions/<uuid:contra_pk>/resolve/",
        views.contradiction_resolve,
        name="contradiction-resolve",
    ),
    path(
        "<uuid:pk>/contradictions/batch-resolve/",
        views.contradiction_batch_resolve,
        name="contradiction-batch-resolve",
    ),
    path("<uuid:pk>/clusters/", views.clusters_view, name="analysis-clusters"),
    path("<uuid:pk>/gaps/", views.gaps_report, name="analysis-gaps"),
    path("<uuid:pk>/gaps/<uuid:gap_pk>/resolve/", views.gap_resolve, name="gap-resolve"),
    path("<uuid:pk>/gaps/batch-resolve/", views.gap_batch_resolve, name="gap-batch-resolve"),
    path("<uuid:pk>/hallucinations/", views.hallucination_report, name="analysis-hallucinations"),
    path(
        "<uuid:pk>/hallucinations/<uuid:hallu_pk>/resolve/",
        views.hallucination_resolve,
        name="hallucination-resolve",
    ),
    path(
        "<uuid:pk>/hallucinations/batch-resolve/",
        views.hallucination_batch_resolve,
        name="hallucination-batch-resolve",
    ),
    path("<uuid:pk>/tree/", views.tree_view, name="analysis-tree"),
    path("<uuid:pk>/knowledge-map/", views.knowledge_map_view, name="analysis-knowledge-map"),
    path("<uuid:pk>/trace/", views.trace_view, name="analysis-trace"),
    path("<uuid:pk>/audit/", views.analysis_audit_overview, name="analysis-audit-overview"),
    path("<uuid:pk>/_progress/", views.analysis_progress_partial, name="analysis-progress-partial"),
    path(
        "<uuid:pk>/_progress_full/",
        views.analysis_progress_full_partial,
        name="analysis-progress-full-partial",
    ),
    path("<uuid:pk>/_results/", views.analysis_results_partial, name="analysis-results-partial"),
    # JSON API endpoints for D3.js visualizations
    path("<uuid:pk>/api/clusters/", views.clusters_json, name="api-clusters"),
    path("<uuid:pk>/api/tree/", views.tree_json, name="api-tree"),
    path("<uuid:pk>/api/concept-graph/", views.concept_graph_json, name="api-concept-graph"),
    path(
        "<uuid:pk>/api/concept-graph/query/",
        views.concept_graph_query,
        name="api-concept-graph-query",
    ),
    # Audit RAG routes
    path("audit/", views_audit.audit_list, name="audit-list"),
    path("audit/run/", views_audit.audit_run, name="audit-run"),
    path("audit/<uuid:pk>/", views_audit.audit_detail, name="audit-detail"),
    path("audit/<uuid:pk>/retry/", views_audit.audit_retry, name="audit-retry"),
    path("audit/<uuid:pk>/delete/", views_audit.audit_delete, name="audit-delete"),
    path("audit/<uuid:pk>/hygiene/", views_audit.audit_hygiene, name="audit-hygiene"),
    path("audit/<uuid:pk>/structure/", views_audit.audit_structure, name="audit-structure"),
    path("audit/<uuid:pk>/coverage/", views_audit.audit_coverage, name="audit-coverage"),
    path("audit/<uuid:pk>/coherence/", views_audit.audit_coherence, name="audit-coherence"),
    path(
        "audit/<uuid:pk>/retrievability/",
        views_audit.audit_retrievability,
        name="audit-retrievability",
    ),
    path("audit/<uuid:pk>/governance/", views_audit.audit_governance, name="audit-governance"),
    path(
        "audit/<uuid:pk>/_progress/",
        views_audit.audit_progress_partial,
        name="audit-progress-partial",
    ),
    path("audit/<uuid:pk>/api/<str:axis>/", views_audit.api_audit_axis, name="api-audit-axis"),
]
