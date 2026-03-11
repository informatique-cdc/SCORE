from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="dashboard-home"),
    path("_stats/", views.stats_partial, name="dashboard-stats-partial"),
    path("_latest-analysis/", views.latest_analysis_partial, name="dashboard-latest-analysis-partial"),
    path("_recent-jobs/", views.recent_jobs_partial, name="dashboard-recent-jobs-partial"),
    path("_score-detail/", views.score_detail_json, name="dashboard-score-detail"),
    path("feedback/", views.submit_feedback, name="dashboard-feedback"),
]
