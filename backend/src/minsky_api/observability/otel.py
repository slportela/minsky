"""OpenTelemetry setup. Empty OTEL_EXPORTER_OTLP_ENDPOINT means no export (ADR 0007)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Tracer

_PROVIDER: TracerProvider | None = None
_TRACER_NAME = "minsky"


def configure_tracing(*, endpoint: str | None = None, exporter: SpanExporter | None = None) -> bool:
    """Wire a TracerProvider when an OTLP endpoint or a test exporter is given.

    Returns True when export is active. Empty endpoint and no exporter → leave the default
    no-op provider (returns False).
    """
    global _PROVIDER
    if _PROVIDER is not None:
        return True

    resolved = (endpoint if endpoint is not None else os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")).strip()
    if exporter is None and not resolved:
        return False

    resource = Resource.create({"service.name": "minsky-api"})
    provider = TracerProvider(resource=resource)
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        base = resolved.rstrip("/")
        traces_url = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url)))
    trace.set_tracer_provider(provider)
    _PROVIDER = provider
    return True


def reset_tracing_for_tests() -> None:
    """Shutdown any provider so tests can configure again."""
    global _PROVIDER
    if _PROVIDER is not None:
        _PROVIDER.shutdown()
        _PROVIDER = None
    # The API allows set_tracer_provider only once per process; tests need a second chance.
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001


def get_tracer() -> Tracer:
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def start_span(name: str, **attributes: object) -> Iterator[Span]:
    """Start a span. Attributes must not carry customer free text or secrets."""
    with get_tracer().start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is None:
                continue
            if isinstance(value, (bool, int, float, str)):
                span.set_attribute(key, value)
            else:
                span.set_attribute(key, str(value))
        yield span
