"""Every module must parse on the Python floor ``pyproject.toml`` declares.

``requires-python`` is ``>=3.11``, and ``uvx --from <repo> print3d mcp`` (the plugin's
``mcp.json``) runs on whichever interpreter ``uv`` resolves — which is not necessarily
3.12+. A PEP 701 nested f-string (3.12+) parses fine on the author's machine and then
kills the CLI and the MCP server at import on 3.11:

    File ".../print3d/handoff.py", line 249
      f'... {_img(p.get("png"), f"plate {p["plate"]}")} ...'
    SyntaxError: f-string: f-string: unmatched '['

That reads to the user as ``MCP server print3d not connected: Connection closed`` with
zero tools registered, so the guard is cheap insurance.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_every_module_parses_on_the_declared_python_floor():
    failures: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                      feature_version=(3, 11))
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(SRC)}:{exc.lineno}: {exc.msg}")
    assert not failures, "modules that need Python 3.12+: " + "; ".join(failures)
