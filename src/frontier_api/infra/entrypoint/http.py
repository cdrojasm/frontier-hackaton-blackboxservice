from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from frontier_api.application.create_service import CreateServiceCommand, CreateServiceUseCase
from frontier_api.config import get_settings
from frontier_api.infra.db.base import get_session
from frontier_api.infra.db.models import ClonedServiceModel
from frontier_api.infra.factory.service_factory import ServiceFactory

router = APIRouter()
settings = get_settings()


class CreateServiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: AnyHttpUrl
    request_interval_ms: int = Field(default=250, ge=0, le=60_000)


class ServiceResponse(BaseModel):
    id: UUID
    name: str
    base_url: str
    request_interval_ms: int
    status: str


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/health")
async def api_health() -> dict[str, str]:
    return {"status": "ok"}


MIMIC_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@router.api_route("/mimic/{slug}", methods=MIMIC_METHODS)
@router.api_route("/mimic/{slug}/{proxy_path:path}", methods=MIMIC_METHODS)
async def mimic_gateway(slug: str, request: Request, proxy_path: str = "", session: AsyncSession = Depends(get_session)):
    """Forward public mimic traffic to the private Railway runtime.

    Authorization is forwarded untouched: the runtime only uses the team Bearer
    token for POST /__reset and otherwise answers exactly like the original.
    """
    from starlette.responses import Response

    clone = await session.scalar(select(ClonedServiceModel).where(ClonedServiceModel.slug == slug))
    if clone is None or not clone.enabled:
        raise HTTPException(status_code=404, detail="Mimic not found")

    target = f"{settings.mimic_runtime_url.rstrip('/')}/mimic/{slug}/{proxy_path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(request.method, target, headers=headers, content=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Mimic runtime unavailable: {exc}") from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in {"content-length", "transfer-encoding", "connection"}
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


def service_response(service) -> ServiceResponse:
    return ServiceResponse(
        id=service.id,
        name=service.name,
        base_url=service.base_url,
        request_interval_ms=service.request_interval_ms,
        status=service.status.value if hasattr(service.status, "value") else service.status,
    )


@router.get("/api/v1/services", response_model=list[ServiceResponse])
async def list_services(session: AsyncSession = Depends(get_session)) -> list[ServiceResponse]:
    from frontier_api.infra.db.models import ServiceModel

    rows = list((await session.scalars(select(ServiceModel).order_by(ServiceModel.created_at.desc()))).all())
    return [service_response(row) for row in rows]


@router.post("/api/v1/services", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: CreateServiceRequest,
    session: AsyncSession = Depends(get_session),
) -> ServiceResponse:
    use_case = CreateServiceUseCase(ServiceFactory.service_repository(session))
    try:
        service = await use_case.execute(
            CreateServiceCommand(
                name=payload.name,
                base_url=str(payload.base_url),
                request_interval_ms=payload.request_interval_ms,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service_response(service)


@router.get("/api/v1/services/{service_id}", response_model=ServiceResponse)
async def get_service(service_id: UUID, session: AsyncSession = Depends(get_session)) -> ServiceResponse:
    service = await ServiceFactory.service_repository(session).get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return service_response(service)


@router.patch("/api/v1/services/{service_id}", response_model=ServiceResponse)
async def update_service(
    service_id: UUID,
    payload: CreateServiceRequest,
    session: AsyncSession = Depends(get_session),
) -> ServiceResponse:
    from frontier_api.infra.db.models import ServiceModel

    row = await session.get(ServiceModel, service_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Service not found")
    row.name = payload.name
    row.base_url = str(payload.base_url)
    row.request_interval_ms = payload.request_interval_ms
    await session.commit()
    await session.refresh(row)
    return service_response(row)


@router.delete("/api/v1/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(service_id: UUID, session: AsyncSession = Depends(get_session)) -> None:
    from frontier_api.infra.db.models import ServiceModel

    result = await session.execute(delete(ServiceModel).where(ServiceModel.id == service_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Service not found")
    await session.commit()

