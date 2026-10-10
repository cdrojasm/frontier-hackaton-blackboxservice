"""Durable mimic generation worker.

This first worker produces the versioned use-case contract. Compilation into an
HTTP runtime and deployment are deliberately separate approval-controlled steps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select

from frontier_api.artifact_store import store_artifact
from frontier_api.infra.db.base import SessionFactory, engine
from frontier_api.infra.db.models import (
    ClonedServiceModel,
    InsightModel,
    MimicRecordModel,
    MimicRunInsightModel,
    MimicRunModel,
    ReplayResultModel,
    RunEventModel,
    ServiceModel,
    ServiceRequestModel,
)
from frontier_api.runtime import (
    RuntimeContext,
    RuntimeRequest,
    RuntimeStore,
    _path_params,
    execute_use_case,
)
from frontier_api.workers.mimic_agent import generate_spec

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


POLL_INTERVAL_SECONDS = _env_float("MIMIC_WORKER_POLL_INTERVAL_SECONDS", 2.0)
MATCH_THRESHOLD = _env_float("MIMIC_MATCH_THRESHOLD", 0.95)


def _route_matches(template: str, path: str) -> bool:
    pattern = re.sub(r"\{[^/]+\}", r"[^/]+", template.rstrip("/") or "/")
    return re.fullmatch(pattern, path.rstrip("/") or "/") is not None


def _inline_value(value: str | None) -> str | None:
    return value.removeprefix("inline:") if value and value.startswith("inline:") else value


def _decode_payload(value: str | None) -> object | None:
    raw = _inline_value(value)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _body_equal(expected: str | None, actual: object) -> bool:
    raw = _inline_value(expected)
    if raw is None:
        return True
    try:
        return json.loads(raw) == actual
    except json.JSONDecodeError:
        return raw == str(actual)


async def _replay_clone(session, service_id: UUID, run_id: UUID) -> ClonedServiceModel:
    clone = await session.scalar(select(ClonedServiceModel).where(ClonedServiceModel.service_id == service_id))
    if clone is None:
        clone = ClonedServiceModel(service_id=service_id, slug=f"replay-{str(run_id)[:8]}", enabled=False)
        session.add(clone)
        await session.flush()
    return clone


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


async def _run_replay(
    session,
    run: MimicRunModel,
    spec: dict,
    requests: list[ServiceRequestModel],
    clone: ClonedServiceModel,
    iteration: int,
) -> tuple[int, int, list[dict[str, object]]]:
    matched = 0
    total = 0
    diffs: list[dict[str, object]] = []
    sequences: dict[UUID, list[ServiceRequestModel]] = {}
    for request in requests:
        sequences.setdefault(request.sequence_id, []).append(request)
    for sequence_id, sequence in sequences.items():
        namespace = f"replay-{run.id}-{sequence_id}"
        for request in sorted(sequence, key=lambda item: item.sequence_order):
            total += 1
            use_case = next(
                (
                    item for item in spec.get("use_cases", [])
                    if str(item.get("method", "")).upper() == request.method.upper()
                    and _route_matches(str(item.get("path", "")), request.path)
                ),
                None,
            )
            diff: dict[str, object] = {}
            is_match = False
            try:
                if use_case is None:
                    raise ValueError("No generated use case matched the request")
                params = _path_params(str(use_case.get("path", "")), request.path) or {}
                response = await execute_use_case(
                    use_case,
                    RuntimeRequest(request.method, request.path, {}, _decode_payload(request.payload_ref), params),
                    RuntimeContext(RuntimeStore(session, clone.id, namespace)),
                )
                is_match = response.status == request.response_code and _body_equal(request.response_ref, response.body)
                if not is_match:
                    diff = {
                        "expected_status": request.response_code,
                        "actual_status": response.status,
                        "expected_body": _inline_value(request.response_ref),
                        "actual_body": response.body,
                    }
            except Exception as exc:  # noqa: BLE001 - one replay must not abort the sequence
                diff = {"error": str(exc), "path": request.path}
            matched += int(is_match)
            if not is_match:
                diffs.append({"request_id": str(request.id), **diff})
            session.add(ReplayResultModel(
                mimic_run_id=run.id,
                service_request_id=request.id,
                iteration=iteration,
                matched=is_match,
                diff=diff,
            ))
        await session.execute(delete(MimicRecordModel).where(
            MimicRecordModel.cloned_service_id == clone.id,
            MimicRecordModel.namespace == namespace,
        ))
    return matched, total, diffs


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
        clone = await _replay_clone(session, service.id, run.id)
        feedback = ""
        max_iterations = max(1, int(os.getenv("MIMIC_MAX_ITERATIONS", "5")))
        for iteration in range(1, max_iterations + 1):
            try:
                spec = await generate_spec(
                    service_name=service.name,
                    base_url=service.base_url,
                    insights=[{"title": i.title, "kind": i.kind, "content": i.content, "confidence": i.confidence} for i in insights],
                    evidence=evidence,
                    iteration=iteration,
                    feedback=feedback,
                )
            except Exception as exc:
                logger.exception("Mimic generation failed for run=%s", run.id)
                await _fail(session, run.id, f"Mimic generation failed: {exc}")
                return
            run.iterations = iteration
            run.status = "generating"
            run.code_ref = store_artifact(run.id, spec, iteration)
            await _event(session, run.id, "generated", f"Generated {len(spec.get('use_cases', []))} use cases (iteration {iteration})")
            await session.flush()
            run.status = "replaying"
            await _event(session, run.id, "replaying", f"Replaying generated handlers by sequence (iteration {iteration})")
            await session.flush()
            matched, total, diffs = await _run_replay(session, run, spec, requests, clone, iteration)
            run.match_rate = matched / total if total else 0.0
            feedback = json.dumps({"matched": matched, "total": total, "diffs": diffs[-40:]}, ensure_ascii=False)
            if run.match_rate >= MATCH_THRESHOLD or iteration == max_iterations:
                break
            await _event(session, run.id, "iteration_feedback", feedback, "warning")
        run.status = "approval_required"
        await _event(
            session,
            run.id,
            f"replay_iteration_{run.iterations}",
            f"Replay matched {matched}/{total} requests ({run.match_rate:.1%}); threshold {MATCH_THRESHOLD:.1%}",
            "info" if run.match_rate >= MATCH_THRESHOLD else "warning",
        )
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
