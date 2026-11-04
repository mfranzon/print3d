"""Turn a print prompt into an internal brief. Not a user-facing form."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

Intent = Literal["sign", "utility", "enclosure", "figurine"]

MM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)
_AXIS = r"(\d+(?:\.\d+)?)\s*(?:mm)?"
_BY = r"\s*(?:x|\u00d7|by)\s*"
DIMS_RE = re.compile(rf"{_AXIS}{_BY}{_AXIS}(?:{_BY}{_AXIS})?(?:\s*mm)?", re.IGNORECASE)
SAYS_RE = re.compile(
    r"""(?:says?|text|reading)\s+(?:["']([^"']{1,32})["']|([A-Za-z0-9][A-Za-z0-9._-]{0,32}))""",
    re.I,
)
COLOR_RE = re.compile(r"\b(green|pink|white|yellow|red|blue|black|orange|purple)\b", re.I)
SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class PrintBrief:
    prompt: str
    slug: str
    intent: Intent
    target_size_mm: tuple[float, float, float] | None
    material: str
    color_name: str | None
    text: str | None
    assumptions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify(text: str, limit: int = 30) -> str:
    slug = SLUG_RE.sub("_", text.lower()).strip("_")
    return (slug or "print")[:limit]


def _intent(prompt: str) -> Intent:
    lower = prompt.lower()
    if any(word in lower for word in ("figurine", "statue", "dragon", "scan", "organic")):
        return "figurine"
    if any(word in lower for word in ("nameplate", "sign", "plaque", "says", "label")):
        return "sign"
    if any(word in lower for word in ("enclosure", "case", "box", "housing")):
        return "enclosure"
    return "utility"


def _size(prompt: str, intent: Intent) -> tuple[tuple[float, float, float] | None, list[str]]:
    """The brief's size, plus one assumption note per axis that had to be derived.

    A sign brief may state every axis ("a 40mm x 8mm x 3mm nameplate", "40 x 8 x 3 mm");
    those are used as given. Only axes written as one dimension count, so a letter height
    or a hole size elsewhere in the prompt is never read as depth. An axis the prompt
    leaves out keeps the usual nameplate proportion and is recorded as an assumption, so
    a run never invents a dimension silently.
    """
    matches = list(MM_RE.finditer(prompt))
    if intent == "utility" and "cube" in prompt.lower() and matches:
        side = float(matches[0].group(1))
        return (side, side, side), []
    if not matches:
        return None, []
    first = float(matches[0].group(1))
    if intent == "sign":
        dims = next((m for m in DIMS_RE.finditer(prompt) if "mm" in m.group(0).lower()), None)
        size = [float(v) for v in dims.groups() if v] if dims else [first]
        assumptions: list[str] = []
        if len(size) < 2:
            size.append(round(size[0] * 0.4, 3))
            assumptions.append(
                f"Depth not stated; used 40% of the stated width ({size[1]:g} mm).")
        if len(size) < 3:
            size.append(3.0)
            assumptions.append("Thickness not stated; used 3 mm.")
        return (size[0], size[1], size[2]), assumptions
    return (first, first, first), []


def _text(prompt: str) -> str | None:
    match = SAYS_RE.search(prompt)
    if match:
        return (match.group(1) or match.group(2) or "").strip()
    quoted = re.search(r'["\']([^"\']{1,32})["\']', prompt)
    if quoted:
        return quoted.group(1).strip()
    return None


def parse_brief(prompt: str, *, color: str | None = None) -> PrintBrief:
    text = prompt.strip()
    if not text:
        raise ValueError("Prompt is empty.")
    intent = _intent(text)
    size, size_assumptions = _size(text, intent)
    color_name = color or (COLOR_RE.search(text).group(1).lower() if COLOR_RE.search(text) else None)
    plate_text = _text(text)
    assumptions: list[str] = list(size_assumptions)
    if size is None:
        if intent == "sign":
            size = (40.0, 16.0, 3.0)
            assumptions.append("Default nameplate 40 x 16 x 3 mm.")
        elif intent == "utility" and "cube" in text.lower():
            size = (20.0, 20.0, 20.0)
            assumptions.append("Default calibration cube 20 mm.")
        else:
            assumptions.append("No size given; CAD/mesh must carry dimensions.")
    slug_source = plate_text or text
    return PrintBrief(
        prompt=text,
        slug=slugify(slug_source),
        intent=intent,
        target_size_mm=size,
        material="PLA",
        color_name=color_name,
        text=plate_text,
        assumptions=tuple(assumptions),
    )
