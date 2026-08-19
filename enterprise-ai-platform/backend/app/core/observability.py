"""
Observability — OpenTelemetry setup for distributed tracing and metrics.
Call configure_telemetry() at application startup.
"""
from __future__ import annotations

from structlog import get_logger

logger = get_logger(__name__)


def configure_telemetry(service_name: str = "eatap-backend") -> None:
    """
    Configure OpenTelemetry with Azure Monitor exporter.
    Falls back to no-op if the SDK is not installed or connection string is missing.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # Try Azure Monitor exporter
        try:
            from app.core.config import settings
            conn_str = getattr(settings, "APPLICATIONINSIGHTS_CONNECTION_STRING", None)
            if conn_str:
                from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
                exporter = AzureMonitorTraceExporter(connection_string=conn_str)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info("azure_monitor_tracer_configured")
        except ImportError:
            # Fall back to console exporter for development
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("console_tracer_configured_fallback")

        trace.set_tracer_provider(provider)
        logger.info("opentelemetry_configured", service=service_name)

    except ImportError:
        logger.warning("opentelemetry_unavailable", reason="opentelemetry-sdk not installed")
    except Exception as e:
        logger.warning("opentelemetry_setup_failed", error=str(e))


def configure_logging() -> None:
    """Configure structlog for structured JSON logging."""
    import structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer() if _is_dev() else structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


def _is_dev() -> bool:
    try:
        from app.core.config import settings
        return settings.ENVIRONMENT == "development"
    except Exception:
        return True
