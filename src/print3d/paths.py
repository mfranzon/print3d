"""Where the pinned profiles, the skill and the jobs live."""

from __future__ import annotations

import os
import re
from pathlib import Path

from print3d.printers import PROFILES, active_printer

PACKAGE = Path(__file__).resolve().parent
JOBS_ENV = "PRINT3D_JOBS"
NAME_RE = re.compile(r"[a-z0-9_-]{1,64}")


def repo_root() -> Path:
    return PACKAGE.parents[1]


def default_profiles_dir() -> Path:
    """The pinned presets of the printer print3d slices for (see printers.py)."""
    return PROFILES / active_printer()


def coupon_script() -> Path:
    return PACKAGE / "coupons" / "fit_coupon.py"


def skill_dir() -> Path:
    # The plugin uses a real directory; setuptools ships it inside the wheel.
    source = repo_root() / "skills" / "print3d"
    if (repo_root() / "pyproject.toml").is_file() and source.is_dir():
        return source
    return PACKAGE / "skill"


def default_jobs_dir() -> Path:
    """`$PRINT3D_JOBS`, else `jobs/` in a source checkout, else `~/print3d/jobs`."""
    if env := os.environ.get(JOBS_ENV, "").strip():
        return Path(env).expanduser()
    if (repo_root() / "pyproject.toml").is_file():
        return repo_root() / "jobs"
    return Path.home() / "print3d" / "jobs"


def safe_name(name: str, what: str = "name") -> str:
    """A job slug or view name, kept to the alphabet slugify writes so it can't leave the jobs folder."""
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"{what} {name!r}: use 1 to 64 of a-z, 0-9, _ and -")
    return name


def job_path(slug: str) -> Path:
    """jobs/<slug>, refused if it would land outside the jobs folder."""
    root = default_jobs_dir().resolve()
    path = (root / safe_name(slug, "slug")).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"slug {slug!r} leaves the jobs folder")
    return path
