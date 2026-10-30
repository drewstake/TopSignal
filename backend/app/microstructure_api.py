"""Authenticated, account-scoped research API, mounted by the application."""
from fastapi import APIRouter, Depends, HTTPException, Response
from .services import microstructure as research
from .services.microstructure_models import ResearchRequest, BookmarkInput, ExperimentInput


def install(app, *, get_db, authenticated_user, require_account):
    router = APIRouter(prefix='/api/accounts/{account_id}/microstructure', tags=['research'])

    def access(account_id: int, response: Response, db=Depends(get_db)):
        user = authenticated_user()
        require_account(db, user_id=user, account_id=account_id)
        db.rollback()  # Never retain a transaction across filesystem or SSH work.
        response.headers['Cache-Control'] = 'no-store'
        return user, account_id

    def call(function, identity, *args, **kwargs):
        try:
            return function(*identity, *args, **kwargs)
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Research artifact not found') from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Research storage or private recording host is unavailable') from exc

    @router.get('/coverage')
    def coverage(identity=Depends(access)):
        return call(lambda user, account: research.source_read(user, {'operation': 'inventory'}), identity)

    @router.get('/notebook')
    def notebook(identity=Depends(access)):
        return call(research.list_artifacts, identity)

    @router.post('/jobs', status_code=202)
    def create_job(body: ResearchRequest, identity=Depends(access)):
        return call(research.submit, identity, body)

    @router.get('/jobs/{job_id}')
    def job(job_id: str, result: bool = False, identity=Depends(access)):
        return call(research.get_job, identity, job_id, include_result=result)

    @router.post('/jobs/{job_id}/cancel')
    def cancel(job_id: str, identity=Depends(access)):
        return call(research.cancel_job, identity, job_id)

    @router.get('/jobs/{job_id}/export')
    def export(job_id: str, identity=Depends(access)):
        text = call(research.export_result, identity, job_id)
        return Response(text, media_type='text/csv', headers={'Cache-Control': 'no-store',
            'Content-Disposition': f'attachment; filename="microstructure-{job_id}.csv"'})

    @router.post('/bookmarks', status_code=201)
    def bookmark(body: BookmarkInput, identity=Depends(access)):
        return call(research.save_bookmark, identity, body)

    @router.post('/experiments', status_code=201)
    def experiment(body: ExperimentInput, identity=Depends(access)):
        return call(research.save_experiment, identity, body)

    app.include_router(router)
