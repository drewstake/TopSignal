"""Safe, CORS-compatible responses when an API database operation is unavailable."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, TimeoutError

logger = logging.getLogger(__name__)


async def database_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    # Never log the exception text: it may contain SQL parameters or credentials.
    logger.warning("api_database_unavailable error_type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"code": "database_unavailable", "detail":
            "TopSignal's database is temporarily unavailable or busy. Please try again shortly."},
        headers={"Cache-Control": "no-store", "Retry-After": "5"},
    )


def install_database_error_handlers(app: FastAPI) -> None:
    # Handled inside ExceptionMiddleware so the response passes through CORS.
    # Do not retry an operation: a failed response does not prove a write failed.
    app.add_exception_handler(TimeoutError, database_unavailable)
    app.add_exception_handler(OperationalError, database_unavailable)
