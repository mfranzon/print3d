"""Turn a print prompt into an internal brief. Not a user-facing form."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

Intent = Literal["sign", "utility", "enclosure", "figurine"]

MM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)
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


def _size(prompt: str, intent: Intent) -> tuple[float, float, float] | None:
    matches = list(MM_RE.finditer(prompt))
    if intent == "utility" and "cube" in prompt.lower() and matches:
        side = float(matches[0].group(1))
        return (side, side, side)
    if matches:
        first = float(matches[0].group(1))
        if intent == "sign":
            return (first, first * 0.4, 3.0)
        return (first, first, first)
    return None


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
    size = _size(text, intent)
    color_name = color or (COLOR_RE.search(text).group(1).lower() if COLOR_RE.search(text) else None)
    plate_text = _text(text)
    assumptions: list[str] = []
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
