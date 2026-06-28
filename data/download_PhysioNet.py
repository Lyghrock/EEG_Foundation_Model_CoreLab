#!/usr/bin/env python3
"""
download_PhysioNet.py - strict EEG-only PhysioNet downloader.

Examples:
  # Validate only; this should be rejected because butqdb is ECG, not EEG.
  python download_PhysioNet.py --dataset butqdb/1.0.0 --dry-run

  # Download an EEG dataset after validation.
  python download_PhysioNet.py --dataset neuro-stress-resilience-hci/1.0.0 \
      --output-dir /mnt/ddn/shared/datasets/eeg/PhysioNet

  # Credentialed datasets: keep secrets out of git and shell history.
  PHYSIONET_USERNAME=Lyghrock PHYSIONET_PASSWORD=... \
      python download_PhysioNet.py --dataset some-slug/1.0.0

Dataset specs can be:
  - slug/version
  - slug (uses --default-version)
  - https://physionet.org/content/slug/version/
  - https://physionet.org/files/slug/version/
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import getpass
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests


PHYSIONET_BASE = "https://physionet.org"
DATABASE_URL = f"{PHYSIONET_BASE}/about/database/"
CHALLENGE_URL = f"{PHYSIONET_BASE}/about/challenge/moody-challenge"
TOPIC_DISCOVERY_URLS = [
    f"{PHYSIONET_BASE}/content/?topic=eeg",
    f"{PHYSIONET_BASE}/content/?topic=electroencephalogram",
    f"{PHYSIONET_BASE}/content/?topic=polysomnogram",
    f"{PHYSIONET_BASE}/content/?topic=sleep+study",
    f"{PHYSIONET_BASE}/content/?topic=sleep+staging",
    f"{PHYSIONET_BASE}/content/?topic=seizure",
]
DEFAULT_VERSION = "1.0.0"
PAGE_TIMEOUT = 60
WGET_CUT_DIRS = "3"  # files/<slug>/<version>/...

EEG_TEXT_RE = re.compile(
    r"\bEEG\b|electroencephal(?:ogram|ography|ographic|ograms|ographs)?",
    re.IGNORECASE,
)
EEG_CONTEXT_RE = re.compile(
    r"dataset|data|database|recording|recorded|records|contains|include|"
    r"including|signals?|channels?|scalp|polysomno|PSG|sleep|seizure|BCI|"
    r"brain[- ]computer|evoked|imagery|headband|spectra",
    re.IGNORECASE,
)
EEG_STRONG_CONTEXT_RE = re.compile(
    r"scalp|polysomno|PSG|sleep|seizure|BCI|brain[- ]computer|"
    r"evoked|imagery|headband|neuro|EEG\s+channels?",
    re.IGNORECASE,
)
EEG_CITATION_RE = re.compile(
    r"\bdoi\b|citation|references?|article|journal|proceedings|"
    r"@article|bibliography",
    re.IGNORECASE,
)
NON_EEG_RE = re.compile(r"\bnon[- ]?eeg\b", re.IGNORECASE)
INCIDENTAL_EEG_RE = re.compile(
    r"not\s+acquired\s+as\s+indicators?\s+of\s+cardiac\s+activity|"
    r"such\s+as\s+EEG\s+signals?",
    re.IGNORECASE,
)
CARDIAC_CONTEXT_RE = re.compile(
    r"\bECG\b|electrocardiogram|electrocardiography|cardiac|heart\s*beats?|arrhythm",
    re.IGNORECASE,
)
NON_RAW_EEG_PRIMARY_RE = re.compile(
    r"calcium\s+imaging|wide[- ]field\s+calcium|instantaneous\s+heart\s+rate\s+and\s+accelerometry|"
    r"accelerometry\s+dataset\s+with\s+EEG\s+sleep\s+stage\s+labels|step\s+count|actigraphy|"
    r"multitaper\s+spectra|spectra\s+recorded|EEG\s+spectra",
    re.IGNORECASE,
)
RAW_EEG_TITLE_RE = re.compile(
    r"scalp\s+EEG|EEG\s+(database|signals?|dataset|recordings?)|"
    r"electroencephalogram.*dataset|polysomno|PSG|Sleep-EDF|seizure",
    re.IGNORECASE,
)
NEGATIVE_MODALITY_RE = re.compile(
    r"\bECG\b|electrocardiogram|electrocardiography",
    re.IGNORECASE,
)
SIZE_RE = re.compile(
    r"Total\s+uncompressed\s+size\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*"
    r"(B|KB|MB|GB|TB)",
    re.IGNORECASE,
)
ACCESS_RE = re.compile(
    r"\b(Open Access|Credentialed Access|Restricted Access)\b",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
CONTENT_VERSION_PATH_RE = re.compile(
    r"^/content/([A-Za-z0-9_.-]+)/([^/?#]+)/?$",
    re.IGNORECASE,
)
CONTENT_SLUG_PATH_RE = re.compile(
    r"^/content/([A-Za-z0-9_.-]+)/?$",
    re.IGNORECASE,
)
KNOWN_EEG_DATASETS = {
    "challenge-2018/1.0.0": {
        "title": "You Snooze, You Win: the PhysioNet/Computing in Cardiology Challenge 2018",
        "access": "open-access",
        "eeg_evidence": (
            "curated allowlist: PhysioNet/CinC Challenge 2018 is a PSG sleep-arousal "
            "dataset with EEG channels plus auxiliary physiological channels"
        ),
    },
    "chbmit/1.0.0": {
        "title": "CHB-MIT Scalp EEG Database",
        "access": "open-access",
        "eeg_evidence": "curated allowlist: pediatric scalp EEG seizure database",
    },
    "siena-scalp-eeg/1.0.0": {
        "title": "Siena Scalp EEG Database",
        "access": "open-access",
        "eeg_evidence": "curated allowlist: adult scalp EEG seizure database in EDF format",
    },
}
CURATED_EEG_SEED_DATASETS = {
    # EEG topic/search hits. These still go through normal validation unless
    # also present in KNOWN_EEG_DATASETS above.
    "auditory-eeg/1.0.0",
    "bidsleep-dataset/1.0.0",
    "chbmit/1.0.0",
    "eeg-eye-gaze-data/1.0.0",
    "eeg-eye-gaze-for-fls-tasks/1.0.0",
    "eeg-gaba-anesthesia/1.0.0",
    "eeg-power-anesthesia/1.0.0",
    "eegmat/1.0.0",
    "eegmmidb/1.0.0",
    "hmc-sleep-staging/1.1",
    "ltrsvp/1.0.0",
    "motion-artifact/1.0.0",
    "nch-sleep/3.1.0",
    "psg-ipa/1.0.0",
    "shhpsgdb/1.0.0",
    "siena-scalp-eeg/1.0.0",
    "sleep-edf/1.0.0",
    "sleep-edfx/1.0.0",
    "ucddb/1.0.0",
}


@dataclasses.dataclass(frozen=True)
class DatasetSpec:
    slug: str
    version: str

    @property
    def id(self) -> str:
        return f"{self.slug}/{self.version}"

    @property
    def content_url(self) -> str:
        return f"{PHYSIONET_BASE}/content/{self.slug}/{self.version}/"

    @property
    def files_url(self) -> str:
        return f"{PHYSIONET_BASE}/files/{self.slug}/{self.version}/"


@dataclasses.dataclass
class DatasetInfo:
    spec: DatasetSpec
    title: str = ""
    access: str = "unknown"
    size_bytes: int = 0
    is_eeg: bool = False
    eeg_evidence: str = ""
    reject_reason: str = ""
    content_url: str = ""
    files_url: str = ""

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)

    @property
    def size_str(self) -> str:
        if self.size_bytes <= 0:
            return "unknown"
        if self.size_bytes < 1024 ** 3:
            return f"{self.size_bytes / (1024 ** 2):.1f} MB"
        return f"{self.size_gb:.1f} GB"


class SizeTracker:
    """Reserve estimated download size across parallel workers."""

    def __init__(self, max_gb: Optional[float]):
        self.max_gb = max_gb
        self.reserved_gb = 0.0
        self._lock = threading.Lock()

    def reserve(self, dataset: DatasetInfo) -> bool:
        if self.max_gb is None or dataset.size_bytes <= 0:
            return True
        size_gb = dataset.size_gb
        with self._lock:
            if self.reserved_gb + size_gb > self.max_gb:
                return False
            self.reserved_gb += size_gb
            return True


class Printer:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self._lock = threading.Lock()

    def log(self, message: str):
        if self.quiet:
            return
        with self._lock:
            print(message, flush=True)


def known_eeg_info(spec: DatasetSpec, note: str = "") -> DatasetInfo | None:
    known = KNOWN_EEG_DATASETS.get(spec.id)
    if not known:
        return None
    evidence = known["eeg_evidence"]
    if note:
        evidence = f"{evidence}; fallback reason: {note}"
    return DatasetInfo(
        spec=spec,
        title=known["title"],
        access=known["access"],
        size_bytes=int(known.get("size_bytes", 0) or 0),
        is_eeg=True,
        eeg_evidence=evidence,
        content_url=spec.content_url,
        files_url=spec.files_url,
    )


def known_eeg_specs() -> list[DatasetSpec]:
    specs = []
    for dataset_id in sorted(KNOWN_EEG_DATASETS):
        slug, version = dataset_id.split("/", 1)
        specs.append(DatasetSpec(slug=slug, version=version))
    return specs


def curated_seed_specs() -> list[DatasetSpec]:
    specs = []
    for dataset_id in sorted(CURATED_EEG_SEED_DATASETS):
        slug, version = dataset_id.split("/", 1)
        specs.append(DatasetSpec(slug=slug, version=version))
    return specs


def append_unique_specs(specs: list[DatasetSpec], new_specs: list[DatasetSpec]) -> int:
    seen = {spec.id for spec in specs}
    added = 0
    for spec in new_specs:
        if spec.id in seen:
            continue
        specs.append(spec)
        seen.add(spec.id)
        added += 1
    return added


def strip_html(raw_html: str) -> str:
    raw_html = re.sub(
        r"<(script|style).*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_title(raw_html: str, fallback: str) -> str:
    match = TITLE_RE.search(raw_html)
    if not match:
        return fallback
    return strip_html(match.group(1)) or fallback


def parse_size(text: str) -> int:
    match = SIZE_RE.search(text)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).upper()
    scale = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }[unit]
    return int(value * scale)


def parse_access(text: str) -> str:
    match = ACCESS_RE.search(text)
    if not match:
        return "unknown"
    return match.group(1).lower().replace(" ", "-")


def parse_dataset_spec(raw: str, default_version: str) -> DatasetSpec:
    raw = raw.strip()
    if not raw:
        raise ValueError("empty dataset spec")

    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 3 and parts[0] in {"content", "files"}:
            return DatasetSpec(slug=parts[1], version=parts[2])
        raise ValueError(f"unsupported PhysioNet URL: {raw}")

    parts = [p for p in raw.split("/") if p]
    if len(parts) == 1:
        return DatasetSpec(slug=parts[0], version=default_version)
    if len(parts) == 2:
        return DatasetSpec(slug=parts[0], version=parts[1])
    raise ValueError(f"unsupported dataset spec: {raw}")


def read_dataset_specs(args: argparse.Namespace) -> list[DatasetSpec]:
    raw_specs: list[str] = []
    raw_specs.extend(args.dataset or [])
    raw_specs.extend(args.url or [])

    if args.datasets_file:
        with open(args.datasets_file, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    raw_specs.append(line)

    seen: set[str] = set()
    specs: list[DatasetSpec] = []
    for raw in raw_specs:
        spec = parse_dataset_spec(raw, args.default_version)
        if spec.id in seen:
            continue
        seen.add(spec.id)
        specs.append(spec)
    return specs


def discover_dataset_specs(
    session: requests.Session,
    discover_url: str,
    default_version: str,
    workers: int,
    limit: Optional[int] = None,
) -> list[DatasetSpec]:
    """Discover PhysioNet project/version links from the official database page."""
    status, raw_html = fetch_text(session, discover_url)
    if status >= 400:
        raise RuntimeError(f"discovery page HTTP {status}: {discover_url}")

    seen: set[str] = set()
    items: list[DatasetSpec | str] = []
    slug_seen: set[str] = set()
    auth = session.auth
    headers = dict(session.headers)

    def add_item(item: DatasetSpec | str):
        if isinstance(item, DatasetSpec):
            key = item.id
            if key in seen:
                return
            seen.add(key)
        else:
            if item in slug_seen:
                return
            slug_seen.add(item)
        items.append(item)

    def resolve_slug(slug: str) -> DatasetSpec:
        worker_session = requests.Session()
        worker_session.headers.update(headers)
        if auth:
            worker_session.auth = auth
        return resolve_latest_spec(worker_session, slug, default_version)

    for href in HREF_RE.findall(raw_html):
        parsed = urlparse(urljoin(discover_url, href))
        version_match = CONTENT_VERSION_PATH_RE.match(parsed.path)
        slug_match = CONTENT_SLUG_PATH_RE.match(parsed.path)
        if version_match:
            add_item(DatasetSpec(slug=version_match.group(1), version=version_match.group(2)))
        elif slug_match:
            slug = slug_match.group(1)
            if slug:
                add_item(slug)
        if limit is not None and len(items) >= limit:
            break

    specs: list[DatasetSpec] = []
    slug_items = [item for item in items if isinstance(item, str)]
    resolved_by_slug: dict[str, DatasetSpec] = {}
    if slug_items:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(resolve_slug, slug): slug for slug in slug_items}
            for future in concurrent.futures.as_completed(future_map):
                slug = future_map[future]
                resolved_by_slug[slug] = future.result()

    spec_seen: set[str] = set()
    for item in items:
        spec = resolved_by_slug[item] if isinstance(item, str) else item
        if spec.id in spec_seen:
            continue
        spec_seen.add(spec.id)
        specs.append(spec)
    return specs


def resolve_latest_spec(
    session: requests.Session,
    slug: str,
    default_version: str,
) -> DatasetSpec:
    try:
        response = session.get(
            f"{PHYSIONET_BASE}/content/{slug}/",
            timeout=PAGE_TIMEOUT,
            allow_redirects=True,
        )
        parsed = urlparse(response.url)
        match = CONTENT_VERSION_PATH_RE.match(parsed.path)
        if match:
            return DatasetSpec(slug=match.group(1), version=match.group(2))
        for href in HREF_RE.findall(response.text):
            parsed_href = urlparse(urljoin(response.url, href))
            match = CONTENT_VERSION_PATH_RE.match(parsed_href.path)
            if match and match.group(1) == slug:
                return DatasetSpec(slug=match.group(1), version=match.group(2))
    except requests.RequestException:
        pass
    return DatasetSpec(slug=slug, version=default_version)


def make_session(
    username: Optional[str],
    password: Optional[str],
) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "EEG-FM-PhysioNet-downloader/0.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    if username and password:
        session.auth = (username, password)
    return session


def load_auth_config(path: Optional[Path]) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid PhysioNet config JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"PhysioNet config must be a JSON object: {path}")
    return {
        str(k): str(v)
        for k, v in data.items()
        if v is not None
    }


def fetch_text(
    session: requests.Session,
    url: str,
    retries: int = 4,
    backoff_sec: float = 2.0,
) -> tuple[int, str]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=PAGE_TIMEOUT)
            return response.status_code, response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(backoff_sec * attempt)
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}")


def link_hrefs(raw_html: str, base_url: str) -> list[str]:
    links: list[str] = []
    for href in HREF_RE.findall(raw_html):
        if href.startswith("#") or href.startswith("?"):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc and parsed.netloc != "physionet.org":
            continue
        links.append(full)
    return links


def probe_files_index(
    session: requests.Session,
    spec: DatasetSpec,
    max_subdirs: int,
) -> tuple[str, str]:
    """Return (plain index text, EEG evidence) from /files pages."""
    status, root_html = fetch_text(session, spec.files_url)
    if status >= 400:
        return "", f"files index HTTP {status}"

    texts = [strip_html(root_html)]
    evidence = find_eeg_evidence(texts[0], "files index")
    if evidence:
        return texts[0], evidence

    checked = 0
    for link in link_hrefs(root_html, spec.files_url):
        if checked >= max_subdirs:
            break
        parsed = urlparse(link)
        if not parsed.path.endswith("/"):
            path_text = parsed.path
            evidence = find_eeg_evidence(path_text, "file path")
            if evidence:
                return " ".join(texts), evidence
            continue
        if not parsed.path.startswith(f"/files/{spec.slug}/{spec.version}/"):
            continue
        if parsed.path.rstrip("/").endswith(f"/files/{spec.slug}/{spec.version}"):
            continue
        checked += 1
        try:
            status, child_html = fetch_text(session, link)
        except RuntimeError:
            continue
        if status >= 400:
            continue
        child_text = strip_html(child_html)
        texts.append(child_text)
        evidence = find_eeg_evidence(child_text, f"files child index {checked}")
        if evidence:
            return " ".join(texts), evidence

    return " ".join(texts), ""


def find_eeg_evidence(text: str, source: str) -> str:
    for match in EEG_TEXT_RE.finditer(text):
        start = max(match.start() - 80, 0)
        end = min(match.end() + 120, len(text))
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        if not is_acceptable_eeg_evidence(snippet, source):
            continue
        return f"{source}: {snippet}"
    return ""


def is_acceptable_eeg_evidence(snippet: str, source: str) -> bool:
    if NON_EEG_RE.search(snippet):
        return False
    if INCIDENTAL_EEG_RE.search(snippet):
        return False
    if CARDIAC_CONTEXT_RE.search(snippet) and not EEG_STRONG_CONTEXT_RE.search(snippet):
        return False
    if source == "title":
        return True
    if source in {"file path", "files index"} or source.startswith("files child index"):
        return True
    if EEG_CITATION_RE.search(snippet) and not EEG_CONTEXT_RE.search(snippet):
        return False
    return bool(EEG_CONTEXT_RE.search(snippet))


def looks_like_non_raw_eeg_primary(title: str, content_text: str) -> bool:
    if not NON_RAW_EEG_PRIMARY_RE.search(f"{title} {content_text[:2000]}"):
        return False
    return not RAW_EEG_TITLE_RE.search(title)


def resolve_dataset(
    spec: DatasetSpec,
    session: requests.Session,
    probe_subdirs: int,
) -> DatasetInfo:
    info = DatasetInfo(
        spec=spec,
        title=spec.slug,
        content_url=spec.content_url,
        files_url=spec.files_url,
    )

    try:
        status, content_html = fetch_text(session, spec.content_url)
    except RuntimeError as exc:
        known = known_eeg_info(spec, note=f"content page request failed: {exc}")
        if known is not None:
            return known
        info.reject_reason = f"content page request failed: {exc}"
        return info

    if status >= 400:
        known = known_eeg_info(spec, note=f"content page HTTP {status}")
        if known is not None:
            return known
        info.reject_reason = f"content page HTTP {status}"
        return info

    content_text = strip_html(content_html)
    info.title = extract_title(content_html, spec.slug)
    info.access = parse_access(content_text)
    info.size_bytes = parse_size(content_text)

    if looks_like_non_raw_eeg_primary(info.title, content_text):
        info.reject_reason = "REJECT_NON_RAW_EEG_PRIMARY: page suggests EEG labels/adjunct signals but primary dataset is not raw EEG/PSG"
        return info

    evidence = find_eeg_evidence(info.title, "title")
    if not evidence:
        evidence = find_eeg_evidence(content_text, "content page")
    if not evidence:
        try:
            _, evidence = probe_files_index(session, spec, probe_subdirs)
        except RuntimeError as exc:
            evidence = f"files index request failed: {exc}"

    if evidence and not evidence.startswith("files index HTTP"):
        info.is_eeg = True
        info.eeg_evidence = evidence
        return info

    known = known_eeg_info(spec, note="official page did not provide parseable EEG evidence")
    if known is not None:
        known.size_bytes = info.size_bytes
        known.access = info.access if info.access != "unknown" else known.access
        return known

    negative = NEGATIVE_MODALITY_RE.search(content_text)
    if negative:
        info.reject_reason = "REJECT_NON_EEG: page contains non-EEG modality but no EEG evidence"
    elif evidence:
        info.reject_reason = f"REJECT_NO_EEG_EVIDENCE: {evidence}"
    else:
        info.reject_reason = "REJECT_NO_EEG_EVIDENCE"
    return info


def format_table(infos: list[DatasetInfo]):
    print()
    print("=" * 118)
    print(f"  PhysioNet datasets: {len(infos)}")
    print("=" * 118)
    print(f"  {'ID':<36} {'STATUS':<16} {'SIZE':<12} {'ACCESS':<20} TITLE")
    print(f"  {'-' * 110}")
    for info in infos:
        status = "EEG_OK" if info.is_eeg else "REJECT_NON_EEG"
        print(
            f"  {info.spec.id:<36} {status:<16} {info.size_str:<12} "
            f"{info.access:<20} {info.title[:42]}"
        )
        if info.is_eeg and info.eeg_evidence:
            print(f"    evidence: {info.eeg_evidence[:100]}")
        if not info.is_eeg:
            print(f"    reason: {info.reject_reason}")
    print("=" * 118)
    print()


def write_eeg_list(infos: list[DatasetInfo], output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{info.spec.id}  # {info.size_str} | {info.title}"
        for info in infos
        if info.is_eeg
    ]
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def resolve_all_datasets(
    specs: list[DatasetSpec],
    username: Optional[str],
    password: Optional[str],
    probe_subdirs: int,
    resolve_workers: int,
) -> list[DatasetInfo]:
    if resolve_workers <= 1:
        session = make_session(username, password)
        return [
            resolve_dataset(spec, session, probe_subdirs=probe_subdirs)
            for spec in specs
        ]

    def worker(spec: DatasetSpec) -> DatasetInfo:
        session = make_session(username, password)
        return resolve_dataset(spec, session, probe_subdirs=probe_subdirs)

    infos: list[DatasetInfo] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=resolve_workers) as pool:
        future_map = {pool.submit(worker, spec): spec for spec in specs}
        completed = 0
        total = len(future_map)
        for future in concurrent.futures.as_completed(future_map):
            completed += 1
            info = future.result()
            infos.append(info)
            if total >= 20 and (completed % 20 == 0 or completed == total):
                print(f"[DISCOVER] resolved {completed}/{total}", file=sys.stderr)
    return infos


def build_wget_config(
    username: Optional[str],
    password: Optional[str],
) -> Optional[Path]:
    if not username or not password:
        return None
    fd, path = tempfile.mkstemp(prefix="physionet_wget_", suffix=".conf")
    os.close(fd)
    config = Path(path)
    config.write_text(
        "\n".join(
            [
                f"http_user = {username}",
                f"http_password = {password}",
                "auth_no_challenge = on",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    return config


def acquire_dataset_lock(final_dir: Path) -> Optional[Path]:
    final_dir.mkdir(parents=True, exist_ok=True)
    lock_path = final_dir / ".download.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()}\n")
        f.write(f"time={time.time()}\n")
    return lock_path


def run_checksum(final_dir: Path, printer: Printer, dataset_id: str) -> bool:
    checksum = final_dir / "SHA256SUMS.txt"
    if not checksum.exists():
        printer.log(f"[{dataset_id}] [WARN] SHA256SUMS.txt not found; checksum skipped")
        return True
    proc = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS.txt"],
        cwd=str(final_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        printer.log(f"[{dataset_id}] [ERROR] checksum failed")
        printer.log(proc.stdout[-4000:])
        return False
    printer.log(f"[{dataset_id}] checksum OK")
    return True


def download_one(
    info: DatasetInfo,
    output_dir: Path,
    wget_config: Optional[Path],
    username: Optional[str],
    ask_password: bool,
    checksum: bool,
    quiet: bool,
    printer: Printer,
) -> tuple[str, str]:
    dataset_id = info.spec.id
    if not info.is_eeg:
        return dataset_id, "rejected"

    final_dir = output_dir / info.spec.slug / info.spec.version
    marker = final_dir / ".download_complete.json"
    if marker.exists():
        printer.log(f"[{dataset_id}] [SKIP] completion marker exists: {marker}")
        return dataset_id, "skipped"

    lock_path = acquire_dataset_lock(final_dir)
    if lock_path is None:
        printer.log(f"[{dataset_id}] [SKIP] lock exists; another process may be downloading")
        return dataset_id, "locked"

    try:
        cmd = [
            "wget",
            "-r",
            "-N",
            "-c",
            "-np",
            "-nH",
            "--cut-dirs",
            WGET_CUT_DIRS,
            "-R",
            "index.html*",
            "-P",
            str(final_dir),
        ]

        if wget_config is not None:
            cmd.extend(["--config", str(wget_config)])
        elif username and ask_password:
            cmd.extend(["--user", username, "--ask-password"])

        if quiet:
            cmd.append("--quiet")
        else:
            cmd.extend(["--progress=dot:giga"])

        cmd.append(info.files_url)

        printable_cmd = [c if c != str(wget_config) else "<wget-auth-config>" for c in cmd]
        printer.log(f"[{dataset_id}] running: {' '.join(printable_cmd)}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if not quiet:
                printer.log(f"[{dataset_id}] {line.rstrip()}")
        proc.wait()
        if proc.returncode != 0:
            printer.log(f"[{dataset_id}] [ERROR] wget exit code {proc.returncode}")
            return dataset_id, "failed"

        if checksum and not run_checksum(final_dir, printer, dataset_id):
            return dataset_id, "failed"

        marker.write_text(
            json.dumps(
                {
                    "dataset": dataset_id,
                    "title": info.title,
                    "files_url": info.files_url,
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "size_bytes_estimate": info.size_bytes,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        printer.log(f"[{dataset_id}] [DONE] {final_dir}")
        return dataset_id, "ok"
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def run_downloads(
    infos: list[DatasetInfo],
    args: argparse.Namespace,
    username: Optional[str],
    password: Optional[str],
) -> int:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    max_gb = None
    if args.max_size_mb is not None:
        max_gb = args.max_size_mb / 1024
    elif args.max_size is not None:
        max_gb = args.max_size
    if max_gb is not None and max_gb <= 0:
        max_gb = None

    tracker = SizeTracker(max_gb)
    printer = Printer(args.quiet)
    eligible: list[DatasetInfo] = []
    for info in infos:
        if not info.is_eeg:
            continue
        if not tracker.reserve(info):
            printer.log(
                f"[{info.spec.id}] [SKIP] exceeds max-size limit "
                f"({info.size_str}, reserved {tracker.reserved_gb:.2f} GB)"
            )
            continue
        eligible.append(info)

    if not eligible:
        print("[ERROR] no EEG-validated PhysioNet datasets to download", file=sys.stderr)
        return 1

    if args.ask_password and args.max_workers > 1 and not password:
        print(
            "[ERROR] --ask-password is not compatible with parallel workers; "
            "set PHYSIONET_PASSWORD or use --max-workers 1",
            file=sys.stderr,
        )
        return 1

    wget_config = build_wget_config(username, password)
    try:
        status_counts: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
            futures = [
                pool.submit(
                    download_one,
                    info,
                    output_dir,
                    wget_config,
                    username,
                    args.ask_password,
                    args.checksum,
                    args.quiet,
                    printer,
                )
                for info in eligible
            ]
            for future in concurrent.futures.as_completed(futures):
                _, status = future.result()
                status_counts[status] = status_counts.get(status, 0) + 1

        print()
        print("=" * 70)
        print("PhysioNet download summary")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")
        print(f"  output_dir: {output_dir.resolve()}")
        print("=" * 70)
        return 0 if status_counts.get("failed", 0) == 0 else 1
    finally:
        if wget_config is not None:
            try:
                wget_config.unlink()
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strict EEG-only PhysioNet downloader"
    )
    parser.add_argument(
        "--dataset",
        action="append",
        help="PhysioNet slug/version, slug, /content URL, or /files URL. Repeatable.",
    )
    parser.add_argument(
        "--url",
        action="append",
        help="Alias for --dataset when passing a PhysioNet URL. Repeatable.",
    )
    parser.add_argument(
        "--datasets-file",
        type=Path,
        help="Text file with one PhysioNet dataset spec per line.",
    )
    parser.add_argument(
        "--default-version",
        default=DEFAULT_VERSION,
        help=f"Version used when --dataset only gives a slug (default: {DEFAULT_VERSION}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./physionet_eeg"),
        help="Download root directory.",
    )
    parser.add_argument("--dry-run", "--dryrun", action="store_true", dest="dry_run")
    parser.add_argument(
        "--discover",
        action="store_true",
        help=(
            "Discover PhysioNet projects from the official database page, "
            "the challenge list unless disabled, and curated known EEG entries; "
            "then keep EEG-validated datasets."
        ),
    )
    parser.add_argument(
        "--discover-url",
        default=DATABASE_URL,
        help=f"Discovery source page (default: {DATABASE_URL}).",
    )
    parser.add_argument(
        "--discover-primary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scan --discover-url as the primary discovery source (default: true).",
    )
    parser.add_argument(
        "--discover-challenges",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=f"Also discover project links from the PhysioNet challenge list (default: true; {CHALLENGE_URL}).",
    )
    parser.add_argument(
        "--discover-topics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also discover project links from EEG/sleep/seizure PhysioNet topic pages.",
    )
    parser.add_argument(
        "--discover-topic-url",
        action="append",
        default=[],
        help="Additional PhysioNet topic/search URL to scan for dataset links. Repeatable.",
    )
    parser.add_argument(
        "--include-known-eeg",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="In --discover mode, include curated known EEG PhysioNet datasets such as challenge-2018/1.0.0.",
    )
    parser.add_argument(
        "--include-curated-seeds",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="In --discover mode, include curated EEG/PSG candidate specs that still require normal validation.",
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=None,
        help="Limit number of discovered project links for quick tests.",
    )
    parser.add_argument(
        "--show-rejected",
        action="store_true",
        help="In --discover mode, also print datasets rejected by EEG validation.",
    )
    parser.add_argument(
        "--write-eeg-list",
        type=Path,
        default=None,
        help="Write EEG-validated dataset IDs to a text file after validation.",
    )
    parser.add_argument("--sort", choices=["size", "name"], default=None)
    parser.add_argument("--max-size", type=float, default=None, metavar="GB")
    parser.add_argument("--max-size-mb", type=float, default=None, metavar="MB")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument(
        "--resolve-workers",
        type=int,
        default=6,
        help="Parallel workers for PhysioNet metadata/discovery validation.",
    )
    parser.add_argument(
        "--probe-subdirs",
        type=int,
        default=12,
        help="Number of first-level /files subdirectories to inspect for EEG evidence.",
    )
    parser.add_argument(
        "--checksum",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Validate SHA256SUMS.txt after download when present.",
    )
    parser.add_argument("--quiet", action="store_true")

    parser.add_argument(
        "--username",
        default=None,
        help="PhysioNet username. Overrides PHYSIONET_USERNAME and config file.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=Path(__file__).with_name("config_physionet.json"),
        help="Local JSON credentials file. Default: data/config_physionet.json.",
    )
    parser.add_argument(
        "--password-env",
        default="PHYSIONET_PASSWORD",
        help="Environment variable containing the PhysioNet password.",
    )
    parser.add_argument(
        "--ask-password",
        action="store_true",
        help="Prompt for PhysioNet password instead of reading --password-env.",
    )

    # Accepted for sbatch_download.sh compatibility. Actual preprocessing should
    # be run by a separate, explicit pipeline once dataset layout is confirmed.
    parser.add_argument("--preprocess", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--target-fs", type=int, default=250, help=argparse.SUPPRESS)
    parser.add_argument("--no-align-sfreq", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--standard-channels", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--target-duration", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--length-mode", default="crop", help=argparse.SUPPRESS)
    parser.add_argument("--interpolate-channels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-remove-original", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.max_workers < 1:
        print("[ERROR] --max-workers must be >= 1", file=sys.stderr)
        return 1
    if args.resolve_workers < 1:
        print("[ERROR] --resolve-workers must be >= 1", file=sys.stderr)
        return 1

    if args.preprocess and not args.quiet:
        print(
            "[WARN] --preprocess is accepted for launcher compatibility but "
            "download_PhysioNet.py does not preprocess data.",
            file=sys.stderr,
        )

    try:
        config = load_auth_config(args.config_file)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    username = (
        args.username
        or os.environ.get("PHYSIONET_USERNAME")
        or config.get("username")
    )
    password = (
        os.environ.get(args.password_env)
        if args.password_env
        else None
    ) or config.get("password")
    if args.ask_password and username and not password:
        password = getpass.getpass(f"PhysioNet password for {username}: ")

    session = make_session(username, password)
    try:
        specs = read_dataset_specs(args)
        if args.discover:
            discovery_sources = [("primary", args.discover_url)] if args.discover_primary else []
            if args.discover_challenges:
                discovery_sources.append(("challenges", CHALLENGE_URL))
            topic_urls = list(TOPIC_DISCOVERY_URLS) if args.discover_topics else []
            topic_urls.extend(args.discover_topic_url or [])
            for idx, topic_url in enumerate(topic_urls, 1):
                discovery_sources.append((f"topic-{idx}", topic_url))
            discovery_errors: list[str] = []
            for source_name, source_url in discovery_sources:
                try:
                    discovered = discover_dataset_specs(
                        session,
                        source_url,
                        default_version=args.default_version,
                        workers=args.resolve_workers,
                        limit=args.discover_limit,
                    )
                except RuntimeError as exc:
                    discovery_errors.append(f"{source_name}: {exc}")
                    print(f"[WARN] discovery source failed ({source_name}): {exc}", file=sys.stderr)
                    continue
                added = append_unique_specs(specs, discovered)
                print(
                    f"[DISCOVER] found {len(discovered)} PhysioNet project links "
                    f"from {source_name} ({source_url}); added {added}",
                    file=sys.stderr,
                )
            if args.include_curated_seeds:
                added = append_unique_specs(specs, curated_seed_specs())
                print(f"[DISCOVER] added {added} curated EEG/PSG seed specs", file=sys.stderr)
            if args.include_known_eeg:
                added = append_unique_specs(specs, known_eeg_specs())
                print(f"[DISCOVER] added {added} curated known EEG dataset specs", file=sys.stderr)
            if discovery_errors and not specs:
                raise RuntimeError("; ".join(discovery_errors))
        if not specs:
            raise ValueError("provide --dataset, --url, --datasets-file, or --discover")
    except (ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    infos = resolve_all_datasets(
        specs,
        username=username,
        password=password,
        probe_subdirs=args.probe_subdirs,
        resolve_workers=args.resolve_workers,
    )

    if args.sort == "size":
        infos.sort(key=lambda info: info.size_bytes, reverse=True)
    elif args.sort == "name":
        infos.sort(key=lambda info: info.title.lower())

    if args.discover and not args.show_rejected:
        display_infos = [info for info in infos if info.is_eeg]
    else:
        display_infos = infos
    format_table(display_infos)

    if args.write_eeg_list:
        write_eeg_list(infos, args.write_eeg_list)
        print(f"[INFO] wrote EEG dataset list: {args.write_eeg_list}")

    if args.dry_run:
        print("[DRY-RUN] no files downloaded")
        resolution_errors = [
            info for info in infos
            if info.reject_reason.startswith("content page request failed")
            or info.reject_reason.startswith("content page HTTP")
            or info.reject_reason.startswith(
                "REJECT_NO_EEG_EVIDENCE: files index request failed"
            )
        ]
        if resolution_errors and not args.discover:
            return 1
        if resolution_errors and args.show_rejected:
            print(
                f"[WARN] {len(resolution_errors)} dataset metadata requests failed",
                file=sys.stderr,
            )
        return 0

    return run_downloads(infos, args, username, password)


if __name__ == "__main__":
    sys.exit(main())
