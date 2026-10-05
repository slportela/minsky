"""OpenTelemetry configure is a no-op without an endpoint; a test exporter records spans."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from minsky_api.observability.otel import configure_tracing, reset_tracing_for_tests, start_span


def setup_function() -> None:
    reset_tracing_for_tests()


def test_configure_without_endpoint_is_a_noop() -> None:
    assert configure_tracing(endpoint="") is False
    with start_span("chat.turn", phase="understand"):
        pass


def test_configure_with_exporter_records_a_span() -> None:
    exporter = InMemorySpanExporter()
    assert configure_tracing(exporter=exporter) is True
    with start_span("chat.turn", conversation_id="c1", phase="confirm_act"):
        with start_span("llm.respond", model="gpt-6-luna"):
            pass
    names = {span.name for span in exporter.get_finished_spans()}
    assert names == {"chat.turn", "llm.respond"}


def test_failed_span_records_error_status() -> None:
    exporter = InMemorySpanExporter()
    assert configure_tracing(exporter=exporter) is True
    with pytest.raises(RuntimeError, match="boom"):
        with start_span("llm.respond", model="gpt-6-luna"):
            raise RuntimeError("boom")
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert spans[0].status.description == "RuntimeError"
