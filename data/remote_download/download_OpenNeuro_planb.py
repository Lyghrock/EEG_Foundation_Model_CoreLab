#!/usr/bin/env python3
"""
OpenNeuro Plan B downloader for a small overseas WSL machine.

This script is intentionally independent from the Slurm launcher. It is built
for a machine with limited local storage: it lists OpenNeuro S3 objects, downloads
one bounded batch at a time, optionally uploads that batch to cloud storage, marks
uploaded objects in a local SQLite state DB, and deletes the local batch.

The local batch directory mirrors OpenNeuro paths:
  <batch_dir>/dsXXXXXX/path/in/dataset

If the upload command copies the batch directory into a cloud folder, all batches
can later be merged back into the final OpenNeuro root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


GRAPHQL_URL = "https://openneuro.org/crn/graphql"
GRAPHQL_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://openneuro.org",
    "Referer": "https://openneuro.org/",
    "User-Agent": "EEG-FM-openneuro-planb/0.1",
}
OPENNEURO_BUCKET = "openneuro.org"
DEFAULT_STATE_DIR = Path("openneuro_planb_state")
DEFAULT_LOG_DIR = Path("openneuro_planb_logs")
DEFAULT_LOCAL_BUDGET_GB = 250.0
DEFAULT_BATCH_TARGET_GB = 50.0
MIN_FREE_GB = 20.0


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    size_bytes: int = 0


@dataclass(frozen=True)
class S3Object:
    dataset: str
    key: str
    size: int
    etag: str = ""
    last_modified: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def human_bytes(num: float) -> str:
    value = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def gb_to_bytes(value: float) -> int:
    return int(float(value) * 1024**3)


def run_cmd(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    text: bool = True,
    timeout: int | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=text,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def ensure_dependencies(install_missing: bool) -> None:
    if sys.version_info < (3, 10):
        raise SystemExit(
            f"[ERROR] Python >= 3.10 is required, got {sys.version.split()[0]}"
        )
    backends = available_transfer_backends()
    print(
        f"[DEPS] Python {sys.version.split()[0]} OK; "
        f"available transfer backends: {', '.join(backends)}"
    )
    if install_missing and "awscli" not in backends:
        print(
            "[DEPS] awscli is not currently visible to Python; "
            "run_OpenNeuro_planb.sh will try to install it before launching this script."
        )


def aws_command() -> list[str] | None:
    env_cmd = os.environ.get("AWS_CLI_BIN", "").strip()
    if env_cmd:
        return shlex.split(env_cmd)
    aws_bin = shutil.which("aws")
    if aws_bin:
        return [aws_bin]
    try:
        proc = run_cmd(
            [sys.executable, "-m", "awscli", "--version"],
            check=False,
            capture=True,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode == 0:
        return [sys.executable, "-m", "awscli"]
    return None


def curl_command() -> list[str] | None:
    curl_bin = shutil.which("curl")
    return [curl_bin] if curl_bin else None


def available_transfer_backends() -> list[str]:
    backends = ["urllib"]
    if aws_command():
        backends.append("awscli")
    if curl_command():
        backends.append("curl")
    return backends


def http_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 300,
) -> bytes:
    request = Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def query_graphql(query: str, retries: int = 5, timeout: int = 60) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            body = json.dumps({"query": query}).encode("utf-8")
            raw = http_request(
                GRAPHQL_URL,
                method="POST",
                data=body,
                headers=GRAPHQL_HEADERS,
                timeout=timeout,
            )
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("errors") and not payload.get("data"):
                raise RuntimeError(payload["errors"][0])
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                wait = min(30.0, 2.0 * attempt)
                print(f"[WARN] GraphQL failed ({attempt}/{retries}): {exc}; retry in {wait:.1f}s")
                time.sleep(wait)
    raise RuntimeError(f"GraphQL failed after {retries} attempts: {last_error}")


def fetch_openneuro_eeg() -> list[Dataset]:
    page_size = 100
    cursor: str | None = None
    seen: dict[str, Dataset] = {}
    while True:
        after = f'after: "{cursor}", ' if cursor else ""
        query = f"""
        {{
          datasets(modality: "EEG", first: {page_size}, {after}) {{
            edges {{
              cursor
              node {{
                id
                name
                latestSnapshot {{
                  description {{ Name }}
                  summary {{ size }}
                }}
              }}
            }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}
        """
        payload = query_graphql(query)
        datasets = payload.get("data", {}).get("datasets")
        if not datasets:
            raise RuntimeError("OpenNeuro GraphQL returned no dataset page")
        for edge in datasets["edges"]:
            node = edge["node"]
            ds_id = node["id"]
            if ds_id in seen:
                continue
            snapshot = node.get("latestSnapshot") or {}
            summary = snapshot.get("summary") or {}
            description = snapshot.get("description") or {}
            size_raw = summary.get("size")
            try:
                size = int(size_raw) if size_raw else 0
            except (TypeError, ValueError):
                size = 0
            name = (node.get("name") or description.get("Name") or ds_id).strip()
            seen[ds_id] = Dataset(ds_id, name, size)
        page_info = datasets["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return list(seen.values())


def read_dataset_file(path: Path) -> list[Dataset]:
    datasets: list[Dataset] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        datasets.append(Dataset(line, f"[manual] {line}", 0))
    return datasets


def select_datasets(args: argparse.Namespace) -> list[Dataset]:
    if args.datasets_file:
        datasets = read_dataset_file(args.datasets_file)
    elif args.dataset:
        datasets = [Dataset(ds_id, f"[manual] {ds_id}", 0) for ds_id in args.dataset]
    else:
        print("[DISCOVER] querying OpenNeuro modality=EEG")
        datasets = fetch_openneuro_eeg()

    if args.dataset:
        wanted = set(args.dataset)
        existing = {d.id for d in datasets}
        for ds_id in wanted - existing:
            datasets.append(Dataset(ds_id, f"[manual] {ds_id}", 0))
        datasets = [d for d in datasets if d.id in wanted]

    if args.sort == "size":
        datasets.sort(key=lambda d: d.size_bytes, reverse=args.sort_desc)
    elif args.sort == "name":
        datasets.sort(key=lambda d: d.name.lower(), reverse=args.sort_desc)
    else:
        datasets.sort(key=lambda d: d.id)
    return datasets


class StateDB:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists objects (
                key text primary key,
                dataset text not null,
                size integer not null,
                etag text,
                last_modified text,
                status text not null default 'pending',
                batch_id text,
                uploaded_at text,
                updated_at text not null
            );
            create index if not exists idx_objects_dataset on objects(dataset);
            create index if not exists idx_objects_status on objects(status);
            create table if not exists batches (
                batch_id text primary key,
                status text not null,
                object_count integer not null,
                total_size integer not null,
                batch_dir text not null,
                manifest_path text not null,
                created_at text not null,
                updated_at text not null,
                uploaded_at text,
                upload_command text
            );
            """
        )
        self.conn.commit()

    def upsert_objects(self, objects: list[S3Object]) -> None:
        rows = [
            (
                obj.key,
                obj.dataset,
                obj.size,
                obj.etag,
                obj.last_modified,
                utc_now(),
            )
            for obj in objects
        ]
        self.conn.executemany(
            """
            insert into objects(key, dataset, size, etag, last_modified, updated_at)
            values(?, ?, ?, ?, ?, ?)
            on conflict(key) do update set
                dataset=excluded.dataset,
                size=excluded.size,
                etag=excluded.etag,
                last_modified=excluded.last_modified,
                updated_at=excluded.updated_at
            where objects.status != 'uploaded'
            """,
            rows,
        )
        self.conn.commit()

    def object_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "select status, count(*) as n from objects group by status"
        ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def pending_count(self) -> int:
        return int(
            self.conn.execute(
                "select count(*) from objects where status != 'uploaded'"
            ).fetchone()[0]
        )

    def active_batch(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            select * from batches
            where status in ('planned', 'downloading', 'download_failed', 'downloaded', 'upload_failed')
            order by created_at
            limit 1
            """
        ).fetchone()

    def batch_objects(self, batch_id: str) -> list[S3Object]:
        rows = self.conn.execute(
            "select dataset, key, size, etag, last_modified from objects where batch_id=? order by key",
            (batch_id,),
        ).fetchall()
        return [
            S3Object(row["dataset"], row["key"], int(row["size"]), row["etag"] or "", row["last_modified"] or "")
            for row in rows
        ]

    def next_batch_id(self) -> str:
        rows = self.conn.execute("select batch_id from batches").fetchall()
        max_index = 0
        for row in rows:
            batch_id = str(row["batch_id"])
            if not batch_id.startswith("batch_"):
                continue
            suffix = batch_id.removeprefix("batch_")
            if suffix.isdigit():
                max_index = max(max_index, int(suffix))

        while True:
            max_index += 1
            candidate = f"batch_{max_index:06d}"
            exists = self.conn.execute(
                "select 1 from batches where batch_id=?",
                (candidate,),
            ).fetchone()
            if not exists:
                return candidate

    def create_batch(
        self,
        objects: list[S3Object],
        output_dir: Path,
        state_dir: Path,
    ) -> sqlite3.Row:
        if not objects:
            raise ValueError("cannot create empty batch")
        batch_id = self.next_batch_id()
        batch_dir = output_dir / batch_id
        manifest_path = state_dir / "manifests" / f"{batch_id}.jsonl"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        total_size = sum(obj.size for obj in objects)
        now = utc_now()
        self.conn.execute(
            """
            insert into batches(batch_id, status, object_count, total_size, batch_dir,
                                manifest_path, created_at, updated_at)
            values(?, 'planned', ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                len(objects),
                total_size,
                str(batch_dir),
                str(manifest_path),
                now,
                now,
            ),
        )
        self.conn.executemany(
            """
            update objects
            set status='planned', batch_id=?, updated_at=?
            where key=?
            """,
            [(batch_id, now, obj.key) for obj in objects],
        )
        self.conn.commit()
        self.write_manifest(batch_id)
        return self.conn.execute("select * from batches where batch_id=?", (batch_id,)).fetchone()

    def next_objects(self, target_bytes: int, max_object_bytes: int | None = None) -> list[S3Object]:
        rows = self.conn.execute(
            """
            select dataset, key, size, etag, last_modified
            from objects
            where status in ('pending', 'planned', 'failed')
              and (batch_id is null or status in ('pending', 'failed'))
            order by dataset, size desc, key
            """
        ).fetchall()
        selected: list[S3Object] = []
        total = 0
        for row in rows:
            obj = S3Object(
                row["dataset"],
                row["key"],
                int(row["size"]),
                row["etag"] or "",
                row["last_modified"] or "",
            )
            if max_object_bytes is not None and obj.size > max_object_bytes:
                print(
                    f"[SKIP] {obj.key}: object is {human_bytes(obj.size)}, "
                    f"larger than local budget {human_bytes(max_object_bytes)}"
                )
                continue
            if selected and total + obj.size > target_bytes:
                break
            selected.append(obj)
            total += obj.size
            if total >= target_bytes:
                break
        return selected

    def oversized_pending_objects(self, max_object_bytes: int) -> list[S3Object]:
        rows = self.conn.execute(
            """
            select dataset, key, size, etag, last_modified
            from objects
            where status in ('pending', 'planned', 'failed')
              and (batch_id is null or status in ('pending', 'failed'))
              and size > ?
            order by size desc, key
            limit 20
            """,
            (max_object_bytes,),
        ).fetchall()
        return [
            S3Object(row["dataset"], row["key"], int(row["size"]), row["etag"] or "", row["last_modified"] or "")
            for row in rows
        ]

    def set_batch_status(self, batch_id: str, status: str, upload_command: str | None = None) -> None:
        now = utc_now()
        uploaded_at = now if status == "uploaded" else None
        self.conn.execute(
            """
            update batches
            set status=?, updated_at=?, uploaded_at=coalesce(?, uploaded_at),
                upload_command=coalesce(?, upload_command)
            where batch_id=?
            """,
            (status, now, uploaded_at, upload_command, batch_id),
        )
        self.conn.commit()

    def mark_batch_uploaded(self, batch_id: str, upload_command: str | None = None) -> None:
        now = utc_now()
        self.conn.execute(
            """
            update objects
            set status='uploaded', uploaded_at=?, updated_at=?
            where batch_id=?
            """,
            (now, now, batch_id),
        )
        self.conn.execute(
            """
            update batches
            set status='uploaded', uploaded_at=?, updated_at=?, upload_command=coalesce(?, upload_command)
            where batch_id=?
            """,
            (now, now, upload_command, batch_id),
        )
        self.conn.commit()

    def write_manifest(self, batch_id: str) -> Path:
        batch = self.conn.execute("select * from batches where batch_id=?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError(f"unknown batch {batch_id}")
        manifest_path = Path(batch["manifest_path"])
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        objects = self.batch_objects(batch_id)
        with manifest_path.open("w", encoding="utf-8") as f:
            for obj in objects:
                f.write(json.dumps(obj.__dict__, sort_keys=True) + "\n")
        return manifest_path


def list_dataset_objects(dataset_id: str) -> list[S3Object]:
    prefix = dataset_id.rstrip("/") + "/"
    token: str | None = None
    objects: list[S3Object] = []
    print(f"[LIST] {dataset_id}: listing https://s3.amazonaws.com/{OPENNEURO_BUCKET}/{prefix}")
    while True:
        params = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }
        if token:
            params["continuation-token"] = token
        url = f"https://s3.amazonaws.com/{OPENNEURO_BUCKET}?{urlencode(params)}"
        try:
            raw = http_request(url, timeout=300)
        except URLError as exc:
            raise RuntimeError(
                f"Cannot reach OpenNeuro S3 while listing {dataset_id}: {exc}. "
                "Check HTTPS egress/proxy first, for example: "
                "curl -I https://s3.amazonaws.com/openneuro.org/"
            ) from exc
        root = ET.fromstring(raw)
        for item in root.findall(".//{*}Contents"):
            key = item.findtext("{*}Key") or ""
            size_text = item.findtext("{*}Size") or "0"
            size = int(size_text)
            if key.endswith("/") or size == 0:
                continue
            objects.append(
                S3Object(
                    dataset=dataset_id,
                    key=key,
                    size=size,
                    etag=(item.findtext("{*}ETag") or "").strip('"'),
                    last_modified=item.findtext("{*}LastModified") or "",
                )
            )
        is_truncated = (root.findtext(".//{*}IsTruncated") or "").lower() == "true"
        if not is_truncated:
            break
        token = root.findtext(".//{*}NextContinuationToken")
        if not token:
            break
        if len(objects) % 10000 < 1000:
            print(f"[LIST] {dataset_id}: {len(objects)} objects so far")
    print(f"[LIST] {dataset_id}: {len(objects)} objects, {human_bytes(sum(o.size for o in objects))}")
    return objects


def build_manifest(db: StateDB, datasets: list[Dataset], refresh: bool) -> None:
    counts_before = db.object_counts()
    if counts_before and not refresh:
        print(f"[MANIFEST] existing state counts: {counts_before}; use --refresh-manifest to relist")
        return
    for ds in datasets:
        try:
            objects = list_dataset_objects(ds.id)
        except RuntimeError as exc:
            raise SystemExit(f"[ERROR] {exc}") from exc
        db.upsert_objects(objects)
    print(f"[MANIFEST] state counts: {db.object_counts()}")


def local_path_for_key(batch_dir: Path, key: str) -> Path:
    return batch_dir / key


def md5_hex(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def object_verified(path: Path, obj: S3Object) -> tuple[bool, str]:
    if not path.exists():
        return False, "missing"
    size = path.stat().st_size
    if size != obj.size:
        return False, f"size mismatch local={size} expected={obj.size}"
    if obj.etag and "-" not in obj.etag and obj.size <= 2 * 1024**3:
        local_md5 = md5_hex(path)
        if local_md5 != obj.etag:
            return False, f"md5 mismatch local={local_md5} etag={obj.etag}"
    return True, "ok"


def append_file(src: Path, dst: Path) -> None:
    with src.open("rb") as in_f, dst.open("ab") as out_f:
        for chunk in iter(lambda: in_f.read(1024 * 1024), b""):
            out_f.write(chunk)


def s3_object_url(key: str) -> str:
    return f"https://s3.amazonaws.com/{OPENNEURO_BUCKET}/{quote(key, safe='/')}"


def download_range_urllib(key: str, start: int, end: int, dest: Path, timeout: int = 600) -> None:
    request = Request(
        s3_object_url(key),
        headers={
            "Range": f"bytes={start}-{end}",
            "User-Agent": "EEG-FM-openneuro-planb/0.1",
        },
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(request, timeout=timeout) as response, dest.open("wb") as out_f:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out_f.write(chunk)


def download_range_awscli(key: str, start: int, end: int, dest: Path, timeout: int = 3600) -> None:
    aws = aws_command()
    if not aws:
        raise RuntimeError("awscli backend requested but awscli is not available")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *aws,
        "s3api",
        "get-object",
        "--bucket",
        OPENNEURO_BUCKET,
        "--key",
        key,
        "--range",
        f"bytes={start}-{end}",
        str(dest),
        "--no-sign-request",
        "--region",
        "us-east-1",
    ]
    proc = run_cmd(cmd, check=False, capture=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"awscli failed exit={proc.returncode}: {detail[:800]}")


def download_range_curl(key: str, start: int, end: int, dest: Path, timeout: int = 3600) -> None:
    curl = curl_command()
    if not curl:
        raise RuntimeError("curl backend requested but curl is not available")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *curl,
        "-L",
        "--fail",
        "--retry",
        "3",
        "--retry-delay",
        "2",
        "--range",
        f"{start}-{end}",
        "-o",
        str(dest),
        s3_object_url(key),
    ]
    proc = run_cmd(cmd, check=False, capture=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"curl failed exit={proc.returncode}: {detail[:800]}")


def download_range_backend(
    backend: str,
    key: str,
    start: int,
    end: int,
    dest: Path,
) -> None:
    if backend == "urllib":
        download_range_urllib(key, start, end, dest)
    elif backend == "awscli":
        download_range_awscli(key, start, end, dest)
    elif backend == "curl":
        download_range_curl(key, start, end, dest)
    else:
        raise ValueError(f"unknown transfer backend: {backend}")


def download_object(
    obj: S3Object,
    batch_dir: Path,
    retries: int,
    chunk_mb: int,
    transfer_backend: str,
) -> bool:
    dest = local_path_for_key(batch_dir, obj.key)
    ok, reason = object_verified(dest, obj)
    if ok:
        print(f"[HIT] {obj.key} ({human_bytes(obj.size)})")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if tmp.exists() and tmp.stat().st_size > obj.size:
        tmp.unlink()

    for attempt in range(1, retries + 1):
        print(
            f"[GET] {obj.key} -> {dest} ({human_bytes(obj.size)}), "
            f"backend={transfer_backend}, attempt {attempt}/{retries}"
        )
        start = time.time()
        chunk_bytes = max(1, int(chunk_mb)) * 1024**2
        while True:
            offset = tmp.stat().st_size if tmp.exists() else 0
            if offset >= obj.size:
                break
            end = min(obj.size - 1, offset + chunk_bytes - 1)
            part = tmp.with_name(tmp.name + f".range_{offset}_{end}")
            part.unlink(missing_ok=True)
            try:
                download_range_backend(transfer_backend, obj.key, offset, end, part)
            except Exception as exc:
                print(f"[WARN] {obj.key}: range {offset}-{end} failed: {exc}")
                part.unlink(missing_ok=True)
                break
            expected = end - offset + 1
            got = part.stat().st_size if part.exists() else 0
            if got != expected:
                print(
                    f"[WARN] {obj.key}: range size mismatch "
                    f"{offset}-{end}, got {got}, expected {expected}"
                )
                part.unlink(missing_ok=True)
                break
            append_file(part, tmp)
            part.unlink(missing_ok=True)
            current = tmp.stat().st_size
            elapsed = max(0.001, time.time() - start)
            pct = current / obj.size * 100 if obj.size else 100.0
            print(
                f"[GET] {obj.key}: {human_bytes(current)} / "
                f"{human_bytes(obj.size)} ({pct:.1f}%), "
                f"{human_bytes(current / elapsed)}/s"
            )

        if tmp.exists() and tmp.stat().st_size == obj.size:
            tmp.replace(dest)
            ok, reason = object_verified(dest, obj)
            if ok:
                elapsed = max(0.001, time.time() - start)
                print(f"[OK] {obj.key}: {human_bytes(obj.size / elapsed)}/s")
                return True
        print(f"[WARN] {obj.key}: {reason}; retrying")
        time.sleep(min(60, 5 * attempt))
    print(f"[ERROR] failed object: {obj.key}")
    return False


def write_batch_sidecar(batch: sqlite3.Row, objects: list[S3Object]) -> None:
    batch_dir = Path(batch["batch_dir"])
    sidecar_dir = batch_dir / "_planb_manifests"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar = sidecar_dir / f"{batch['batch_id']}.jsonl"
    with sidecar.open("w", encoding="utf-8") as f:
        for obj in objects:
            f.write(json.dumps(obj.__dict__, sort_keys=True) + "\n")
    meta = {
        "batch_id": batch["batch_id"],
        "created_at": batch["created_at"],
        "object_count": len(objects),
        "total_size": sum(o.size for o in objects),
        "total_size_human": human_bytes(sum(o.size for o in objects)),
    }
    (sidecar_dir / f"{batch['batch_id']}.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sample_file_check(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "size": path.stat().st_size,
        "suffix": path.suffix.lower(),
        "ok": True,
        "kind": "binary",
    }
    try:
        head = path.read_bytes()[:4096]
    except OSError as exc:
        item.update({"ok": False, "error": str(exc)})
        return item
    if not head and item["size"] > 0:
        item.update({"ok": False, "error": "could not read file head"})
        return item
    if item["suffix"] == ".json":
        item["kind"] = "json"
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            item.update({"ok": False, "error": f"json parse failed: {exc}"})
    elif item["suffix"] in {".tsv", ".csv", ".txt", ".bval", ".bvec"}:
        item["kind"] = "text"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            first_line = text.splitlines()[0] if text.splitlines() else ""
            item["first_line"] = first_line[:200]
        except Exception as exc:
            item.update({"ok": False, "error": f"text read failed: {exc}"})
    else:
        item["head_hex"] = head[:16].hex()
    return item


def scan_batch_quality(batch: sqlite3.Row, objects: list[S3Object], sample_n: int = 30) -> Path:
    batch_dir = Path(batch["batch_dir"])
    sidecar_dir = batch_dir / "_planb_manifests"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    files = [
        p for p in batch_dir.rglob("*")
        if p.is_file() and "_planb_manifests" not in p.parts
    ]
    total_size = sum(p.stat().st_size for p in files)
    ext_counts: dict[str, int] = {}
    ext_bytes: dict[str, int] = {}
    for path in files:
        suffix = path.suffix.lower() or "<none>"
        size = path.stat().st_size
        ext_counts[suffix] = ext_counts.get(suffix, 0) + 1
        ext_bytes[suffix] = ext_bytes.get(suffix, 0) + size

    rng = random.Random(batch["batch_id"])
    sample_paths = files[:]
    rng.shuffle(sample_paths)
    samples = [sample_file_check(p) for p in sample_paths[:sample_n]]
    missing = []
    size_mismatch = []
    for obj in objects:
        path = local_path_for_key(batch_dir, obj.key)
        if not path.exists():
            missing.append(obj.key)
        elif path.stat().st_size != obj.size:
            size_mismatch.append(
                {"key": obj.key, "local": path.stat().st_size, "expected": obj.size}
            )

    report = {
        "batch_id": batch["batch_id"],
        "generated_at": utc_now(),
        "object_count_manifest": len(objects),
        "file_count_local": len(files),
        "total_size_manifest": sum(o.size for o in objects),
        "total_size_local": total_size,
        "total_size_local_human": human_bytes(total_size),
        "extension_counts": dict(sorted(ext_counts.items(), key=lambda x: (-x[1], x[0]))),
        "extension_bytes": dict(sorted(ext_bytes.items(), key=lambda x: (-x[1], x[0]))),
        "missing_objects": missing[:100],
        "missing_object_count": len(missing),
        "size_mismatch": size_mismatch[:100],
        "size_mismatch_count": len(size_mismatch),
        "sample_checks": samples,
        "sample_error_count": sum(1 for item in samples if not item.get("ok")),
    }
    report_path = sidecar_dir / f"quality_report_{batch['batch_id']}.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"[QUALITY] {batch['batch_id']}: {len(files)} files, "
        f"{human_bytes(total_size)}, samples={len(samples)}, "
        f"sample_errors={report['sample_error_count']}, "
        f"missing={len(missing)}, mismatch={len(size_mismatch)}"
    )
    print(f"[QUALITY] report: {report_path}")
    return report_path


def timed_backend_range_download(
    label: str,
    backend: str,
    key: str,
    output_path: Path,
    expected_bytes: int,
) -> float:
    if output_path.exists():
        output_path.unlink()
    print(f"[SPEED] {label}: {s3_object_url(key)} range=0-{expected_bytes - 1}")
    start = time.time()
    try:
        download_range_backend(backend, key, 0, expected_bytes - 1, output_path)
    except Exception as exc:
        print(f"[SPEED] {label}: failed: {exc}")
        output_path.unlink(missing_ok=True)
        return 0.0
    elapsed = max(0.001, time.time() - start)
    got = output_path.stat().st_size if output_path.exists() else 0
    mbps = got / elapsed / 1024**2
    print(f"[SPEED] {label}: {human_bytes(got)} in {elapsed:.1f}s = {mbps:.2f} MiB/s")
    if expected_bytes and got < expected_bytes * 0.95:
        print(f"[WARN] {label}: expected about {human_bytes(expected_bytes)}, got {human_bytes(got)}")
    output_path.unlink(missing_ok=True)
    return mbps


def benchmark_transfer_backends(
    obj: S3Object,
    sample_mb: int,
    tmp_dir: Path,
    candidates: list[str] | None = None,
) -> dict[str, float]:
    available = available_transfer_backends()
    selected = candidates or available
    selected = [backend for backend in selected if backend in available]
    if not selected:
        selected = ["urllib"]
    sample_bytes = max(1, int(sample_mb)) * 1024**2
    expected = min(sample_bytes, obj.size)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, float] = {}
    for backend in selected:
        out = tmp_dir / f"openneuro_{backend}_range_test.bin"
        results[backend] = timed_backend_range_download(
            backend,
            backend,
            obj.key,
            out,
            expected,
        )
    return results


def resolve_transfer_backend(
    requested: str,
    objects: list[S3Object],
    probe_mb: int,
    tmp_dir: Path,
) -> str:
    available = available_transfer_backends()
    if requested != "auto":
        if requested not in available:
            raise SystemExit(
                f"[ERROR] transfer backend {requested!r} is not available; "
                f"available: {', '.join(available)}"
            )
        print(f"[BACKEND] using requested transfer backend: {requested}")
        return requested
    if not objects:
        return "urllib"
    probe_min = max(1, int(probe_mb)) * 1024**2
    probe_obj = next((obj for obj in objects if obj.size >= probe_min), objects[0])
    print(
        f"[BACKEND] auto-probing transfer backends on {probe_obj.key} "
        f"with sample={probe_mb} MiB"
    )
    results = benchmark_transfer_backends(probe_obj, probe_mb, tmp_dir, candidates=available)
    nonzero = {name: value for name, value in results.items() if value > 0}
    if not nonzero:
        print("[BACKEND] all probes failed; falling back to urllib")
        return "urllib"
    best = max(nonzero, key=nonzero.get)
    summary = ", ".join(f"{k}={v:.2f} MiB/s" for k, v in sorted(nonzero.items()))
    print(f"[BACKEND] selected {best}; measured {summary}")
    return best


def download_batch(
    db: StateDB,
    batch: sqlite3.Row,
    retries: int,
    object_chunk_mb: int,
    transfer_backend: str,
    max_workers: int,
) -> bool:
    batch_id = batch["batch_id"]
    batch_dir = Path(batch["batch_dir"])
    objects = db.batch_objects(batch_id)
    write_batch_sidecar(batch, objects)
    db.set_batch_status(batch_id, "downloading")
    total = sum(o.size for o in objects)
    done = 0
    failures: list[str] = []
    workers = max(1, int(max_workers))
    print(
        f"[BATCH] {batch_id}: {len(objects)} objects, {human_bytes(total)}, "
        f"backend={transfer_backend}, workers={workers}"
    )
    if workers == 1:
        for index, obj in enumerate(objects, 1):
            ok = download_object(
                obj,
                batch_dir,
                retries=retries,
                chunk_mb=object_chunk_mb,
                transfer_backend=transfer_backend,
            )
            if not ok:
                failures.append(obj.key)
                continue
            done += obj.size
            print(f"[BATCH] {batch_id}: {index}/{len(objects)}, {human_bytes(done)} / {human_bytes(total)}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_obj = {
                pool.submit(
                    download_object,
                    obj,
                    batch_dir,
                    retries,
                    object_chunk_mb,
                    transfer_backend,
                ): obj
                for obj in objects
            }
            completed = 0
            for future in as_completed(future_to_obj):
                obj = future_to_obj[future]
                completed += 1
                try:
                    ok = bool(future.result())
                except Exception as exc:
                    print(f"[ERROR] {obj.key}: worker crashed: {exc}")
                    ok = False
                if not ok:
                    failures.append(obj.key)
                    continue
                done += obj.size
                print(
                    f"[BATCH] {batch_id}: {completed}/{len(objects)}, "
                    f"{human_bytes(done)} / {human_bytes(total)}"
                )
    if failures:
        print(f"[BATCH] {batch_id}: failed {len(failures)} objects")
        db.set_batch_status(batch_id, "download_failed")
        return False
    scan_batch_quality(batch, objects)
    db.set_batch_status(batch_id, "downloaded")
    return True


def run_upload_command(command_template: str, batch: sqlite3.Row) -> bool:
    batch_dir = Path(batch["batch_dir"]).resolve()
    manifest = Path(batch["manifest_path"]).resolve()
    mapping = {
        "batch_dir": str(batch_dir),
        "batch_id": batch["batch_id"],
        "manifest": str(manifest),
    }
    command = command_template.format(**mapping)
    print(f"[UPLOAD] {command}")
    start = time.time()
    proc = subprocess.run(command, shell=True, text=True)
    elapsed = max(0.001, time.time() - start)
    if proc.returncode != 0:
        print(f"[UPLOAD] failed with exit code {proc.returncode}")
        return False
    print(f"[UPLOAD] done in {elapsed / 60:.1f} min")
    return True


def remove_batch_dir(batch: sqlite3.Row) -> None:
    batch_dir = Path(batch["batch_dir"])
    if batch_dir.exists():
        print(f"[CLEAN] removing {batch_dir}")
        shutil.rmtree(batch_dir)


def finalize_previous_manual_batch(db: StateDB, delete_after_upload: bool) -> None:
    active = db.active_batch()
    if active is None or active["status"] != "downloaded":
        return

    batch_id = active["batch_id"]
    print(
        f"[RESUME] previous batch {batch_id} is already downloaded; "
        "assuming it was manually uploaded before this run"
    )
    db.mark_batch_uploaded(batch_id, "manual-before-next-download")
    if delete_after_upload:
        remove_batch_dir(active)


def stage_has_space(output_dir: Path, local_budget_gb: float, min_free_gb: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(output_dir)
    free_gb = usage.free / 1024**3
    budget = local_budget_gb + min_free_gb
    if free_gb < min_free_gb:
        raise SystemExit(
            f"[ERROR] only {free_gb:.1f} GB free under {output_dir}; "
            f"need at least {min_free_gb:.1f} GB spare"
        )
    print(
        f"[SPACE] {output_dir}: free {free_gb:.1f} GB; "
        f"local budget {local_budget_gb:.1f} GB; reserve {min_free_gb:.1f} GB"
    )
    if free_gb < budget:
        print(
            f"[WARN] free space is below budget+reserve ({budget:.1f} GB); "
            "the batch planner will still cap by --batch-target-gb"
        )


def command_download(args: argparse.Namespace) -> int:
    ensure_dependencies(not args.no_install)
    state_dir = args.state_dir
    log_dir = args.log_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    db = StateDB(state_dir / "openneuro_planb.sqlite3")

    if args.auto_mark_previous_uploaded and not args.upload_command:
        finalize_previous_manual_batch(db, args.delete_after_upload)

    stage_has_space(args.output_dir, args.local_budget_gb, args.min_free_gb)
    datasets = select_datasets(args)
    print(f"[DATASETS] {len(datasets)} selected")
    for ds in datasets[:20]:
        size = human_bytes(ds.size_bytes) if ds.size_bytes else "unknown"
        print(f"  {ds.id:<12} {size:<12} {ds.name[:80]}")
    if len(datasets) > 20:
        print(f"  ... {len(datasets) - 20} more")

    build_manifest(db, datasets, refresh=args.refresh_manifest)
    if args.dry_run:
        max_object_bytes = gb_to_bytes(args.local_budget_gb - args.min_free_gb)
        pending = db.next_objects(
            gb_to_bytes(args.batch_target_gb),
            max_object_bytes=max_object_bytes,
        )
        print(f"[DRY-RUN] next batch: {len(pending)} objects, {human_bytes(sum(o.size for o in pending))}")
        oversized = db.oversized_pending_objects(max_object_bytes)
        if oversized:
            print(
                f"[DRY-RUN] oversized pending objects beyond usable local budget "
                f"({human_bytes(max_object_bytes)}):"
            )
            for obj in oversized[:10]:
                print(f"  {human_bytes(obj.size):>10}  {obj.key}")
        print(f"[DRY-RUN] state counts: {db.object_counts()}")
        return 0

    batch_count = 0
    while True:
        active = db.active_batch()
        if active is None:
            max_object_bytes = gb_to_bytes(args.local_budget_gb - args.min_free_gb)
            objects = db.next_objects(
                gb_to_bytes(args.batch_target_gb),
                max_object_bytes=max_object_bytes,
            )
            if not objects:
                oversized = db.oversized_pending_objects(max_object_bytes)
                if oversized:
                    print(
                        "[ERROR] pending objects are larger than the local usable budget. "
                        "PlanB does not upload partial single files; increase --local-budget-gb "
                        "or handle these objects with a streaming uploader."
                    )
                    for obj in oversized[:10]:
                        print(f"  {human_bytes(obj.size):>10}  {obj.key}")
                    return 4
                print("[DONE] no pending objects remain")
                return 0
            active = db.create_batch(objects, args.output_dir, state_dir)

        status = active["status"]
        if status in {"planned", "downloading", "download_failed"}:
            batch_objects = db.batch_objects(active["batch_id"])
            transfer_backend = resolve_transfer_backend(
                args.transfer_backend,
                batch_objects,
                args.backend_probe_mb,
                state_dir / "backend_probe",
            )
            ok = download_batch(
                db,
                active,
                retries=args.retries,
                object_chunk_mb=args.object_chunk_mb,
                transfer_backend=transfer_backend,
                max_workers=args.max_workers,
            )
            if not ok:
                return 2
            active = db.conn.execute(
                "select * from batches where batch_id=?", (active["batch_id"],)
            ).fetchone()

        if args.upload_command:
            ok = run_upload_command(args.upload_command, active)
            if not ok:
                db.set_batch_status(active["batch_id"], "upload_failed", args.upload_command)
                return 3
            db.mark_batch_uploaded(active["batch_id"], args.upload_command)
            if args.delete_after_upload:
                remove_batch_dir(active)
        else:
            print(
                "[PAUSE] batch downloaded but no --upload-command was provided. "
                "Upload it manually, then run mark-uploaded."
            )
            print(f"  batch_id: {active['batch_id']}")
            print(f"  batch_dir: {active['batch_dir']}")
            print(f"  manifest:  {active['manifest_path']}")
            return 0

        batch_count += 1
        print(f"[STATE] {db.object_counts()}")
        if args.max_batches > 0 and batch_count >= args.max_batches:
            print(f"[STOP] reached --max-batches {args.max_batches}")
            return 0


def command_mark_uploaded(args: argparse.Namespace) -> int:
    db = StateDB(args.state_dir / "openneuro_planb.sqlite3")
    batch = db.conn.execute("select * from batches where batch_id=?", (args.batch_id,)).fetchone()
    if not batch:
        print(f"[ERROR] unknown batch: {args.batch_id}", file=sys.stderr)
        return 1
    db.mark_batch_uploaded(args.batch_id, "manual")
    if args.delete_after_upload:
        remove_batch_dir(batch)
    print(f"[MARK] {args.batch_id} uploaded")
    print(f"[STATE] {db.object_counts()}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    db = StateDB(args.state_dir / "openneuro_planb.sqlite3")
    print("[STATE]", db.object_counts())
    rows = db.conn.execute(
        "select batch_id, status, object_count, total_size, batch_dir, updated_at from batches order by created_at desc limit 20"
    ).fetchall()
    for row in rows:
        print(
            f"{row['batch_id']}  {row['status']:<14} "
            f"{row['object_count']:>6} objects  {human_bytes(row['total_size']):>10}  "
            f"{row['updated_at']}  {row['batch_dir']}"
        )
    return 0


def choose_speed_object(dataset_id: str, min_size_mb: int) -> S3Object:
    objects = list_dataset_objects(dataset_id)
    objects.sort(key=lambda o: o.size, reverse=True)
    min_size = min_size_mb * 1024**2
    for obj in objects:
        if obj.size >= min_size:
            return obj
    if objects:
        return objects[0]
    raise RuntimeError(f"no objects found for {dataset_id}")


def command_speed_test(args: argparse.Namespace) -> int:
    ensure_dependencies(not args.no_install)
    obj = choose_speed_object(args.dataset, args.min_object_mb)
    sample_bytes = max(1, args.sample_mb) * 1024**2
    end_byte = max(0, min(sample_bytes, obj.size) - 1)
    print(f"[SPEED] selected {obj.key} ({human_bytes(obj.size)}), range 0-{end_byte}")
    tmp_dir = args.tmp_dir or Path(tempfile.gettempdir())
    results = benchmark_transfer_backends(obj, args.sample_mb, tmp_dir)
    nonzero = {name: value for name, value in results.items() if value > 0}
    if nonzero:
        best = max(nonzero, key=nonzero.get)
        summary = ", ".join(f"{k}={v:.2f} MiB/s" for k, v in sorted(nonzero.items()))
        print(f"[SPEED] fastest backend on this host: {best} ({summary})")
    else:
        print("[SPEED] no backend completed the speed test")

    print("[INTERPRET]")
    print("  Compare this output between H100 and the overseas WSL machine.")
    print("  If all backends are slow only on H100, the H100/network path is the bottleneck.")
    print("  If awscli is much faster than urllib on the same host, use --transfer-backend awscli.")
    print("  If urllib is faster or awscli is unavailable, keep --transfer-backend auto or urllib.")
    print("  If both are fast on H100, the current Slurm job bottleneck is likely concurrency/job config.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenNeuro object-batch Plan B downloader")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    common.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    common.add_argument(
        "--no-install",
        action="store_true",
        help="Do not ask the wrapper to install optional tools; Python itself will not install packages.",
    )

    dl = sub.add_parser("download", parents=[common])
    dl.add_argument("--output-dir", type=Path, required=True, help="Local batch staging root")
    dl.add_argument("--dataset", action="append", default=[], help="Dataset id, repeatable")
    dl.add_argument("--datasets-file", type=Path)
    dl.add_argument("--sort", choices=["size", "name", "id"], default="size")
    dl.add_argument("--sort-desc", action=argparse.BooleanOptionalAction, default=True)
    dl.add_argument("--refresh-manifest", action="store_true")
    dl.add_argument("--dry-run", action="store_true")
    dl.add_argument("--local-budget-gb", type=float, default=DEFAULT_LOCAL_BUDGET_GB)
    dl.add_argument("--batch-target-gb", type=float, default=DEFAULT_BATCH_TARGET_GB)
    dl.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB)
    dl.add_argument("--retries", type=int, default=5)
    dl.add_argument(
        "--object-chunk-mb",
        type=int,
        default=512,
        help="Range-download chunk size for single-object resume.",
    )
    dl.add_argument(
        "--transfer-backend",
        choices=["auto", "awscli", "urllib", "curl"],
        default="auto",
        help="Transfer backend. auto benchmarks available backends and uses the fastest.",
    )
    dl.add_argument(
        "--backend-probe-mb",
        type=int,
        default=64,
        help="MiB downloaded from one object to choose the fastest backend when --transfer-backend=auto.",
    )
    dl.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Parallel object downloads inside one batch. Each object is written by only one worker.",
    )
    dl.add_argument("--max-batches", type=int, default=1, help="0 means run until all pending objects finish")
    dl.add_argument(
        "--auto-mark-previous-uploaded",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Manual-upload mode convenience. When a previous batch is fully downloaded "
            "and no --upload-command is configured, the next download run assumes the "
            "operator has uploaded it, marks it uploaded, and removes its local batch dir "
            "when --delete-after-upload is enabled."
        ),
    )
    dl.add_argument(
        "--upload-command",
        default=None,
        help=(
            "Shell command template run after each batch. Placeholders: "
            "{batch_dir}, {batch_id}, {manifest}. Example: "
            "'rclone copy \"{batch_dir}\" remote:OpenNeuro_PlanB --progress'"
        ),
    )
    dl.add_argument("--delete-after-upload", action=argparse.BooleanOptionalAction, default=True)
    dl.set_defaults(func=command_download)

    mark = sub.add_parser("mark-uploaded", parents=[common])
    mark.add_argument("--batch-id", required=True)
    mark.add_argument("--delete-after-upload", action=argparse.BooleanOptionalAction, default=True)
    mark.set_defaults(func=command_mark_uploaded)

    status = sub.add_parser("status", parents=[common])
    status.set_defaults(func=command_status)

    speed = sub.add_parser("speed-test", parents=[common])
    speed.add_argument("--dataset", default="ds004024")
    speed.add_argument("--sample-mb", type=int, default=1024)
    speed.add_argument("--min-object-mb", type=int, default=2048)
    speed.add_argument("--tmp-dir", type=Path, default=None)
    speed.set_defaults(func=command_speed_test)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
