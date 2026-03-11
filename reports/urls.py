from django.urls import path
from . import views

urlpatterns = [
    path("", views.report_list, name="report-list"),
    path("<uuid:job_pk>/duplicates.csv", views.export_duplicates_csv, name="export-duplicates-csv"),
    path(
        "<uuid:job_pk>/contradictions.csv",
        views.export_contradictions_csv,
        name="export-contradictions-csv",
    ),
    path("<uuid:job_pk>/gaps.csv", views.export_gaps_csv, name="export-gaps-csv"),
    path(
        "<uuid:job_pk>/hallucinations.csv",
        views.export_hallucinations_csv,
        name="export-hallucinations-csv",
    ),
    path("<uuid:job_pk>/report.json", views.export_report_json, name="export-report-json"),
    path("<uuid:job_pk>/report.pdf", views.export_report_pdf, name="export-report-pdf"),
]
