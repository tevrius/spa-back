import logging
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api.routes import router
from app.domain.errors import DomainError
from app.infrastructure.database import session_factory

logger = logging.getLogger(__name__)
app = FastAPI(
    title="Warehouse Purchase Planning API",
    version="0.1.0",
    description=(
        "Складской учёт по FEFO и детерминированное планирование закупок. Демо без авторизации."
    ),
)
app.include_router(router)


def error_response(status: int, code: str, message: str, details: dict) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(
            {"error": {"code": code, "message": message, "details": details}},
            custom_encoder={Decimal: str, date: str, datetime: str},
        ),
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return error_response(exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"location": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()
    ]
    malformed = any(e["type"] == "json_invalid" for e in errors)
    return error_response(
        400 if malformed else 422,
        "invalid_json" if malformed else "validation_error",
        "Malformed JSON" if malformed else "Request validation failed",
        {"errors": errors},
    )


@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, exc: OperationalError):
    logger.exception("Database operation failed")
    return error_response(503, "database_unavailable", "Database temporarily unavailable", {})


@app.get("/health/live", tags=["health"])
def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready():
    with session_factory()() as session:
        session.execute(text("SELECT 1"))
        session.execute(text("SELECT version_num FROM alembic_version"))
    return {"status": "ready"}
