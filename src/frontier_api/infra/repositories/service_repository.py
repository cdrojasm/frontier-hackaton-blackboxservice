from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from frontier_api.domain.services import Service, ServiceStatus
from frontier_api.infra.db.models import ServiceModel


class SqlAlchemyServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, service: Service) -> Service:
        model = ServiceModel(
            id=service.id,
            name=service.name,
            base_url=service.base_url,
            request_interval_ms=service.request_interval_ms,
            status=service.status.value,
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get(self, service_id: UUID) -> Service | None:
        model = await self._session.scalar(select(ServiceModel).where(ServiceModel.id == service_id))
        return self._to_domain(model) if model else None

    @staticmethod
    def _to_domain(model: ServiceModel) -> Service:
        return Service(
            id=model.id,
            name=model.name,
            base_url=model.base_url,
            request_interval_ms=model.request_interval_ms,
            status=ServiceStatus(model.status),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

