"""Durable mimic generation worker.

This first worker produces the versioned use-case contract. Compilation into an
HTTP runtime and deployment are deliberately separate approval-controlled steps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from frontier_api.infra.db.base import SessionFactory, engine
from frontier_api.infra.db.models import (
    InsightModel,
    MimicRunInsightModel,
    MimicRunModel,
    RunEventModel,
    ServiceModel,
    ServiceRequestModel,
)
from frontier_api.workers.mimic_agent import generate_spec

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


POLL_INTERVAL_SECONDS = _env_float("MIMIC_WORKER_POLL_INTERVAL_SECONDS", 2.0)


async def _event(session, run_id: UUID, step: str, message: str, level: str = "info") -> None:
    session.add(RunEventModel(mimic_run_id=run_id, step=step, level=level, message=message))


async def _claim(session) -> MimicRunModel | None:
    result = await session.execute(
        select(MimicRunModel)
        .where(MimicRunModel.status == "created")
        .order_by(MimicRunModel.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None
    run.status = "generating"
    run.started_at = datetime.now(UTC)
    run.iterations = 0
    await _event(session, run.id, "claimed", "Mimic claimed by worker")
    await session.commit()
    return run


async def _fail(session, run_id: UUID, error: str) -> None:
    run = await session.get(MimicRunModel, run_id)
    if run is None:
        return
    run.status = "failed"
    run.error = error[:2_000]
    run.finished_at = datetime.now(UTC)
    await _event(session, run_id, "finished", error[:2_000], "error")
    await session.commit()


async def process_run(run: MimicRunModel) -> None:
    async with SessionFactory() as session:
        service = await session.get(ServiceModel, run.service_id)
        if service is None:
            await _fail(session, run.id, "Service not found")
            return

        insights = list((await session.scalars(
            select(InsightModel).where(
                InsightModel.service_id == service.id,
                InsightModel.status == "validated",
            ).order_by(InsightModel.created_at)
        )).all())
        if not insights:
            await _fail(session, run.id, "No validated insights available for mimic generation")
            return

        for insight in insights:
            session.add(MimicRunInsightModel(mimic_run_id=run.id, insight_id=insight.id))
        await _event(session, run.id, "collecting_insights", f"Using {len(insights)} validated insights")
        await session.commit()

        requests = list((await session.scalars(
            select(ServiceRequestModel)
            .where(ServiceRequestModel.service_id == service.id)
            .order_by(ServiceRequestModel.sequence_id, ServiceRequestModel.sequence_order)
        )).all())
        evidence = [
            {
                "method": item.method,
                "path": item.path,
                "status": item.response_code,
                "payload": item.payload_ref,
                "response": item.response_ref,
            }
            for item in requests
        ]
        try:
            spec = await generate_spec(
                service_name=service.name,
                base_url=service.base_url,
                insights=[{"title": i.title, "kind": i.kind, "content": i.content, "confidence": i.confidence} for i in insights],
                evidence=evidence,
                iteration=1,
            )
        except Exception as exc:
            logger.exception("Mimic generation failed for run=%s", run.id)
            await _fail(session, run.id, f"Mimic generation failed: {exc}")
            return

        run.iterations = 1
        run.code_ref = "inline:" + json.dumps(spec, ensure_ascii=False)
        run.status = "approval_required"
        await _event(session, run.id, "generated", f"Generated {len(spec.get('use_cases', []))} use cases")
        await _event(session, run.id, "approval_required", "Generation ready for review")
        await session.commit()


async def worker_loop() -> None:
    logger.info("Mimic worker started; poll interval=%ss", POLL_INTERVAL_SECONDS)
    while True:
        try:
            async with SessionFactory() as session:
                run = await _claim(session)
            if run is not None:
                await process_run(run)
            else:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mimic worker iteration failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        asyncio.run(worker_loop())
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
