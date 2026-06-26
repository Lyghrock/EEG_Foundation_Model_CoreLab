#!/usr/bin/env python3
"""Path-based subject/session/task inference helpers.

These helpers intentionally keep the original path-derived identifiers. The
goal is transparent grouping for dataset statistics, not irreversible ID
normalization.
"""

from __future__ import annotations

import re
from pathlib import Path


BIDS_SUB_RE = re.compile(r"^sub-([A-Za-z0-9][A-Za-z0-9_.-]*)$", re.IGNORECASE)
BIDS_SES_RE = re.compile(r"^ses-([A-Za-z0-9][A-Za-z0-9_.-]*)$", re.IGNORECASE)
TASK_RE = re.compile(r"(?:^|[_-])task-([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
RUN_RE = re.compile(r"(?:^|[_-])run-([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
GENERIC_SUB_RE = re.compile(
    r"^(?:subject|subj|patient|participant|person|pt|p)[_-]?([A-Za-z0-9][A-Za-z0-9_.-]*)$",
    re.IGNORECASE,
)
GENERIC_SES_RE = re.compile(r"^(?:session|sess|ses|visit)[_-]?([A-Za-z0-9][A-Za-z0-9_.-]*)$", re.IGNORECASE)
TUH_PATIENT_RE = re.compile(r"^s\d{3,}$", re.IGNORECASE)


def _first_match(pattern: re.Pattern[str], parts: list[str]) -> str:
    for part in parts:
        match = pattern.search(part)
        if match:
            return match.group(1)
    return ""


def infer_from_relative_path(relative_path: str | Path) -> dict[str, str]:
    path = Path(relative_path)
    parts = [p for p in path.parts if p not in {"", "."}]
    stem = path.stem
    search_parts = parts + [stem]

    subject_id = ""
    session_id = ""
    task_id = ""
    run_id = ""

    for part in search_parts:
        match = BIDS_SUB_RE.match(part)
        if match:
            subject_id = f"sub-{match.group(1)}"
            break
    if not subject_id:
        for part in search_parts:
            match = GENERIC_SUB_RE.match(part)
            if match:
                subject_id = part
                break
    if not subject_id:
        for part in search_parts:
            if TUH_PATIENT_RE.match(part):
                subject_id = part
                break

    for part in search_parts:
        match = BIDS_SES_RE.match(part)
        if match:
            session_id = f"ses-{match.group(1)}"
            break
    if not session_id:
        for part in search_parts:
            match = GENERIC_SES_RE.match(part)
            if match:
                session_id = part
                break

    task = _first_match(TASK_RE, search_parts)
    if task:
        task_id = f"task-{task}"

    run = _first_match(RUN_RE, search_parts)
    if run:
        run_id = f"run-{run}"

    return {
        "inferred_subject_id": subject_id,
        "inferred_session_id": session_id,
        "inferred_task_id": task_id,
        "inferred_run_id": run_id,
    }


def top_level_subset(relative_path: str | Path) -> str:
    parts = [p for p in Path(relative_path).parts if p not in {"", "."}]
    return parts[0] if parts else ""
