"""Ponto de entrada do Execution Service."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.infrastructure.config import settings
from app.infrastructure.logging_config import setup_logging
from app.infrastructure.messaging.runtime import MessagingRuntime
from app.infrastructure.mongo.database import create_client, ensure_indexes, unit_of_work_factory
from app.infrastructure.observability.metrics import set_app_info
from app.infrastructure.observability.tracing import setup_tracing, shutdown_tracing
from app.interfaces.http.controllers import execution_controller, health, part_controller
from app.interfaces.http.exception_handlers import register_exception_handlers
from app.interfaces.http.middleware import (
    MetricsMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)

API_V1 = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Conecta no MongoDB e, quando o RabbitMQ está configurado, sobe a mensageria."""
    client = create_client(settings.MONGODB_URL)
    db = client[settings.MONGODB_DATABASE]
    await ensure_indexes(db)
    app.state.uow_factory = unit_of_work_factory(client, db)

    async def database_is_up() -> bool:
        await client.admin.command("ping")
        return True

    app.state.readiness_checks["database"] = database_is_up
    runtime = None
    if settings.RABBITMQ_URL:
        runtime = MessagingRuntime(app.state.uow_factory, db)
        await runtime.start()
        app.state.readiness_checks["broker"] = runtime.is_up
    yield
    if runtime is not None:
        await runtime.stop()
    await client.close()
    shutdown_tracing()


def create_app() -> FastAPI:
    setup_logging(settings.LOG_LEVEL, service=settings.SERVICE_NAME)

    app = FastAPI(
        title="AutoGiro · Execution Service",
        description="Produção: estoque de peças, fila de execução, diagnóstico e reparo.",
        version=settings.SERVICE_VERSION,
        lifespan=lifespan,
    )
    # Verificações de prontidão (banco, broker), registradas por cada adapter.
    app.state.readiness_checks = {}

    set_app_info(name=settings.SERVICE_NAME, version=settings.SERVICE_VERSION)
    # Antes de o cliente do MongoDB ser criado (no lifespan): a instrumentação do
    # pymongo vale para os clientes criados depois dela.
    setup_tracing(
        app,
        service_name=settings.SERVICE_NAME,
        service_version=settings.SERVICE_VERSION,
        environment=settings.DEPLOYMENT_ENVIRONMENT,
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        license_key=settings.NEW_RELIC_LICENSE_KEY,
        instrument_pymongo=True,
    )

    # Middlewares (o último adicionado é o mais externo).
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=settings.cors_origins_list != ["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    for controller in (part_controller, execution_controller):
        app.include_router(controller.router, prefix=API_V1)
    return app


app = create_app()
