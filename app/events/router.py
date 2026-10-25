from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.common.dependencies import require_csrf, require_user
from app.common.models import Event, EventCreate, Items, Notification, UserContext
from app.events.service import build_notifications


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["events"], dependencies=[Depends(private)])
READ = Annotated[UserContext, Depends(require_user)]
WRITE = Annotated[UserContext, Depends(require_csrf)]
META = {"x-package": 9, "x-implementation": "implemented"}


@router.get("/cases/{case_id}/events", response_model=Items[Event], openapi_extra=META)
def list_events(case_id: UUID, request: Request, owner: READ):
    return Items(items=request.app.state.event_service.list(owner, case_id))


@router.post("/cases/{case_id}/events", response_model=Event, status_code=201, openapi_extra=META)
def create_event(case_id: UUID, body: EventCreate, request: Request, owner: WRITE):
    return request.app.state.event_service.create(owner, case_id, body)


@router.get("/cases/{case_id}/notifications", response_model=Items[Notification], openapi_extra=META)
def notifications(case_id: UUID, request: Request, owner: READ):
    case = request.app.state.case_service.get(owner, case_id)
    events = request.app.state.event_service.list(owner, case_id)
    return Items(items=build_notifications(case, events))
