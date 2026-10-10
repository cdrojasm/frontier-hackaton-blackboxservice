"""Durable discovery worker backed by PostgreSQL.

The worker claims one discovery run at a time with ``SKIP LOCKED``. This makes
multiple Railway replicas safe without requiring Redis for the first version.
The database remains the source of truth for progress and recovery metadata.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from frontier_api.infra.db.base import SessionFactory, engine
from frontier_api.infra.db.models import (
    DiscoveryRunModel,
    InsightModel,
    RunEventModel,
    ServiceCredentialModel,
    ServiceModel,
    ServiceRequestModel,
)
from frontier_api.workers.react_agent import DiscoveryDecision, decide

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


POLL_INTERVAL_SECONDS = _env_float("DISCOVERY_WORKER_POLL_INTERVAL_SECONDS", 2.0)
REQUEST_TIMEOUT_SECONDS = _env_float("DISCOVERY_REQUEST_TIMEOUT_SECONDS", 30.0)
RESPONSE_LIMIT_BYTES = _env_int("DISCOVERY_INLINE_RESPONSE_LIMIT_BYTES", 64_000)


def _is_private_address(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_target(base_url: str, path: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Discovery target must be an http(s) URL with a hostname")
    if path.startswith("//"):
        raise ValueError("Request path cannot override the discovery target host")

    host = parsed.hostname
    if os.getenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", "false").lower() != "true":
        if host in {"localhost", "ip6-localhost"} or _is_private_address(host):
            raise ValueError("Private and loopback discovery targets are blocked")
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve discovery target: {host}") from exc
        if any(_is_private_address(address) for address in addresses):
            raise ValueError("Discovery target resolves to a private or loopback address")

    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _request_body(payload_ref: str | None) -> object | None:
    """Support small inline JSON payloads while S3 storage is being added."""
    if not payload_ref or not payload_ref.startswith("inline:"):
        return None
    try:
        return json.loads(payload_ref.removeprefix("inline:"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid inline JSON payload") from exc


def _event_value(value: object | None, limit: int = 900) -> str:
    if value is None:
        return "<empty>"
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\x00", "")[:limit]


async def _service_headers(session: AsyncSession, service_id: UUID) -> dict[str, str]:
    rows = list(
        (
            await session.scalars(
                select(ServiceCredentialModel).where(
                    ServiceCredentialModel.service_id == service_id,
                    ServiceCredentialModel.revoked_at.is_(None),
                )
            )
        ).all()
    )
    headers: dict[str, str] = {}
    for credential in rows:
        if credential.placement != "header":
            continue
        value = credential.value
        if (
            credential.param_name.lower() == "authorization"
            and credential.kind.lower() == "bearer"
        ):
            value = value if value.lower().startswith("bearer ") else f"Bearer {value}"
        headers[credential.param_name] = value
    token = os.getenv("DISCOVERY_AUTHORIZATION_TOKEN")
    if token and "Authorization" not in headers:
        headers["Authorization"] = (
            token if token.lower().startswith("bearer ") else f"Bearer {token}"
        )
    return headers


async def _evidence_for_run(session: AsyncSession, run_id: UUID) -> list[dict[str, object]]:
    requests = list(
        (
            await session.scalars(
                select(ServiceRequestModel)
                .where(ServiceRequestModel.source_run_id == run_id)
                .order_by(ServiceRequestModel.sequence_order, ServiceRequestModel.sequence_id)
            )
        ).all()
    )
    return [
        {
            "index": index,
            "method": request.method,
            "path": request.path,
            "payload": _request_body(request.payload_ref),
            "status": request.response_code,
            "latency_ms": request.latency_ms,
            "response": (request.response_ref or "").removeprefix("inline:")[:8_000],
            "error": request.error,
            "request_type": request.type,
        }
        for index, request in enumerate(requests, start=1)
    ]


async def _store_decision(
    session: AsyncSession,
    run_id: UUID,
    service_id: UUID,
    decision: DiscoveryDecision,
) -> None:
    existing = {
        (row.title, row.content)
        for row in (
            await session.scalars(
                select(InsightModel).where(InsightModel.discovery_run_id == run_id)
            )
        ).all()
    }
    for insight in decision.insights[:10]:
        title = str(insight.get("title", "Observed behavior"))[:255]
        content = str(insight.get("content", ""))[:8_000]
        if (title, content) in existing:
            continue
        session.add(
            InsightModel(
                service_id=service_id,
                discovery_run_id=run_id,
                kind=str(insight.get("kind", "behavior"))[:32],
                title=title,
                content=content,
                confidence=max(0.0, min(float(insight.get("confidence", 0.0)), 1.0)),
                # Discovery is the approving agent in this workflow. Its
                # evidence-backed insights are immediately eligible for Mimic.
                status="validated",
            )
        )
        existing.add((title, content))
    await _add_event(session, run_id, "reasoning", decision.hypothesis[:2_000])


async def _add_probe(
    session: AsyncSession,
    run_id: UUID,
    service_id: UUID,
    decision: DiscoveryDecision,
) -> bool:
    action = decision.action
    method = str(action.get("method", "GET")).upper()
    path = str(action.get("path", ""))
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return False
    if not path.startswith("/") or path.startswith("//") or len(path) > 2_000:
        return False
    query = str(action.get("query", "") or "")
    if query and not query.startswith("?"):
        return False
    if any(marker in query for marker in ("#", "//", "http:", "https:")):
        return False
    path = f"{path}{query}" if query else path
    raw_body = action.get("body")
    if not isinstance(raw_body, str) or not raw_body.strip() or raw_body.strip() == "null":
        body = None
    else:
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return False
    payload_ref = None if body is None else f"inline:{json.dumps(body, ensure_ascii=False)}"
    auth_mode = str(action.get("auth_mode", "valid"))
    if auth_mode not in {"valid", "missing", "invalid"}:
        return False
    duplicate = await session.scalar(
        select(ServiceRequestModel.id).where(
            ServiceRequestModel.source_run_id == run_id,
            ServiceRequestModel.method == method,
            ServiceRequestModel.path == path,
            ServiceRequestModel.payload_ref == payload_ref,
        ).limit(1)
    )
    if duplicate is not None:
        return False
    count = await session.scalar(
        select(ServiceRequestModel.sequence_order)
        .where(ServiceRequestModel.source_run_id == run_id)
        .order_by(ServiceRequestModel.sequence_order.desc())
        .limit(1)
    )
    session.add(
        ServiceRequestModel(
            service_id=service_id,
            source_run_id=run_id,
            type=f"probe:{auth_mode}",
            sequence_id=uuid4(),
            sequence_order=(count or 0) + 1,
            status="pending",
            method=method,
            path=path,
            payload_ref=payload_ref,
        )
    )
    await _add_event(session, run_id, "planning_probe", f"Next probe: {method} {path}")
    return True


async def _add_event(
    session: AsyncSession,
    run_id: UUID,
    step: str,
    message: str,
    done: int | None = None,
    total: int | None = None,
    level: str = "info",
) -> None:
    session.add(
        RunEventModel(
            discovery_run_id=run_id,
            step=step,
            level=level,
            message=message,
            progress_done=done,
            progress_total=total,
        )
    )


async def _claim_run(session: AsyncSession) -> DiscoveryRunModel | None:
    result = await session.execute(
        select(DiscoveryRunModel)
        .where(DiscoveryRunModel.status == "created")
        .order_by(DiscoveryRunModel.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None
    run.status = "running"
    run.started_at = datetime.now(UTC)
    await _add_event(session, run.id, "claimed", "Discovery claimed by worker")
    await session.commit()
    return run


async def _finish_run(
    session: AsyncSession,
    run_id: UUID,
    status: str,
    error: str | None = None,
) -> None:
    run = await session.get(DiscoveryRunModel, run_id)
    if run is None:
        return
    run.status = status
    run.error = error
    run.finished_at = datetime.now(UTC)
    await _add_event(
        session,
        run_id,
        "finished",
        "Discovery completed" if status == "completed" else (error or "Discovery failed"),
        level="info" if status == "completed" else "error",
    )
    await session.commit()


async def _execute_pending(
    session: AsyncSession,
    run: DiscoveryRunModel,
    service: ServiceModel,
    headers: dict[str, str],
) -> bool:
    requests = list(
        (
            await session.scalars(
                select(ServiceRequestModel)
                .where(
                    ServiceRequestModel.source_run_id == run.id,
                    ServiceRequestModel.status == "pending",
                )
                .order_by(ServiceRequestModel.sequence_order, ServiceRequestModel.sequence_id)
            )
        ).all()
    )
    if not requests:
        return False

    failed = False
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for index, request in enumerate(requests, start=1):
            request.status = "running"
            await session.commit()
            started = time.perf_counter()
            request_headers = dict(headers)
            if request.type == "probe:missing":
                request_headers.pop("Authorization", None)
            elif request.type == "probe:invalid":
                request_headers["Authorization"] = "Bearer invalid-frontier-probe"
            try:
                target = _validate_target(service.base_url, request.path)
                request_body = _request_body(request.payload_ref)
                response = await client.request(
                    request.method.upper(),
                    target,
                    headers=request_headers,
                    json=request_body,
                )
                request.response_code = response.status_code
                request.latency_ms = round((time.perf_counter() - started) * 1000)
                body = (
                    response.content[:RESPONSE_LIMIT_BYTES]
                    .decode("utf-8", errors="replace")
                    .replace("\x00", "")
                )
                request.response_ref = f"inline:{body}"
                request.status = "completed"
                request.executed_at = datetime.now(UTC)
                message = (
                    f"{request.method.upper()} {request.path} → {response.status_code} | "
                    f"payload={_event_value(request_body)} | response={_event_value(body)}"
                )[:4_000]
            except Exception as exc:  # request-level failures must not kill the whole worker
                failed = True
                request.status = "failed"
                request.error = str(exc)[:2_000]
                request.latency_ms = round((time.perf_counter() - started) * 1000)
                request.executed_at = datetime.now(UTC)
                message = (
                    f"{request.method.upper()} {request.path} failed: {request.error} | "
                    f"payload={_event_value(_request_body(request.payload_ref))}"
                )[:4_000]
                logger.warning(
                    "Discovery request failed run=%s request=%s: %s",
                    run.id,
                    request.id,
                    exc,
                )
            await _add_event(
                session,
                run.id,
                "executing_requests",
                message,
                index,
                len(requests),
                "error" if failed else "info",
            )
            await session.commit()
            if index < len(requests):
                await asyncio.sleep(max(service.request_interval_ms, 0) / 1000)
    return failed


async def process_run(run: DiscoveryRunModel) -> None:
    async with SessionFactory() as session:
        service = await session.get(ServiceModel, run.service_id)
        if service is None:
            await _finish_run(session, run.id, "failed", "Service not found")
            return

        headers = await _service_headers(session, service.id)
        iteration = 0
        while True:
            failed = await _execute_pending(session, run, service, headers)
            if failed:
                await _finish_run(session, run.id, "failed", "One or more requests failed")
                return

            try:
                decision = await decide(
                    base_url=service.base_url,
                    evidence=await _evidence_for_run(session, run.id),
                    iteration=iteration,
                )
            except Exception as exc:
                logger.exception("ReAct agent failed for run=%s", run.id)
                await _finish_run(session, run.id, "failed", f"OpenRouter discovery failed: {exc}")
                return

            await _store_decision(session, run.id, service.id, decision)
            if decision.done:
                await session.commit()
                await _finish_run(session, run.id, "completed")
                return
            if not await _add_probe(session, run.id, service.id, decision):
                await session.commit()
                await _finish_run(session, run.id, "completed", "Agent produced no new valid probe")
                return
            await session.commit()
            iteration += 1


async def worker_loop() -> None:
    logger.info("Discovery worker started; poll interval=%ss", POLL_INTERVAL_SECONDS)
    while True:
        try:
            async with SessionFactory() as session:
                run = await _claim_run(session)
            if run is not None:
                logger.info("Processing discovery run %s", run.id)
                await process_run(run)
            else:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Discovery worker iteration failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        asyncio.run(worker_loop())
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
