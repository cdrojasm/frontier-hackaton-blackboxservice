"""Versioned Python artifact storage for mimic runs."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from uuid import UUID


def _module_name(value: str, index: int) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return f"{name or 'use_case'}_{index}.py"


def package_spec(spec: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        use_cases = spec.get("use_cases", [])
        manifest = {
            "format": "frontier-mimic-python-v1",
            "service_summary": spec.get("service_summary", ""),
            "pipeline": spec.get("pipeline", {}),
            "use_cases": [],
        }
        archive.writestr("__init__.py", "")
        archive.writestr("handlers/__init__.py", "")
        for index, use_case in enumerate(use_cases):
            filename = f"handlers/{_module_name(str(use_case.get('name', 'use_case')), index)}"
            archive.writestr(filename, str(use_case.get("handler_code", "")) + "\n")
            manifest["use_cases"].append({
                "name": use_case.get("name"),
                "method": use_case.get("method"),
                "path": use_case.get("path"),
                "behavior": use_case.get("behavior"),
                "module": filename,
            })
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output.getvalue()


def store_artifact(run_id: UUID, spec: dict, iteration: int = 1) -> str:
    """Upload to S3-compatible storage when configured, otherwise inline it."""
    payload = package_spec(spec)
    bucket = os.getenv("ARTIFACTS_BUCKET")
    if not bucket:
        return "inline:" + json.dumps(spec, ensure_ascii=False)
    try:
        key = f"mimics/{run_id}/artifact-{iteration}.zip"
        _s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/zip",
        )
        return f"s3://{bucket}/{key}"
    except Exception:  # noqa: BLE001 - storage failure falls back to inline local mode
        # A generation must remain inspectable if object storage is temporarily
        # unavailable; the inline representation is the local/dev fallback.
        return "inline:" + json.dumps(spec, ensure_ascii=False)


def load_artifact(reference: str) -> dict:
    if reference.startswith("inline:"):
        return json.loads(reference.removeprefix("inline:"))
    if not reference.startswith("s3://"):
        raise ValueError("Unsupported mimic artifact reference")
    bucket, key = reference.removeprefix("s3://").split("/", 1)
    payload = _s3_client().get_object(
        Bucket=bucket, Key=key
    )["Body"].read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        cases = []
        for item in manifest["use_cases"]:
            case = dict(item)
            case["handler_code"] = archive.read(item["module"]).decode("utf-8")
            cases.append(case)
        return {"service_summary": manifest.get("service_summary", ""), "pipeline": manifest.get("pipeline", {}), "use_cases": cases}


def _s3_client():
    import boto3

    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=os.getenv("ARTIFACTS_ENDPOINT") or None,
    )
