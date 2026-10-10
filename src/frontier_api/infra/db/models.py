from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from frontier_api.infra.db.base import Base


class ServiceModel(Base):
    __tablename__ = "services"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_interval_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=250)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DiscoveryRunModel(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ServiceCredentialModel(Base):
    __tablename__ = "service_credentials"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    placement: Mapped[str] = mapped_column(String(32), nullable=False)
    param_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceRequestModel(Base):
    __tablename__ = "service_requests"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("discovery_runs.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    sequence_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    payload_ref: Mapped[str | None] = mapped_column(Text)
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_ref: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InsightModel(Base):
    __tablename__ = "insights"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    discovery_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("discovery_runs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InsightEvidenceModel(Base):
    __tablename__ = "insight_evidence"

    insight_id: Mapped[UUID] = mapped_column(ForeignKey("insights.id", ondelete="CASCADE"), primary_key=True)
    service_request_id: Mapped[UUID] = mapped_column(ForeignKey("service_requests.id", ondelete="CASCADE"), primary_key=True)
    note: Mapped[str | None] = mapped_column(Text)


class MimicRunModel(Base):
    __tablename__ = "mimic_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_rate: Mapped[float | None] = mapped_column()
    code_ref: Mapped[str | None] = mapped_column(Text)
    lambda_version: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class MimicRunInsightModel(Base):
    __tablename__ = "mimic_run_insights"

    mimic_run_id: Mapped[UUID] = mapped_column(ForeignKey("mimic_runs.id", ondelete="CASCADE"), primary_key=True)
    insight_id: Mapped[UUID] = mapped_column(ForeignKey("insights.id", ondelete="RESTRICT"), primary_key=True)


class ReplayResultModel(Base):
    __tablename__ = "replay_results"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    mimic_run_id: Mapped[UUID] = mapped_column(ForeignKey("mimic_runs.id", ondelete="CASCADE"), nullable=False)
    service_request_id: Mapped[UUID] = mapped_column(ForeignKey("service_requests.id", ondelete="CASCADE"), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    diff: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RunEventModel(Base):
    __tablename__ = "run_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    discovery_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("discovery_runs.id", ondelete="CASCADE"))
    mimic_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("mimic_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    progress_done: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClonedServiceModel(Base):
    __tablename__ = "cloned_services"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    active_mimic_run_id: Mapped[UUID | None] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_hash: Mapped[str | None] = mapped_column(String(255))
    token_prefix: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MimicRecordModel(Base):
    __tablename__ = "mimic_records"

    cloned_service_id: Mapped[UUID] = mapped_column(ForeignKey("cloned_services.id", ondelete="CASCADE"), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(120), primary_key=True)
    table_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

