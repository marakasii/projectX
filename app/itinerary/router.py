"""The two package 7 routes, with real sessions, CSRF and ownership guards."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.auth.dependencies import require_csrf
from app.common.errors import AppError
from app.common.models import Route, RouteResult, UserContext
from .registry import TransitRuleRegistry
from .service import ItineraryService


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["itinerary"], dependencies=[Depends(private_response)])
METADATA = {"x-package": 7, "x-implementation": "implemented"}


def get_itinerary(request: Request) -> ItineraryService:
    configured = getattr(request.app.state, "itinerary_service", None)
    if configured is not None:
        return configured
    registry = getattr(request.app.state, "rule_registry", None)
    if registry is None:
        raise AppError(503, "transit_rules_unavailable", "Реестр транзитных правил не подключён.")
    if not isinstance(registry, TransitRuleRegistry):
        registry = TransitRuleRegistry(registry)
    return ItineraryService(request.app.state.database, registry,
                            rebuilder=getattr(request.app.state, "case_rebuilder", None))


@router.put("/cases/{case_id}/route", response_model=Route, openapi_extra=METADATA)
def put_route(case_id: UUID, body: Route, owner: Annotated[UserContext, Depends(require_csrf)],
              service: Annotated[ItineraryService, Depends(get_itinerary)]):
    return service.save(owner, case_id, body)


@router.post("/cases/{case_id}/route/check", response_model=RouteResult, openapi_extra=METADATA)
def check_route(case_id: UUID, owner: Annotated[UserContext, Depends(require_csrf)],
                service: Annotated[ItineraryService, Depends(get_itinerary)]):
    return service.run(owner, case_id)
