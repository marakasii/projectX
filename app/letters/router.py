from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response
from app.common.dependencies import require_csrf, require_user
from app.common.models import Items, Letter, LetterCreate, LetterPatch, UserContext


def private(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(tags=['letters'], dependencies=[Depends(private)])
READ = Annotated[UserContext, Depends(require_user)]
WRITE = Annotated[UserContext, Depends(require_csrf)]
META = {'x-package': 8, 'x-implementation': 'implemented'}


@router.post('/cases/{case_id}/letters', response_model=Letter, status_code=201, openapi_extra=META)
async def generate(case_id: UUID, body: LetterCreate, request: Request, owner: WRITE):
    return await request.app.state.letter_service.generate(owner, case_id)


@router.get('/cases/{case_id}/letters', response_model=Items[Letter], openapi_extra=META)
def listing(case_id: UUID, request: Request, owner: READ):
    return Items(items=request.app.state.letter_service.list(owner, case_id))


@router.patch('/letters/{letter_id}', response_model=Letter, openapi_extra=META)
def edit(letter_id: UUID, body: LetterPatch, request: Request, owner: WRITE):
    return request.app.state.letter_service.edit(owner, letter_id, body.content)


@router.get('/letters/{letter_id}/file', response_class=Response,
            responses={200: {'content': {'text/plain': {}}}}, openapi_extra=META)
def download(letter_id: UUID, request: Request, owner: READ):
    letter = request.app.state.letter_service.get(owner, letter_id)
    return Response(letter.content.encode('utf-8'), media_type='text/plain; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="cover-letter-{letter.id}.txt"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'X-Letter-Stale': str(letter.stale).lower(),
    })
