"""Tests for analysis.trace — TraceCollector & PhaseEventBuffer."""

import threading

import pytest

from analysis.models import PhaseTrace, PipelineTrace, TraceEvent
from analysis.trace import PhaseEventBuffer, TraceCollector


# ---------------------------------------------------------------------------
# PhaseEventBuffer (pure in-memory, no DB)
# ---------------------------------------------------------------------------


class TestPhaseEventBuffer:
    def test_record_event_appends(self):
        buf = PhaseEventBuffer()
        buf.record_event("llm_chat", prompt_tokens=10, completion_tokens=5)
        buf.record_event("llm_embed", item_count=3)
        assert len(buf.events) == 2
        assert buf.events[0]["event_type"] == "llm_chat"
        assert buf.events[1]["item_count"] == 3

    def test_total_tokens_auto_calculated(self):
        buf = PhaseEventBuffer()
        buf.record_event("llm_chat", prompt_tokens=10, completion_tokens=5)
        assert buf.events[0]["total_tokens"] == 15

    def test_total_tokens_explicit(self):
        buf = PhaseEventBuffer()
        buf.record_event("llm_chat", prompt_tokens=10, completion_tokens=5, total_tokens=20)
        assert buf.events[0]["total_tokens"] == 20

    def test_thread_safety(self):
        buf = PhaseEventBuffer()
        errors = []

        def _record(idx):
            try:
                buf.record_event("llm_chat", prompt_tokens=idx)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(buf.events) == 50

    def test_replay_into(self):
        buf = PhaseEventBuffer()
        buf.record_event("llm_chat", prompt_tokens=10, completion_tokens=5)
        buf.record_event("llm_embed", item_count=3)

        # Use a mock collector to verify replay
        received = []

        class FakeCollector:
            def record_event(self, **kwargs):
                received.append(kwargs)

        buf.replay_into(FakeCollector())
        assert len(received) == 2
        assert received[0]["event_type"] == "llm_chat"
        assert received[1]["event_type"] == "llm_embed"


# ---------------------------------------------------------------------------
# TraceCollector (DB-backed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTraceCollector:
    def _make_collector(self, analysis_job):
        pipeline_trace = PipelineTrace.objects.create(
            tenant=analysis_job.tenant,
            project=analysis_job.project,
            analysis_job=analysis_job,
        )
        return TraceCollector(pipeline_trace), pipeline_trace

    def test_start_and_end_phase(self, analysis_job):
        collector, _ = self._make_collector(analysis_job)
        collector.start_phase("duplicates", "Duplicates", sort_order=0, items_in=10)
        collector.end_phase(items_out=3)

        phase = PhaseTrace.objects.get(phase_key="duplicates")
        assert phase.status == "completed"
        assert phase.items_in == 10
        assert phase.items_out == 3
        assert phase.duration_seconds >= 0

    def test_record_event_creates_trace_events(self, analysis_job):
        collector, _ = self._make_collector(analysis_job)
        collector.start_phase("claims", "Claims", sort_order=1)
        collector.record_event("llm_chat", prompt_tokens=100, completion_tokens=50)
        collector.record_event("llm_embed", item_count=5)
        collector.end_phase(items_out=2)

        events = TraceEvent.objects.filter(phase_trace__phase_key="claims")
        assert events.count() == 2
        assert events.filter(event_type="llm_chat").exists()
        assert events.filter(event_type="llm_embed").exists()

    def test_event_type_counting(self, analysis_job):
        collector, _ = self._make_collector(analysis_job)
        collector.start_phase("test", "Test", sort_order=0)
        collector.record_event("llm_chat", prompt_tokens=10, completion_tokens=5)
        collector.record_event("llm_chat_concurrent", prompt_tokens=20, completion_tokens=10)
        collector.record_event("llm_embed", item_count=3)
        collector.record_event("vec_search", result_count=5)
        collector.record_event("vec_upsert", item_count=10)
        collector.end_phase()

        phase = PhaseTrace.objects.get(phase_key="test")
        assert phase.llm_calls == 2  # llm_chat + llm_chat_concurrent
        assert phase.embed_calls == 1
        assert phase.search_calls == 2  # vec_search + vec_upsert
        assert phase.prompt_tokens == 30
        assert phase.completion_tokens == 15

    def test_end_phase_with_failure(self, analysis_job):
        collector, _ = self._make_collector(analysis_job)
        collector.start_phase("failing", "Failing", sort_order=0)
        collector.end_phase(status="failed", error_message="Something broke")

        phase = PhaseTrace.objects.get(phase_key="failing")
        assert phase.status == "failed"
        assert phase.error_message == "Something broke"

    def test_finalize_aggregates_pipeline_totals(self, analysis_job):
        collector, pt = self._make_collector(analysis_job)

        collector.start_phase("phase1", "Phase 1", sort_order=0)
        collector.record_event("llm_chat", prompt_tokens=100, completion_tokens=50)
        collector.end_phase()

        collector.start_phase("phase2", "Phase 2", sort_order=1)
        collector.record_event("llm_embed", item_count=5, total_tokens=200)
        collector.record_event("vec_search", result_count=3)
        collector.end_phase()

        collector.finalize()

        pt.refresh_from_db()
        assert pt.total_llm_calls == 1
        assert pt.total_embed_calls == 1
        assert pt.total_search_calls == 1
        assert pt.total_prompt_tokens == 100
        assert pt.total_completion_tokens == 50
        assert pt.total_tokens == 150 + 200  # phase1 (150) + phase2 (200)
        assert pt.completed_at is not None

    def test_start_new_phase_flushes_previous(self, analysis_job):
        collector, _ = self._make_collector(analysis_job)
        collector.start_phase("phase1", "Phase 1", sort_order=0)
        collector.record_event("llm_chat", prompt_tokens=10)
        # Opening phase2 should auto-close phase1
        collector.start_phase("phase2", "Phase 2", sort_order=1)
        collector.end_phase()

        phase1 = PhaseTrace.objects.get(phase_key="phase1")
        assert phase1.status == "completed"
        assert phase1.llm_calls == 1

    def test_finalize_without_phases(self, analysis_job):
        collector, pt = self._make_collector(analysis_job)
        collector.finalize()

        pt.refresh_from_db()
        assert pt.total_llm_calls == 0
        assert pt.total_tokens == 0
        assert pt.total_duration_seconds == 0.0
        assert pt.completed_at is not None
