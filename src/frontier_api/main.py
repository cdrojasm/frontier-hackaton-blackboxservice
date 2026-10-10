from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from frontier_api.config import get_settings
from frontier_api.infra.entrypoint.http import router
from frontier_api.infra.entrypoint.crud import router as crud_router
from frontier_api.infra.db.base import Base, engine
from frontier_api.infra.db import models  # noqa: F401 - registers ORM metadata

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Temporary bootstrap for the first Railway deployment. Replace with Alembic
    # migrations before production schema changes.
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    except Exception:
        logger.exception("Database bootstrap failed; API will start and retry on requests")
    yield


app = FastAPI(
    title="Frontier API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_api_cache(request: Request, call_next) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.include_router(router)
app.include_router(crud_router)

