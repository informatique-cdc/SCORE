"""
Thread-safe pipeline trace collector.

Buffers TraceEvent objects in memory and flushes them to the database
at phase boundaries to minimize DB writes during the hot path.
"""

import logging
import threading
import time

from django.utils import timezone

logger = logging.getLogger(__name__)


class PhaseEventBuffer:
    """Lightweight event buffer for parallel phase execution.

    Implements the same ``record_event()`` interface as TraceCollector so it
    can be set as ``_trace_local.trace`` on LLMClient / VectorStore threads.
    After the parallel work finishes, the caller replays the buffered events
    into the real TraceCollector via ``replay_into()``.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.events: list[dict] = []

    def record_event(
        self,
        event_type,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        item_count=0,
        result_count=0,
        duration=0.0,
        model_name="",
    ):
        with self._lock:
            self.events.append(
                {
                    "event_type": event_type,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens or (prompt_tokens + completion_tokens),
                    "item_count": item_count,
                    "result_count": result_count,
                    "duration": duration,
                    "model_name": model_name,
                }
            )

    def replay_into(self, collector: "TraceCollector"):
        """Replay all buffered events into a TraceCollector's current phase."""
        for ev in self.events:
            collector.record_event(**ev)


class TraceCollector:
    """Collects trace events during a pipeline run and persists them."""

    def __init__(self, pipeline_trace):
        self._pipeline_trace = pipeline_trace
        self._lock = threading.Lock()
        self._current_phase = None
        self._phase_start = None
        self._event_buffer = []

    def start_phase(self, phase_key, phase_label, sort_order, items_in=0):
        """Flush the previous phase (if any) and start a new one."""
        with self._lock:
            self._flush_phase_unlocked()

        from analysis.models import PhaseTrace

        phase = PhaseTrace.objects.create(
            tenant=self._pipeline_trace.tenant,
            project=self._pipeline_trace.project,
            pipeline_trace=self._pipeline_trace,
            phase_key=phase_key,
            phase_label=phase_label,
            sort_order=sort_order,
            items_in=items_in,
            started_at=timezone.now(),
        )
        with self._lock:
            self._current_phase = phase
            self._phase_start = time.monotonic()
            self._event_buffer = []

    def record_event(
        self,
        event_type,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        item_count=0,
        result_count=0,
        duration=0.0,
        model_name="",
    ):
        """Append a trace event to the in-memory buffer (thread-safe)."""
        from analysis.models import TraceEvent

        event = TraceEvent(
            event_type=event_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
            item_count=item_count,
            result_count=result_count,
            duration_seconds=round(duration, 4),
            model_name=model_name[:100],
        )
        with self._lock:
            self._event_buffer.append(event)

    def end_phase(self, items_out=0, status="completed", error_message=""):
        """Flush buffered events, aggregate phase stats, and save the PhaseTrace."""
        with self._lock:
            self._end_phase_unlocked(items_out, status, error_message)

    def _end_phase_unlocked(self, items_out=0, status="completed", error_message=""):
        phase = self._current_phase
        if phase is None:
            return

        # Bulk-create buffered events
        if self._event_buffer:
            for ev in self._event_buffer:
                ev.phase_trace = phase
            from analysis.models import TraceEvent

            TraceEvent.objects.bulk_create(self._event_buffer)

        # Aggregate stats from buffer
        llm_calls = 0
        embed_calls = 0
        search_calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        for ev in self._event_buffer:
            if ev.event_type.startswith("llm_chat"):
                llm_calls += 1
            elif ev.event_type == "llm_embed":
                embed_calls += 1
            elif ev.event_type.startswith("vec_"):
                search_calls += 1
            prompt_tokens += ev.prompt_tokens
            completion_tokens += ev.completion_tokens
            total_tokens += ev.total_tokens

        duration = time.monotonic() - self._phase_start if self._phase_start else 0.0

        phase.llm_calls = llm_calls
        phase.embed_calls = embed_calls
        phase.search_calls = search_calls
        phase.prompt_tokens = prompt_tokens
        phase.completion_tokens = completion_tokens
        phase.total_tokens = total_tokens
        phase.items_out = items_out
        phase.duration_seconds = round(duration, 3)
        phase.completed_at = timezone.now()
        phase.status = status
        phase.error_message = error_message[:2000]
        phase.save()

        self._current_phase = None
        self._phase_start = None
        self._event_buffer = []

    def _flush_phase_unlocked(self):
        """Flush remaining phase if still open (called with lock held)."""
        if self._current_phase is not None:
            self._end_phase_unlocked(status="completed")

    def finalize(self):
        """Flush any remaining phase and compute pipeline-level totals."""
        with self._lock:
            self._flush_phase_unlocked()

        from django.db.models import Sum

        from analysis.models import PhaseTrace

        agg = PhaseTrace.objects.filter(pipeline_trace=self._pipeline_trace).aggregate(
            total_llm=Sum("llm_calls"),
            total_embed=Sum("embed_calls"),
            total_search=Sum("search_calls"),
            total_prompt=Sum("prompt_tokens"),
            total_completion=Sum("completion_tokens"),
            total_tok=Sum("total_tokens"),
            total_dur=Sum("duration_seconds"),
        )

        pt = self._pipeline_trace
        pt.total_llm_calls = agg["total_llm"] or 0
        pt.total_embed_calls = agg["total_embed"] or 0
        pt.total_search_calls = agg["total_search"] or 0
        pt.total_prompt_tokens = agg["total_prompt"] or 0
        pt.total_completion_tokens = agg["total_completion"] or 0
        pt.total_tokens = agg["total_tok"] or 0
        pt.total_duration_seconds = round(agg["total_dur"] or 0, 3)
        pt.completed_at = timezone.now()
        try:
            pt.save()
        except Exception:
            logger.warning(
                "Could not save pipeline trace %s (may have been replaced by a retry)", pt.pk
            )
