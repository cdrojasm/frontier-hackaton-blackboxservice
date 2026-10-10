"""Durable mimic generation worker.

The worker plans a shared state model, then generates one handler per endpoint
in parallel (several candidates each). Candidates are scored with a fast
in-memory replay and kept per endpoint only when they improve the whole replay;
the winning spec is validated once against PostgreSQL before approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
    MemoryStore,
    RuntimeContext,
    RuntimeStore,
    dispatch,
    find_use_case,
)
from frontier_api.workers.mimic_agent import generate_handler, generate_pipeline

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


POLL_INTERVAL_SECONDS = _env_float("MIMIC_WORKER_POLL_INTERVAL_SECONDS", 2.0)
MATCH_THRESHOLD = _env_float("MIMIC_MATCH_THRESHOLD", 0.95)
# Stop iterating after this many consecutive iterations without beating the best.
PATIENCE = max(1, int(_env_float("MIMIC_PATIENCE", 2)))
# Handler candidates generated per endpoint and iteration (best-of-N).
CANDIDATES = max(1, int(_env_float("MIMIC_CANDIDATES", 3)))
# Maximum concurrent model calls.
CONCURRENCY = max(1, int(_env_float("MIMIC_CONCURRENCY", 8)))
MAX_EXAMPLES_PER_ENDPOINT = 12
MAX_FAILURES_PER_ENDPOINT = 15

EndpointKey = tuple[str, str]
# Captured 404s on undeclared routes describe how the original answers unknown routes.
NOT_FOUND_KEY: EndpointKey = ("*", "*")


def _key(method: str, path: str) -> EndpointKey:
    return method.upper(), path.rstrip("/") or "/"


def _endpoints(design: dict, requests: list[ServiceRequestModel]) -> tuple[list[dict], dict[UUID, EndpointKey]]:
    """Resolve the planned endpoints, ordered by first observed use (writers before readers)."""
    declared = [
        {"method": str(item["method"]).upper(), "path": str(item["path"]), "purpose": item.get("purpose", "")}
        for item in design.get("endpoints", [])
        if isinstance(item, dict) and item.get("method") and item.get("path")
    ]
    endpoints: dict[EndpointKey, dict] = {}
    request_endpoint: dict[UUID, EndpointKey] = {}
    for request in requests:
        endpoint = find_use_case({"use_cases": declared}, request.method, request.path)
        if endpoint is None and request.response_code == 404:
            request_endpoint[request.id] = NOT_FOUND_KEY
            continue
        endpoint = endpoint or {
            "method": request.method.upper(),
            "path": request.path,
            "purpose": "Observed route not covered by the design phase",
        }
        key = _key(endpoint["method"], endpoint["path"])
        endpoints.setdefault(key, endpoint)
        request_endpoint[request.id] = key
    return list(endpoints.values()), request_endpoint


def _not_found(requests: list[ServiceRequestModel]) -> dict[str, object] | None:
    if not requests:
        return None
    bodies = [_decode_payload(item.response_ref) for item in requests]
    body = max(bodies, key=lambda value: bodies.count(value))
    return {"status": 404, "body": body}


def _examples(requests: list[ServiceRequestModel]) -> list[dict[str, object]]:
    """Pick representative traffic: one per status code first, then chronological fill."""
    chosen: list[ServiceRequestModel] = []
    seen_status: set[int | None] = set()
    for request in requests:
        if request.response_code not in seen_status:
            seen_status.add(request.response_code)
            chosen.append(request)
    for request in requests:
        if len(chosen) >= MAX_EXAMPLES_PER_ENDPOINT:
            break
        if request not in chosen:
            chosen.append(request)
    return [
        {
            "method": item.method,
            "path": item.path,
            "payload": _inline_value(item.payload_ref),
            "status": item.response_code,
            "response": _inline_value(item.response_ref),
        }
        for item in chosen[:MAX_EXAMPLES_PER_ENDPOINT]
    ]


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
        expected_value = json.loads(raw)
        actual_value = actual
        # Generated handlers may return an already-serialized JSON string;
        # compare its decoded value with the captured response as well.
        if isinstance(actual_value, str):
            try:
                actual_value = json.loads(actual_value)
            except json.JSONDecodeError:
                pass
        return expected_value == actual_value
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


async def _replay_one(spec: dict, request: ServiceRequestModel, context: RuntimeContext) -> dict[str, object] | None:
    """Replay one captured request; return None on match or the diff otherwise."""
    try:
        response = await dispatch(spec, request.method, request.path, {}, _decode_payload(request.payload_ref), context)
    except Exception as exc:  # noqa: BLE001 - one replay must not abort the run
        return {"error": f"{type(exc).__name__}: {exc}"}
    if response.status == request.response_code and _body_equal(request.response_ref, response.body):
        return None
    return {
        "expected_status": request.response_code,
        "actual_status": response.status,
        "expected_body": _inline_value(request.response_ref),
        "actual_body": response.body,
    }


async def _memory_replay(spec: dict, requests: list[ServiceRequestModel]) -> list[dict[str, object] | None]:
    """Score a spec chronologically against one shared in-memory store (no DB round-trips)."""
    context = RuntimeContext(MemoryStore())
    return [await _replay_one(spec, request, context) for request in requests]


def _with_request(request: ServiceRequestModel, diff: dict[str, object]) -> dict[str, object]:
    return {
        "request_id": str(request.id),
        "method": request.method,
        "path": request.path,
        "payload": _inline_value(request.payload_ref),
        **diff,
    }


async def _run_replay(
    session,
    run: MimicRunModel,
    spec: dict,
    requests: list[ServiceRequestModel],
    clone: ClonedServiceModel,
    iteration: int,
) -> tuple[int, int, list[dict[str, object]]]:
    """Validate a spec against the PostgreSQL-backed store and persist replay results.

    The real service keeps state across sequences (ID counters, balances), so
    every captured request is replayed chronologically in one shared namespace.
    """
    matched = 0
    diffs: list[dict[str, object]] = []
    namespace = f"replay-{run.id}-{iteration}"
    context = RuntimeContext(RuntimeStore(session, clone.id, namespace))
    for request in requests:
        diff = await _replay_one(spec, request, context)
        matched += int(diff is None)
        if diff is not None:
            diffs.append(_with_request(request, diff))
        session.add(ReplayResultModel(
            mimic_run_id=run.id,
            service_request_id=request.id,
            iteration=iteration,
            matched=diff is None,
            diff=diff or {},
        ))
    await session.execute(delete(MimicRecordModel).where(
        MimicRecordModel.cloned_service_id == clone.id,
        MimicRecordModel.namespace == namespace,
    ))
    return matched, len(requests), diffs


async def process_run(run: MimicRunModel) -> None:
    async with SessionFactory() as session:
        service = await session.get(ServiceModel, run.service_id)
        if service is None:
            await _fail(session, run.id, "Service not found")
            return

        insights = list((await session.scalars(
            select(InsightModel).where(
                InsightModel.service_id == service.id,
                InsightModel.status != "rejected",
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
            # Chronological order reproduces the state the real service accumulated.
            .order_by(
                ServiceRequestModel.executed_at.asc().nulls_last(),
                ServiceRequestModel.created_at,
                ServiceRequestModel.sequence_order,
            )
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
        phase_labels = {
            "design": "Design básico: entidades, endpoints, estados y verbos",
            "rules": "Reglas de negocio: invariantes, validaciones y errores",
            "tasks": "Tasks trazables: implementación y criterios de aceptación",
        }
        insight_payload = [{"id": str(i.id), "title": i.title, "kind": i.kind, "content": i.content, "confidence": i.confidence} for i in insights]
        for phase, label in phase_labels.items():
            await _event(session, run.id, f"{phase}_started", label)
        await session.commit()
        logger.info("Mimic planning started run=%s", run.id)
        try:
            pipeline: dict[str, object] = await generate_pipeline(
                service_name=service.name,
                base_url=service.base_url,
                insights=insight_payload,
                evidence=evidence,
            )
        except Exception as exc:
            logger.exception("Mimic planning failed run=%s", run.id)
            await _fail(session, run.id, f"Mimic planning failed: {exc}")
            return
        for phase, label in phase_labels.items():
            await _event(session, run.id, f"{phase}_completed", f"{label} completado")
        await session.commit()
        logger.info("Mimic planning completed run=%s", run.id)
        clone = await _replay_clone(session, service.id, run.id)
        design = pipeline.get("design") if isinstance(pipeline.get("design"), dict) else {}
        endpoints, request_endpoint = _endpoints(design, requests)
        by_endpoint: dict[EndpointKey, list[ServiceRequestModel]] = {}
        for request in requests:
            by_endpoint.setdefault(request_endpoint[request.id], []).append(request)
        examples = {key: _examples(items) for key, items in by_endpoint.items()}
        shared_context = {
            "service_name": service.name,
            "base_url": service.base_url,
            "pipeline": pipeline,
            "endpoints": endpoints,
            "validated_insights": [{"title": i.title, "kind": i.kind, "content": i.content, "confidence": i.confidence} for i in insights],
            "observed_evidence": evidence[-120:],
        }
        total = len(requests)
        await _event(session, run.id, "endpoints_resolved", f"Resolved {len(endpoints)} endpoints from {total} observed requests")
        await session.commit()

        not_found = _not_found(by_endpoint.get(NOT_FOUND_KEY, []))
        endpoints_by_key = {_key(item["method"], item["path"]): item for item in endpoints}

        def build_spec(handlers: dict[EndpointKey, dict]) -> dict:
            spec = {"service_summary": str(design.get("summary", "")), "use_cases": list(handlers.values())}
            if not_found:
                spec["not_found"] = not_found
            return spec

        def endpoint_stats(results: list[dict[str, object] | None]) -> list[dict[str, object]]:
            stats: dict[EndpointKey, dict[str, object]] = {}
            for request, diff in zip(requests, results):
                key = request_endpoint[request.id]
                label = "Rutas inexistentes" if key == NOT_FOUND_KEY else endpoints_by_key[key].get("purpose", "")
                item = stats.setdefault(key, {"method": key[0], "path": key[1], "purpose": label, "matched": 0, "total": 0})
                item["total"] += 1
                item["matched"] += int(diff is None)
            return list(stats.values())

        history: list[dict[str, object]] = []

        best_handlers: dict[EndpointKey, dict] = {}
        best_results = await _memory_replay(build_spec(best_handlers), requests)
        best_matched = 0
        best_iteration = 0
        semaphore = asyncio.Semaphore(CONCURRENCY)
        max_iterations = max(1, int(os.getenv("MIMIC_MAX_ITERATIONS", "5")))
        stale = 0

        async def candidate(endpoint: dict, index: int, failures: list[dict[str, object]]) -> dict:
            key = _key(endpoint["method"], endpoint["path"])
            async with semaphore:
                return await generate_handler(
                    shared_context=shared_context,
                    endpoint=endpoint,
                    examples=examples.get(key, []),
                    previous=best_handlers.get(key),
                    failures=failures,
                    other_handlers={
                        f"{method} {path}": str(item.get("handler_code", ""))
                        for (method, path), item in best_handlers.items() if (method, path) != key
                    },
                    temperature=0.1 if index == 0 else 0.7,
                )

        for iteration in range(1, max_iterations + 1):
            run.iterations = iteration
            run.status = "generating"
            failures: dict[EndpointKey, list[dict[str, object]]] = {}
            for request, diff in zip(requests, best_results):
                if diff is not None:
                    failures.setdefault(request_endpoint[request.id], []).append(_with_request(request, diff))
            targets = [
                endpoint for endpoint in endpoints
                if _key(endpoint["method"], endpoint["path"]) in failures
                or _key(endpoint["method"], endpoint["path"]) not in best_handlers
            ]
            await _event(
                session, run.id, "model_request",
                f"Generating {len(targets)} endpoint(s) x {CANDIDATES} candidates in parallel (iteration {iteration}/{max_iterations})",
            )
            await session.commit()
            logger.info("Mimic model request run=%s iteration=%s endpoints=%s", run.id, iteration, len(targets))
            jobs = [
                (endpoint, candidate(endpoint, index, failures.get(_key(endpoint["method"], endpoint["path"]), [])[:MAX_FAILURES_PER_ENDPOINT]))
                for endpoint in targets for index in range(CANDIDATES)
            ]
            outputs = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
            candidates: dict[EndpointKey, list[dict]] = {}
            errors = 0
            for (endpoint, _), output in zip(jobs, outputs):
                if isinstance(output, BaseException):
                    errors += 1
                    logger.warning("Mimic handler generation failed run=%s endpoint=%s %s: %s", run.id, endpoint["method"], endpoint["path"], output)
                    continue
                candidates.setdefault(_key(endpoint["method"], endpoint["path"]), []).append(output)
            if not candidates and not best_handlers:
                await _fail(session, run.id, f"Mimic generation failed: all {len(jobs)} handler requests errored")
                return
            await _event(
                session, run.id, "generated",
                f"Generated {len(jobs) - errors}/{len(jobs)} handler candidates for {len(targets)} endpoint(s) (iteration {iteration})",
                "warning" if errors else "info",
            )
            run.status = "replaying"
            await _event(session, run.id, "replaying", f"Scoring candidates with in-memory chronological replay (iteration {iteration})")
            await session.commit()

            improved = 0
            # Greedy per-endpoint selection in first-use order: a candidate is
            # kept only if it raises the matches of the whole replay, so fixing
            # one endpoint can never silently break another.
            for endpoint in targets:
                key = _key(endpoint["method"], endpoint["path"])
                for use_case in candidates.get(key, []):
                    trial = {**best_handlers, key: use_case}
                    results = await _memory_replay(build_spec(trial), requests)
                    matched = sum(diff is None for diff in results)
                    if matched > best_matched or (key not in best_handlers and matched >= best_matched):
                        improved += int(matched > best_matched)
                        best_handlers, best_results, best_matched = trial, results, matched
            rate = best_matched / total if total else 0.0
            logger.info("Mimic replay finished run=%s iteration=%s matched=%s/%s", run.id, iteration, best_matched, total)
            await _event(session, run.id, "replay_result", f"Iteration {iteration}: best matched {best_matched}/{total} requests ({rate:.1%}), {improved} endpoint improvement(s)")
            if improved:
                stale = 0
                best_iteration = iteration
                run.match_rate = rate
                history.append({
                    "iteration": iteration,
                    "match_rate": rate,
                    "matched": best_matched,
                    "total": total,
                    "use_case_count": len(best_handlers),
                    "endpoints": endpoint_stats(best_results),
                    "spec": build_spec(best_handlers),
                })
                spec = build_spec(best_handlers) | {"pipeline": {**pipeline, "iterations": history}}
                run.code_ref = store_artifact(run.id, spec, iteration)
                await _event(session, run.id, "artifact_stored", f"Stored artifact for iteration {iteration} (new best)")
            else:
                stale += 1
                await _event(session, run.id, "iteration_discarded", f"Iteration {iteration} did not improve the best ({rate:.1%})", "warning")
            await session.commit()
            if rate >= MATCH_THRESHOLD or iteration == max_iterations:
                break
            if stale >= PATIENCE:
                await _event(session, run.id, "early_stop", f"No improvement in {stale} iterations; stopping early", "warning")
                await session.commit()
                break
            failing = {
                f"{method} {path}": len(items)
                for (method, path), items in failures.items()
            }
            await _event(session, run.id, "iteration_feedback", json.dumps({"matched": best_matched, "total": total, "failing_by_endpoint_before": failing}, ensure_ascii=False), "warning")
            await session.commit()

        if not best_handlers:
            await _fail(session, run.id, "Mimic generation produced no usable handlers")
            return
        final_iteration = best_iteration or run.iterations
        spec = build_spec(best_handlers)
        await _event(session, run.id, "validating", "Validating best spec against the persistent runtime store")
        await session.commit()
        matched, total, diffs = await _run_replay(session, run, spec, requests, clone, final_iteration)
        run.match_rate = matched / total if total else 0.0
        if matched != best_matched:
            await _event(session, run.id, "validation_mismatch", f"Persistent replay matched {matched}, in-memory matched {best_matched}", "warning")
        failed_ids = {str(item["request_id"]) for item in diffs}
        # The final snapshot carries the persistent-store numbers and any handlers added on ties.
        final_entry = {
            "iteration": final_iteration,
            "match_rate": run.match_rate,
            "matched": matched,
            "total": total,
            "use_case_count": len(best_handlers),
            "endpoints": endpoint_stats([{} if str(request.id) in failed_ids else None for request in requests]),
            "spec": spec,
            "validated": True,
        }
        if history and history[-1]["iteration"] == final_iteration:
            history[-1] = final_entry
        else:
            history.append(final_entry)
        run.code_ref = store_artifact(run.id, spec | {"pipeline": {**pipeline, "iterations": history}}, final_iteration)
        run.status = "approval_required"
        await _event(
            session,
            run.id,
            f"replay_iteration_{final_iteration}",
            f"Best iteration {final_iteration}: matched {matched}/{total} requests ({run.match_rate:.1%}); threshold {MATCH_THRESHOLD:.1%}",
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
