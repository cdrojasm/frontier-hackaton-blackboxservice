from typing import Protocol
from uuid import UUID

from frontier_api.domain.services import Service


class ServiceRepository(Protocol):
    async def save(self, service: Service) -> Service: ...

    async def get(self, service_id: UUID) -> Service | None: ...

