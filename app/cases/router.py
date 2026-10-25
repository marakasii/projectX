from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.common.dependencies import require_csrf, require_user
from app.common.models import Case, CaseCreate, CasePatch, ChecklistPatch, Items, UserContext


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["cases"], dependencies=[Depends(private)])
READ = Annotated[UserContext, Depends(require_user)]
WRITE = Annotated[UserContext, Depends(require_csrf)]
META = {"x-package": 4, "x-implementation": "implemented"}


@router.post("/cases", response_model=Case, status_code=201, openapi_extra=META)
def create(body: CaseCreate, request: Request, owner: WRITE):
    return request.app.state.case_service.create(owner, body)


@router.get("/cases", response_model=Items[Case], openapi_extra=META)
def listing(request: Request, owner: READ):
    return Items(items=request.app.state.case_service.list(owner))


@router.get("/cases/{case_id}", response_model=Case, openapi_extra=META)
def get(case_id: UUID, request: Request, owner: READ):
    return request.app.state.case_service.get(owner, case_id)


@router.patch("/cases/{case_id}", response_model=Case, openapi_extra=META)
def update(case_id: UUID, body: CasePatch, request: Request, owner: WRITE):
    return request.app.state.case_service.update(owner, case_id, body)


@router.delete("/cases/{case_id}", status_code=204, openapi_extra=META)
def delete(case_id: UUID, request: Request, owner: WRITE):
    request.app.state.case_service.delete(owner, case_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.put("/cases/{case_id}/documents/{document_id}", response_model=Case, openapi_extra=META)
def attach(case_id: UUID, document_id: UUID, request: Request, owner: WRITE):
    return request.app.state.case_service.attach(owner, case_id, document_id)


@router.delete("/cases/{case_id}/documents/{document_id}", status_code=204, openapi_extra=META)
def detach(case_id: UUID, document_id: UUID, request: Request, owner: WRITE):
    request.app.state.case_service.detach(owner, case_id, document_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.patch("/cases/{case_id}/checklist/{item_id}", response_model=Case, openapi_extra=META)
def confirm(case_id: UUID, item_id: str, body: ChecklistPatch, request: Request, owner: WRITE):
    return request.app.state.case_service.confirm_action(owner, case_id, item_id, body.confirmed)
