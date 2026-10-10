from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from frontier_api.config import get_settings
from frontier_api.infra.entrypoint.http import router
from frontier_api.infra.entrypoint.crud import router as crud_router
from frontier_api.infra.db.base import Base, engine
from frontier_api.infra.db import models  # noqa: F401 - registers ORM metadata

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Temporary bootstrap for the first Railway deployment. Replace with Alembic
    # migrations before production schema changes.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Frontier API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(crud_router)

