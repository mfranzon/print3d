"""Repo-relative paths for pinned profiles and jobs."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_profiles_dir() -> Path:
    return repo_root() / "profiles" / "x2d-0.4"


def default_jobs_dir() -> Path:
    return repo_root() / "jobs"
