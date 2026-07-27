"""
Observability service for structured logging, metrics, and tracing.
"""

import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis
import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

# Optional OpenTelemetry imports (not required)
try:
    from opentelemetry import trace
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

from app.core.config import DEBUG, REDIS_URL

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# Prometheus metrics
REGISTRY = CollectorRegistry()

# Request metrics
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint", "status_code"], registry=REGISTRY)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "endpoint"], registry=REGISTRY
)

# Business metrics
APPOINTMENT_COUNT = Counter(
    "appointments_total", "Total appointments created", ["tenant_id", "service_type", "status"], registry=REGISTRY
)

CALL_COUNT = Counter("calls_total", "Total calls received", ["tenant_id", "status"], registry=REGISTRY)

PROVISIONING_DURATION = Histogram(
    "provisioning_duration_seconds", "Provisioning job duration in seconds", ["job_type", "status"], registry=REGISTRY
)

# System metrics
ACTIVE_TENANTS = Gauge("active_tenants_total", "Number of active tenants", registry=REGISTRY)

ACTIVE_SESSIONS = Gauge("active_sessions_total", "Number of active user sessions", registry=REGISTRY)

REDIS_CONNECTIONS = Gauge("redis_connections_active", "Number of active Redis connections", registry=REGISTRY)


@dataclass
class AuditLog:
    """Audit log entry."""

    id: str
    user_id: Optional[str]
    tenant_id: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    details: Dict[str, Any]
    ip_address: Optional[str]
    user_agent: Optional[str]
    timestamp: datetime


class ObservabilityService:
    """Service class for observability operations."""

    def __init__(self):
        self.logger = structlog.get_logger()
        self.tracer = trace.get_tracer(__name__) if OPENTELEMETRY_AVAILABLE else None
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)

        # Initialize OpenTelemetry if not in debug mode and available
        if not DEBUG and OPENTELEMETRY_AVAILABLE:
            self._setup_tracing()

    def _setup_tracing(self):
        """Setup OpenTelemetry tracing."""
        try:
            # Create tracer provider
            trace.set_tracer_provider(TracerProvider())
            tracer_provider = trace.get_tracer_provider()

            # Create Jaeger exporter
            jaeger_exporter = JaegerExporter(
                agent_host_name="localhost",
                agent_port=14268,
            )

            # Create span processor
            span_processor = BatchSpanProcessor(jaeger_exporter)
            tracer_provider.add_span_processor(span_processor)

            # Instrument libraries
            FastAPIInstrumentor().instrument()
            SQLAlchemyInstrumentor().instrument()
            RedisInstrumentor().instrument()

        except Exception as e:
            self.logger.warning("Failed to setup tracing", error=str(e))

    def log_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration: float,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Log HTTP request."""
        self.logger.info(
            "HTTP request",
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            duration=duration,
            user_id=user_id,
            tenant_id=tenant_id,
            request_id=request_id,
        )

        # Update metrics
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=str(status_code)).inc()

        REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

    def log_appointment_created(
        self, appointment_id: str, tenant_id: str, service_type: str, status: str, user_id: Optional[str] = None
    ):
        """Log appointment creation."""
        self.logger.info(
            "Appointment created",
            appointment_id=appointment_id,
            tenant_id=tenant_id,
            service_type=service_type,
            status=status,
            user_id=user_id,
        )

        # Update metrics
        APPOINTMENT_COUNT.labels(tenant_id=tenant_id, service_type=service_type, status=status).inc()

    def log_call_received(self, call_id: str, tenant_id: str, status: str, caller_number: Optional[str] = None):
        """Log call received."""
        self.logger.info("Call received", call_id=call_id, tenant_id=tenant_id, status=status, caller_number=caller_number)

        # Update metrics
        CALL_COUNT.labels(tenant_id=tenant_id, status=status).inc()

    def log_provisioning_job(self, job_id: str, job_type: str, status: str, duration: float, tenant_id: Optional[str] = None):
        """Log provisioning job."""
        self.logger.info(
            "Provisioning job", job_id=job_id, job_type=job_type, status=status, duration=duration, tenant_id=tenant_id
        )

        # Update metrics
        PROVISIONING_DURATION.labels(job_type=job_type, status=status).observe(duration)

    def log_error(
        self, error: Exception, context: Dict[str, Any], user_id: Optional[str] = None, tenant_id: Optional[str] = None
    ):
        """Log error with context."""
        self.logger.error(
            "Error occurred",
            error=str(error),
            error_type=type(error).__name__,
            context=context,
            user_id=user_id,
            tenant_id=tenant_id,
            exc_info=True,
        )

    def log_audit_event(
        self,
        user_id: Optional[str],
        tenant_id: Optional[str],
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        details: Dict[str, Any],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        """Log audit event."""
        audit_log = AuditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            timestamp=datetime.now(timezone.utc),
        )

        self.logger.info("Audit event", **asdict(audit_log))

    @contextmanager
    def trace_operation(self, operation_name: str, **attributes):
        """Trace an operation."""
        if self.tracer and OPENTELEMETRY_AVAILABLE:
            with self.tracer.start_as_current_span(operation_name) as span:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
                yield span
        else:
            # No-op context manager when tracing is not available
            yield None

    def update_system_metrics(self, active_tenants: int, active_sessions: int, redis_connections: int):
        """Update system metrics."""
        ACTIVE_TENANTS.set(active_tenants)
        ACTIVE_SESSIONS.set(active_sessions)
        REDIS_CONNECTIONS.set(redis_connections)

    def get_metrics(self) -> str:
        """Get Prometheus metrics."""
        return generate_latest(REGISTRY).decode("utf-8")

    def log_performance(self, operation: str, duration: float, success: bool, **context):
        """Log performance metrics."""
        self.logger.info("Performance metric", operation=operation, duration=duration, success=success, **context)


# Global observability instance
observability = ObservabilityService()


# Middleware for request logging
class RequestLoggingMiddleware:
    """Middleware for logging HTTP requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        start_time = time.time()

        # Add request ID to scope
        scope["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                duration = time.time() - start_time
                status_code = message["status"]
                method = scope["method"]
                path = scope["path"]

                # Extract user info from headers if available
                headers = dict(scope["headers"])
                user_id = headers.get(b"x-user-id", b"").decode()
                tenant_id = headers.get(b"x-tenant-id", b"").decode()

                observability.log_request(
                    method=method,
                    endpoint=path,
                    status_code=status_code,
                    duration=duration,
                    user_id=user_id if user_id else None,
                    tenant_id=tenant_id if tenant_id else None,
                    request_id=request_id,
                )

            await send(message)

        await self.app(scope, receive, send_wrapper)


# Utility functions
def log_request(method: str, endpoint: str, status_code: int, duration: float, **kwargs):
    """Log HTTP request."""
    observability.log_request(method, endpoint, status_code, duration, **kwargs)


def log_appointment_created(appointment_id: str, tenant_id: str, service_type: str, status: str, **kwargs):
    """Log appointment creation."""
    observability.log_appointment_created(appointment_id, tenant_id, service_type, status, **kwargs)


def log_call_received(call_id: str, tenant_id: str, status: str, **kwargs):
    """Log call received."""
    observability.log_call_received(call_id, tenant_id, status, **kwargs)


def log_provisioning_job(job_id: str, job_type: str, status: str, duration: float, **kwargs):
    """Log provisioning job."""
    observability.log_provisioning_job(job_id, job_type, status, duration, **kwargs)


def log_error(error: Exception, context: Dict[str, Any], **kwargs):
    """Log error."""
    observability.log_error(error, context, **kwargs)


def log_audit_event(user_id: Optional[str], tenant_id: Optional[str], action: str, resource_type: str, **kwargs):
    """Log audit event."""
    observability.log_audit_event(user_id, tenant_id, action, resource_type, **kwargs)


def trace_operation(operation_name: str, **attributes):
    """Trace an operation."""
    return observability.trace_operation(operation_name, **attributes)


def update_system_metrics(active_tenants: int, active_sessions: int, redis_connections: int):
    """Update system metrics."""
    observability.update_system_metrics(active_tenants, active_sessions, redis_connections)


def get_metrics() -> str:
    """Get Prometheus metrics."""
    return observability.get_metrics()
