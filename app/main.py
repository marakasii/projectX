"""Composition root owned by Matvey. One Uvicorn process, same-origin API."""

from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.openapi.utils import get_openapi
from starlette.concurrency import run_in_threadpool

from app.auth.router import router as auth_router
from app.ai import CodexCLIProvider
from app.auth.service import AuthService
from app.cases.router import router as cases_router
from app.cases.service import CaseService
from app.common.api import PACKAGE_NAMES, PENDING_ROUTERS
from app.common.concurrency import ProcessingGuards
from app.common.config import Settings
from app.common.db import SCHEMA_VERSION, Database
from app.common.errors import AppError, ERROR_RESPONSES, install_error_handlers
from app.common.interfaces import AIProvider, AuthGateway
from app.common.models import Health
from app.common.web import install_web
from app.eligibility.service import EligibilityService
from app.documents.router import router as documents_router
from app.documents.service import DocumentArchive
from app.events.router import router as events_router
from app.events.service import EventService
from app.events.projection import EventProjector
from app.profile.router import router as profile_router
from app.profile.service import ProfileService
from app.rules.registry import FileRuleRegistry
from app.rules.router import router as rules_router

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    *,
    feature_routers: Mapping[int, APIRouter] | None = None,
    auth_gateway: AuthGateway | None = None,
    document_extractor=None,
    case_checker=None,
    case_rebuilder=None,
    ai_provider: AIProvider | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    database = Database(config.database_path)
    installed = dict(feature_routers or {})
    if set(installed) - PACKAGE_NAMES.keys():
        raise ValueError("Unknown feature package")
    installed.setdefault(11, auth_router)
    installed.setdefault(2, rules_router)
    installed.setdefault(3, profile_router)
    installed.setdefault(4, cases_router)
    installed.setdefault(5, documents_router)
    installed.setdefault(9, events_router)
    authentication = AuthService(database, config)
    registry = FileRuleRegistry()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        def initialize():
            config.uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            config.temporary_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            database.initialize()

        await run_in_threadpool(initialize)
        yield

    application = FastAPI(
        title="VisaPilot API", version="0.1.0", lifespan=lifespan,
        description=(
            "Локальное демо на вымышленных документах. Работают аккаунты, 12 направлений, профиль/сравнения, дела, архив и ручная хронология (пакеты 1/11/2/3/4/5/9); "
            "остальные маршруты документируют контракт и отвечают 503 после входа до реализации. "
            "Frontend использует один origin и credentials=include."
        ),
        responses=ERROR_RESPONSES,
    )
    application.state.settings = config
    application.state.database = database
    application.state.auth_service = authentication
    application.state.rule_registry = registry
    application.state.profile_service = ProfileService(database)
    application.state.applicability_evaluator = EligibilityService(registry)
    archive = DocumentArchive(database, config, extractor=document_extractor)
    projector = EventProjector()
    cases = CaseService(database, registry, application.state.applicability_evaluator,
                        document_service=archive, checker=case_checker, projector=projector)
    archive.rebuilder = case_rebuilder if case_rebuilder is not None else cases
    application.state.document_archive = archive
    application.state.document_service = archive
    application.state.case_service = cases
    application.state.case_rebuilder = cases
    application.state.process_projector = projector
    application.state.case_checker = case_checker
    application.state.ai_provider = ai_provider if ai_provider is not None else CodexCLIProvider(
        enabled=config.ai_enabled, model=config.ai_model, timeout_seconds=min(config.ai_timeout_seconds, 60),
        temporary_root=config.temporary_dir,
    )
    application.state.event_service = EventService(database, registry, application.state.applicability_evaluator)
    application.state.auth_gateway = auth_gateway if auth_gateway is not None else authentication
    application.state.processing = ProcessingGuards()
    install_error_handlers(application)

    @application.get(API_PREFIX + "/health", response_model=Health, tags=["health"])
    def health(request: Request):
        if not request.app.state.database.is_ready():
            raise AppError(503, "database_unavailable", "База данных не готова.")
        return Health(
            status="ok", demo_mode=config.demo_mode,
            database="ok", schema_version=SCHEMA_VERSION,
        )

    for package, pending_router in PENDING_ROUTERS.items():
        router = installed.get(package, pending_router)
        application.include_router(router, prefix=API_PREFIX)

    def openapi():
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title, version=application.version,
                description=application.description, routes=application.routes,
            )
            security = schema.get("components", {}).get("securitySchemes", {})
            if "SessionCookie" in security:
                security["SessionCookie"]["name"] = config.session_cookie_name
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = openapi
    install_web(application, config)
    return application


app = create_app()
