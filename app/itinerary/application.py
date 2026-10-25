"""Full local composition through Matvey's public feature/checker ports."""

from app.checks.integration import CaseCheckerAdapter, CaseChecksService
from app.checks.router import router as checks_router
from app.common.config import Settings
from app.main import create_app as create_backend
from .registry import TransitRuleRegistry
from .router import router
from .service import ItineraryService


def create_app(settings: Settings | None = None, *, checker=None, document_checker=None, rebuilder=None, clock=None):
    case_checker = CaseCheckerAdapter(document_checker, clock=clock)
    app = create_backend(settings, feature_routers={6: checks_router, 7: router}, case_checker=case_checker)
    registry = TransitRuleRegistry(app.state.rule_registry)
    app.state.rule_registry = registry
    app.state.checks_service = CaseChecksService(app.state.case_service, case_checker, clock=clock)
    app.state.itinerary_service = ItineraryService(
        app.state.database, registry, checker=checker,
        rebuilder=rebuilder if rebuilder is not None else app.state.case_rebuilder, clock=clock,
    )
    app.description = (
        "VisaPilot: пакеты 1/11/2/3/4/5/6/7/9. Частичная проверка транзита IST/DXB; "
        "рейсы и допуск к перевозке не проверяются. Используются настоящие дела, архив и ручные события."
    )
    return app
