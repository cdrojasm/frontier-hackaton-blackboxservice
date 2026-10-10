from sqlalchemy.ext.asyncio import AsyncSession

from frontier_api.infra.repositories.service_repository import SqlAlchemyServiceRepository


class ServiceFactory:
    """Composition root for service-related dependencies."""

    @staticmethod
    def service_repository(session: AsyncSession) -> SqlAlchemyServiceRepository:
        return SqlAlchemyServiceRepository(session)

