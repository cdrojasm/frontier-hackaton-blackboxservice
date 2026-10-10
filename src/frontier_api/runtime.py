"""Private Railway runtime for dynamically loaded mimic versions.

The runtime keeps a small in-memory cache, but the active version is resolved
from PostgreSQL on every request. Publishing a new mimic version therefore
requires no process restart and is visible on the next request per replica.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from frontier_api.artifact_store import load_artifact
from frontier_api.infra.db.base import SessionFactory
from frontier_api.infra.db.models import ClonedServiceModel, MimicRecordModel, MimicRunModel

app = FastAPI(title="Frontier Mimic Runtime", version="0.1.0")


@dataclass
class LoadedArtifact:
    run_id: UUID
    spec: dict[str, Any]


@dataclass
class RuntimeRequest:
    method: str
    path: str
    query: dict[str, Any]
    body: Any
    params: dict[str, str]


class RuntimeResponse:
    def __init__(self, status: int = 200, body: Any = None, headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def to_http(self) -> JSONResponse:
        return JSONResponse(self.body, status_code=self.status, headers=self.headers)


class StoreTable:
    def __init__(self, session, clone_id: UUID, namespace: str, table_name: str):
        self.session = session
        self.clone_id = clone_id
        self.namespace = namespace
        self.table_name = table_name

    async def get(self, record_id: str) -> dict[str, Any] | None:
        row = await self.session.get(MimicRecordModel, (self.clone_id, self.namespace, self.table_name, str(record_id)))
        return None if row is None else dict(row.data)

    async def put(self, record_id: str, data: dict[str, Any]) -> dict[str, Any]:
        key = (self.clone_id, self.namespace, self.table_name, str(record_id))
        row = await self.session.get(MimicRecordModel, key)
        if row is None:
            row = MimicRecordModel(cloned_service_id=self.clone_id, namespace=self.namespace, table_name=self.table_name, record_id=str(record_id), data=data)
            self.session.add(row)
        else:
            row.data = data
        await self.session.flush()
        return data

    async def delete(self, record_id: str) -> None:
        row = await self.session.get(MimicRecordModel, (self.clone_id, self.namespace, self.table_name, str(record_id)))
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()

    async def list(self) -> list[dict[str, Any]]:
        rows = list((await self.session.scalars(select(MimicRecordModel).where(
            MimicRecordModel.cloned_service_id == self.clone_id,
            MimicRecordModel.namespace == self.namespace,
            MimicRecordModel.table_name == self.table_name,
        ))).all())
        return [dict(row.data) | {"id": row.record_id} for row in rows]

    async def next_id(self) -> str:
        result = await self.session.execute(
            select(MimicRecordModel)
            .where(
                MimicRecordModel.cloned_service_id == self.clone_id,
                MimicRecordModel.namespace == self.namespace,
                MimicRecordModel.table_name == "__seq",
                MimicRecordModel.record_id == self.table_name,
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        current = int((row.data if row else {}).get("value", 0))
        next_value = current + 1
        if row is None:
            row = MimicRecordModel(
                cloned_service_id=self.clone_id,
                namespace=self.namespace,
                table_name="__seq",
                record_id=self.table_name,
                data={"value": next_value},
            )
            self.session.add(row)
        else:
            row.data = {"value": next_value}
        await self.session.flush()
        return str(next_value)


class RuntimeStore:
    def __init__(self, session, clone_id: UUID, namespace: str):
        self.session = session
        self.clone_id = clone_id
        self.namespace = namespace

    def table(self, name: str) -> StoreTable:
        return StoreTable(self.session, self.clone_id, self.namespace, name)


@dataclass
class RuntimeContext:
    store: RuntimeStore


_cache: dict[str, LoadedArtifact] = {}


async def _active_artifact(slug: str) -> LoadedArtifact:
    async with SessionFactory() as session:
        clone = await session.scalar(
            select(ClonedServiceModel).where(
                ClonedServiceModel.slug == slug,
                ClonedServiceModel.enabled.is_(True),
            )
        )
        if clone is None or clone.active_mimic_run_id is None:
            raise LookupError("Mimic is not enabled")
        run = await session.get(MimicRunModel, clone.active_mimic_run_id)
        if run is None or not run.code_ref:
            raise LookupError("Active mimic artifact not found")
        cached = _cache.get(slug)
        if cached is not None and cached.run_id == run.id:
            return cached

        try:
            spec = load_artifact(run.code_ref)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            raise LookupError("Invalid mimic artifact") from exc
        artifact = LoadedArtifact(run_id=run.id, spec=spec)
        _cache[slug] = artifact
        return artifact


def _route_matches(template: str, path: str) -> bool:
    pattern = re.sub(r"\{[^/]+\}", r"[^/]+", template.rstrip("/") or "/")
    return re.fullmatch(pattern, path.rstrip("/") or "/") is not None


def _response_for_use_case(use_case: dict[str, Any]) -> tuple[int, Any]:
    examples = use_case.get("response_examples") or []
    if examples and isinstance(examples[0], dict):
        example = examples[0]
        return int(example.get("status", 200)), example.get("body", example)
    return 200, {"status": "ok", "use_case": use_case.get("name", "mimic")}


def _path_params(template: str, path: str) -> dict[str, str] | None:
    names = re.findall(r"\{([^/]+)\}", template)
    pattern = re.escape(template.rstrip("/") or "/")
    for name in names:
        pattern = pattern.replace("\\{" + re.escape(name) + "\\}", r"([^/]+)")
    match = re.fullmatch(pattern, path.rstrip("/") or "/")
    return None if match is None else dict(zip(names, match.groups()))


async def execute_use_case(
    use_case: dict[str, Any],
    request: RuntimeRequest,
    context: RuntimeContext,
) -> RuntimeResponse:
    """Execute one generated Python handler using the fixed runtime contract."""
    code = str(use_case.get("handler_code", "")).strip()
    if not code:
        status_code, body = _response_for_use_case(use_case)
        return RuntimeResponse(status_code, body)
    scope = {"Request": RuntimeRequest, "Response": RuntimeResponse}
    exec(compile(code, f"mimic:handler:{use_case.get('name', 'handler')}", "exec"), scope)  # noqa: S102
    handler = scope.get("handler")
    if handler is None:
        raise ValueError("handler_code must define handler")
    result = await handler(request, context)
    if not isinstance(result, RuntimeResponse):
        raise TypeError("handler must return Response(status, body)")
    return result


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/mimic/{slug}/{proxy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def invoke(slug: str, proxy_path: str, request: Request):
    try:
        artifact = await _active_artifact(slug)
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    path = "/" + proxy_path
    method = request.method.upper()
    for use_case in artifact.spec.get("use_cases", []):
        params = _path_params(str(use_case.get("path", "")), path)
        if str(use_case.get("method", "")).upper() == method and params is not None:
            body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else None
            async with SessionFactory() as session:
                clone = await session.scalar(select(ClonedServiceModel).where(ClonedServiceModel.slug == slug))
                if clone is None:
                    return JSONResponse({"error": "Mimic not found"}, status_code=404)
                namespace = request.headers.get("x-mimic-namespace", "live")
                context = RuntimeContext(RuntimeStore(session, clone.id, namespace))
                try:
                    result = await execute_use_case(
                        use_case,
                        RuntimeRequest(method, path, dict(request.query_params), body, params),
                        context,
                    )
                    await session.commit()
                    return result.to_http()
                except Exception as exc:  # noqa: BLE001 - generated handler boundary
                    await session.rollback()
                    return JSONResponse({"error": "Generated handler failed", "detail": str(exc)}, status_code=500)
    return JSONResponse({"error": "Mimic route not found", "path": path}, status_code=404)
