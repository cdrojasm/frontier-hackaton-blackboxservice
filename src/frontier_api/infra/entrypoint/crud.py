from datetime import datetime
import hashlib
import re
import secrets
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from frontier_api.infra.db.base import get_session
from frontier_api.infra.db.models import (
    ClonedServiceModel,
    DiscoveryRunModel,
    InsightModel,
    MimicRecordModel,
    MimicRunModel,
    ReplayResultModel,
    RunEventModel,
    ServiceModel,
    ServiceCredentialModel,
    ServiceRequestModel,
)

router = APIRouter(prefix="/api/v1")


class GenericPayload(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)


def serialize(model: Any, *, redact_secret: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in model.__table__.columns:
        value = getattr(model, column.name)
        if redact_secret and column.name == "value":
            value = "********"
        if isinstance(value, (UUID, datetime)):
            value = str(value)
        result[column.name] = value
    return result


def model_fields(model_type: Any) -> set[str]:
    return {column.name for column in model_type.__table__.columns if not column.primary_key}


def clean_data(model_type: Any, data: dict[str, Any]) -> dict[str, Any]:
    allowed = model_fields(model_type)
    clean = {key: value for key, value in data.items() if key in allowed}
    for key, value in list(clean.items()):
        column = model_type.__table__.columns.get(key)
        if column is not None and column.type.python_type is UUID and isinstance(value, str):
            clean[key] = UUID(value)
    return clean


async def create_model(session: AsyncSession, model_type: Any, data: dict[str, Any]) -> Any:
    model = model_type(**clean_data(model_type, data))
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def list_models(session: AsyncSession, model_type: Any, filters: dict[str, Any] | None = None) -> list[Any]:
    query = select(model_type)
    for key, value in (filters or {}).items():
        query = query.where(getattr(model_type, key) == value)
    return list((await session.scalars(query)).all())


async def get_model(session: AsyncSession, model_type: Any, item_id: UUID) -> Any:
    model = await session.get(model_type, item_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return model


@router.get("/discovery-runs")
async def list_discovery_runs(service_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, DiscoveryRunModel, {"service_id": service_id} if service_id else None)
    return [serialize(row) for row in rows]


@router.post("/services/{service_id}/discovery-runs", status_code=status.HTTP_201_CREATED)
async def create_discovery_run(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await create_model(session, DiscoveryRunModel, {"service_id": service_id, "config": payload.data})
    return serialize(row)


@router.post("/services/{service_id}/discovery-runs/import", status_code=status.HTTP_201_CREATED)
async def import_discovery_run(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    requests = payload.data.get("requests", [])
    if not isinstance(requests, list):
        raise HTTPException(status_code=400, detail="requests must be a list")
    run = DiscoveryRunModel(
        service_id=service_id,
        config={"mode": payload.data.get("mode", "react"), "seed_count": len(requests)},
    )
    session.add(run)
    await session.flush()
    for index, item in enumerate(requests):
        if not isinstance(item, dict) or not item.get("method") or not item.get("path"):
            raise HTTPException(status_code=400, detail=f"Invalid request at index {index}")
        session.add(
            ServiceRequestModel(
                service_id=service_id,
                source_run_id=run.id,
                sequence_id=uuid4(),
                sequence_order=int(item.get("sequence_order", index)),
                type="seed",
                method=str(item["method"]).upper(),
                path=str(item["path"]),
                payload_ref=item.get("payload_ref"),
                response_code=item.get("response_code"),
                response_ref=item.get("response_ref"),
            )
        )
    await session.commit()
    await session.refresh(run)
    return serialize(run)


@router.get("/discovery-runs/{run_id}")
async def get_discovery_run(run_id: UUID, session: AsyncSession = Depends(get_session)):
    return serialize(await get_model(session, DiscoveryRunModel, run_id))


@router.patch("/discovery-runs/{run_id}")
async def update_discovery_run(run_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await get_model(session, DiscoveryRunModel, run_id)
    for key, value in clean_data(DiscoveryRunModel, payload.data).items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return serialize(row)


@router.get("/service-requests")
async def list_service_requests(service_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, ServiceRequestModel, {"service_id": service_id} if service_id else None)
    return [serialize(row) for row in rows]


@router.get("/services/{service_id}/credentials")
async def list_service_credentials(service_id: UUID, session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, ServiceCredentialModel, {"service_id": service_id})
    return [serialize(row, redact_secret=True) for row in rows]


@router.post("/services/{service_id}/credentials", status_code=status.HTTP_201_CREATED)
async def create_service_credential(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await create_model(session, ServiceCredentialModel, {"service_id": service_id, **payload.data})
    return serialize(row, redact_secret=True)


@router.post("/services/{service_id}/requests", status_code=status.HTTP_201_CREATED)
async def create_service_request(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    data = {"service_id": service_id, "sequence_id": uuid4(), "sequence_order": 0, **payload.data}
    row = await create_model(session, ServiceRequestModel, data)
    return serialize(row)


@router.get("/service-requests/{request_id}")
async def get_service_request(request_id: UUID, session: AsyncSession = Depends(get_session)):
    return serialize(await get_model(session, ServiceRequestModel, request_id))


@router.patch("/service-requests/{request_id}")
async def update_service_request(request_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await get_model(session, ServiceRequestModel, request_id)
    for key, value in clean_data(ServiceRequestModel, payload.data).items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return serialize(row)


@router.get("/insights")
async def list_insights(service_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, InsightModel, {"service_id": service_id} if service_id else None)
    return [serialize(row) for row in rows]


@router.post("/services/{service_id}/insights", status_code=status.HTTP_201_CREATED)
async def create_insight(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await create_model(session, InsightModel, {"service_id": service_id, **payload.data})
    return serialize(row)


@router.patch("/insights/{insight_id}")
async def update_insight(insight_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await get_model(session, InsightModel, insight_id)
    for key, value in clean_data(InsightModel, payload.data).items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return serialize(row)


@router.get("/mimic-runs")
async def list_mimic_runs(service_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, MimicRunModel, {"service_id": service_id} if service_id else None)
    return [serialize(row) for row in rows]


@router.post("/services/{service_id}/mimic-runs", status_code=status.HTTP_201_CREATED)
async def create_mimic_run(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await create_model(session, MimicRunModel, {"service_id": service_id, **payload.data})
    return serialize(row)


@router.get("/mimic-runs/{run_id}")
async def get_mimic_run(run_id: UUID, session: AsyncSession = Depends(get_session)):
    return serialize(await get_model(session, MimicRunModel, run_id))


@router.patch("/mimic-runs/{run_id}")
async def update_mimic_run(run_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await get_model(session, MimicRunModel, run_id)
    for key, value in clean_data(MimicRunModel, payload.data).items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return serialize(row)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "mimic"


@router.post("/mimic-runs/{run_id}/approve")
async def approve_mimic_run(run_id: UUID, session: AsyncSession = Depends(get_session)):
    run = await get_model(session, MimicRunModel, run_id)
    if run.status != "approval_required":
        raise HTTPException(status_code=409, detail=f"Run is not awaiting approval: {run.status}")
    service = await session.get(ServiceModel, run.service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    clone = await session.scalar(select(ClonedServiceModel).where(ClonedServiceModel.service_id == service.id))
    if clone is None:
        base_slug = _slugify(service.name)
        slug = base_slug
        suffix = 2
        while await session.scalar(select(ClonedServiceModel.id).where(ClonedServiceModel.slug == slug)) is not None:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        clone = ClonedServiceModel(service_id=service.id, slug=slug, enabled=False)
        session.add(clone)
        await session.flush()
    elif clone.slug.startswith("replay-"):
        base_slug = _slugify(service.name)
        slug = base_slug
        suffix = 2
        while await session.scalar(select(ClonedServiceModel.id).where(
            ClonedServiceModel.slug == slug,
            ClonedServiceModel.id != clone.id,
        )) is not None:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        clone.slug = slug
    clone.active_mimic_run_id = run.id
    run.status = "ready"
    run.finished_at = datetime.now()
    session.add(RunEventModel(mimic_run_id=run.id, step="approved", level="info", message="Mimic artifact approved and ready to ship"))
    await session.commit()
    await session.refresh(clone)
    return {"run": serialize(run), "clone": serialize(clone, redact_secret=True)}


@router.post("/cloned-services/{clone_id}/ship")
async def ship_clone(clone_id: UUID, session: AsyncSession = Depends(get_session)):
    clone = await get_model(session, ClonedServiceModel, clone_id)
    if clone.active_mimic_run_id is None:
        raise HTTPException(status_code=409, detail="Clone has no approved mimic run")
    token = secrets.token_urlsafe(32)
    clone.token_hash = hashlib.sha256(token.encode()).hexdigest()
    clone.token_prefix = token[:8]
    clone.enabled = True
    await session.commit()
    await session.refresh(clone)
    return {"clone": serialize(clone, redact_secret=True), "token": token}


@router.post("/cloned-services/{clone_id}/disable")
async def disable_clone(clone_id: UUID, session: AsyncSession = Depends(get_session)):
    clone = await get_model(session, ClonedServiceModel, clone_id)
    clone.enabled = False
    await session.commit()
    await session.refresh(clone)
    return serialize(clone, redact_secret=True)


@router.get("/cloned-services")
async def list_cloned_services(session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, ClonedServiceModel)
    return [serialize(row, redact_secret=True) for row in rows]


@router.post("/services/{service_id}/clones", status_code=status.HTTP_201_CREATED)
async def create_clone(service_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await create_model(session, ClonedServiceModel, {"service_id": service_id, **payload.data})
    return serialize(row, redact_secret=True)


@router.patch("/cloned-services/{clone_id}")
async def update_clone(clone_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await get_model(session, ClonedServiceModel, clone_id)
    for key, value in clean_data(ClonedServiceModel, payload.data).items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return serialize(row, redact_secret=True)


@router.get("/run-events")
async def list_run_events(discovery_run_id: UUID | None = None, mimic_run_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    filters = {"discovery_run_id": discovery_run_id} if discovery_run_id else {}
    if mimic_run_id:
        filters = {"mimic_run_id": mimic_run_id}
    query = select(RunEventModel)
    for key, value in filters.items():
        query = query.where(getattr(RunEventModel, key) == value)
    query = query.order_by(RunEventModel.created_at, RunEventModel.id)
    rows = list((await session.scalars(query)).all())

    # Older worker versions stored only "POST /path -> status" in the event.
    # Enrich those events from the request row so the UI also works for runs
    # that were completed before the payload/response event format existed.
    request_by_position: dict[UUID, list[ServiceRequestModel]] = {}
    if discovery_run_id:
        requests = list((await session.scalars(
            select(ServiceRequestModel)
            .where(ServiceRequestModel.source_run_id == discovery_run_id)
            .order_by(ServiceRequestModel.sequence_id, ServiceRequestModel.sequence_order)
        )).all())
        request_by_position[discovery_run_id] = requests

    def inline_value(value: str | None) -> Any:
        if not value:
            return None
        return value.removeprefix("inline:") if value.startswith("inline:") else value

    result = []
    for row in rows:
        item = serialize(row)
        if discovery_run_id and row.step == "executing_requests" and row.progress_done and "payload=" not in row.message:
            request = request_by_position[discovery_run_id][row.progress_done - 1] if row.progress_done <= len(request_by_position[discovery_run_id]) else None
            if request is not None:
                item["payload"] = inline_value(request.payload_ref)
                item["response"] = inline_value(request.response_ref)
                item["response_code"] = request.response_code
                item["request_id"] = str(request.id)
                if "payload=" not in item["message"]:
                    payload_text = item["payload"] if item["payload"] is not None else "<empty>"
                    response_text = item["response"] if item["response"] is not None else (request.error or "<empty>")
                    item["message"] = (
                        f"{item['message']} | payload={payload_text} | "
                        f"response={response_text}"
                    )[:4_000]
        result.append(item)
    return result


@router.get("/replay-results")
async def list_replay_results(mimic_run_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    rows = await list_models(session, ReplayResultModel, {"mimic_run_id": mimic_run_id} if mimic_run_id else None)
    return [serialize(row) for row in rows]


@router.post("/mimic-runs/{mimic_run_id}/replay-results", status_code=status.HTTP_201_CREATED)
async def create_replay_result(mimic_run_id: UUID, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await create_model(session, ReplayResultModel, {"mimic_run_id": mimic_run_id, **payload.data})
    return serialize(row)


@router.get("/cloned-services/{clone_id}/records")
async def list_clone_records(clone_id: UUID, namespace: str = "live", session: AsyncSession = Depends(get_session)):
    rows = list((await session.scalars(select(MimicRecordModel).where(
        MimicRecordModel.cloned_service_id == clone_id,
        MimicRecordModel.namespace == namespace,
    ))).all())
    return [serialize(row) for row in rows]


@router.put("/cloned-services/{clone_id}/records/{table_name}/{record_id}")
async def put_clone_record(clone_id: UUID, table_name: str, record_id: str, payload: GenericPayload, session: AsyncSession = Depends(get_session)):
    row = await session.get(MimicRecordModel, (clone_id, "live", table_name, record_id))
    if row is None:
        row = MimicRecordModel(cloned_service_id=clone_id, namespace="live", table_name=table_name, record_id=record_id, data=payload.data)
        session.add(row)
    else:
        row.data = payload.data
    await session.commit()
    await session.refresh(row)
    return serialize(row)


@router.delete("/cloned-services/{clone_id}/records/{table_name}/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clone_record(clone_id: UUID, table_name: str, record_id: str, session: AsyncSession = Depends(get_session)) -> None:
    await session.execute(delete(MimicRecordModel).where(
        MimicRecordModel.cloned_service_id == clone_id,
        MimicRecordModel.namespace == "live",
        MimicRecordModel.table_name == table_name,
        MimicRecordModel.record_id == record_id,
    ))
    await session.commit()

