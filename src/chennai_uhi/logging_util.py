from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chennai_uhi.config import write_json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).netloc.lower()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def md5_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class FetchLog:

    def __init__(self, temporal_start: str, temporal_end: str) -> None:
        self.records: list[dict[str, Any]] = []
        self.meta = {
            "stage": "extractor",
            "temporal_start_required": temporal_start,
            "temporal_end_required": temporal_end,
            "created_at": utc_now_iso(),
        }

    def add(
        self,
        *,
        layer_id: str,
        variable: str,
        source_name: str,
        query_parameters: dict[str, Any],
        resolved_source_url: str | None,
        fetch_timestamp: str | None = None,
        data_dates: list[str] | None = None,
        last_available_date: str | None = None,
        local_path: str | None = None,
        checksum_sha256: str | None = None,
        checksum_md5_source: str | None = None,
        checksum_md5_computed: str | None = None,
        native_crs: str | None = None,
        native_resolution_m: float | None = None,
        sensors: list[str] | None = None,
        notes: str | None = None,
        status: str = "ok",
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "layer_id": layer_id,
            "variable": variable,
            "source_name": source_name,
            "query_parameters": query_parameters,
            "resolved_source_url": resolved_source_url,
            "resolved_host": host_of(resolved_source_url),
            "fetch_timestamp": fetch_timestamp or utc_now_iso(),
            "data_dates": data_dates or [],
            "last_available_date": last_available_date,
            "local_path": local_path,
            "checksum_sha256": checksum_sha256,
            "checksum_md5_source": checksum_md5_source,
            "checksum_md5_computed": checksum_md5_computed,
            "native_crs": native_crs,
            "native_resolution_m": native_resolution_m,
            "sensors": sensors or [],
            "notes": notes,
            "status": status,
            "error": error,
        }
        if extra:
            rec["extra"] = extra
        self.records.append(rec)
        return rec

    def save(self, path: Path) -> Path:
        payload = {**self.meta, "n_records": len(self.records), "fetches": self.records}
        write_json(path, payload)
        return path
