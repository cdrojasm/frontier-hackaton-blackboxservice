from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from frontier_api.application.create_service import CreateServiceCommand, CreateServiceUseCase
from frontier_api.infra.db.base import get_session
from frontier_api.infra.factory.service_factory import ServiceFactory

router = APIRouter()


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

