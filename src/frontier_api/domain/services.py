from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4


class ServiceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


@dataclass(slots=True)
class Service:
    name: str
    base_url: str
    request_interval_ms: int = 250
    id: UUID | None = None
    status: ServiceStatus = ServiceStatus.ACTIVE
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Service name cannot be empty")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Service base_url must use http or https")
        if self.request_interval_ms < 0:
            raise ValueError("request_interval_ms cannot be negative")
        self.id = self.id or uuid4()

