from dataclasses import dataclass

from frontier_api.application.ports import ServiceRepository
from frontier_api.domain.services import Service


@dataclass(frozen=True, slots=True)
class CreateServiceCommand:
    name: str
    base_url: str
    request_interval_ms: int = 250


class CreateServiceUseCase:
    def __init__(self, repository: ServiceRepository) -> None:
        self._repository = repository

    async def execute(self, command: CreateServiceCommand) -> Service:
        service = Service(
            name=command.name,
            base_url=command.base_url,
            request_interval_ms=command.request_interval_ms,
        )
        return await self._repository.save(service)

