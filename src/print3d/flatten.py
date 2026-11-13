"""Flatten Bambu Lab profile inheritance for the Studio CLI.

Studio's GUI resolves `inherits` and `include` at runtime. The CLI does not.
`--load-settings` / `--load-filaments` need a full config (`from` = User).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Literal

from print3d.printers import DEFAULT_BED_TYPE, DEFAULT_PRINTER, PRESETS

ProfileKind = Literal["machine", "process", "filament"]
META_SKIP = {"inherits", "include", "instantiation", "setting_id", "is_custom_defined"}
NOZZLE_SUFFIX_RE = re.compile(r"\s+\d+\.\d+\s+nozzle.*$")

# The X2D's presets, the defaults of flatten_presets (and of flatten_x2d before it).
DEFAULT_MACHINE = PRESETS[DEFAULT_PRINTER].machine
DEFAULT_PROCESS = PRESETS[DEFAULT_PRINTER].process
DEFAULT_FILAMENT = PRESETS[DEFAULT_PRINTER].filament


class FlattenError(RuntimeError):
    """Raised when a profile tree cannot be flattened."""


def detect_profiles_root(slicer_path: str | None = None, vendor: str = "BBL") -> Path:
    """The system profiles that hold `vendor`'s tree: BBL in Bambu Studio's, Elegoo in ElegooSlicer's, OrcaSlicer in its own."""
    if vendor == "OrcaSlicer":
        candidates = [
            Path(p).expanduser()
            for p in os.environ.get("ORCA_PROFILES_DIR", "").split(os.pathsep)
            if p.strip()
        ]
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", "").strip() or Path.home() / ".config")
        candidates.extend(
            [
                config_home / "OrcaSlicer" / "system",
                config_home / "OrcaSlicer" / "user",
                Path("/usr/share/OrcaSlicer/resources/profiles"),
            ]
        )
        if slicer_path:
            slicer = Path(slicer_path).resolve()
            candidates.extend(
                [
                    slicer.parent / "resources" / "profiles",
                    slicer.parent / "resources" / "profiles" / "system",
                ]
            )
        # WSL: the Windows side's OrcaSlicer profiles (resources tree or APPDATA).
        candidates.extend(
            win_home / "resources" / "profiles"
            for win_home in [Path("/mnt/c/Program Files/OrcaSlicer")]
            if win_home.exists()
        )
        candidates.extend(
            win_user / rel
            for win_user in Path("/mnt/c/Users").glob("*/AppData/Roaming/OrcaSlicer")
            for rel in (Path("system"), Path("user"))
        )
        for candidate in candidates:
            if (
                (candidate / "machine").is_dir()
                or (candidate / "OrcaSlicer" / "machine").is_dir()
                or any(candidate.glob("*/machine"))
                or any(candidate.glob("*/filament"))
            ):
                return candidate
        raise FlattenError(
            "Could not find the OrcaSlicer profiles. Install OrcaSlicer or set ORCA_PROFILES_DIR."
        )
    elegoo = vendor == "Elegoo"
    candidates: list[Path] = []
    env_root = os.environ.get("ELEGOO_PROFILES_ROOT" if elegoo else "BAMBU_PROFILES_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())
    app = "ElegooSlicer" if elegoo else "BambuStudio"
    candidates.append(Path.home() / "Library/Application Support" / app / "system")
    # Linux: the slicer keeps its system profiles in the user's config directory.
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", "").strip() or Path.home() / ".config")
    candidates.append(config_home / app / "system")
    if slicer_path:
        slicer = Path(slicer_path).resolve()
        # Windows: <install>/resources/profiles; macOS: <app>/Contents/Resources/profiles.
        for relative in ("resources", "Resources"):
            candidates.append(slicer.parent / relative / "profiles")
        candidates.append(slicer.parent.parent / "Resources" / "profiles")
    candidates.append(Path("/Applications") / f"{app}.app" / "Contents/Resources/profiles")
    from print3d.windows import program_files_dirs

    installs = (
        ("ElegooSlicer/resources/profiles", "ElegooSlicer/Resources/profiles")
        if elegoo
        else ("Bambu Studio/Resources/profiles",)
    )
    for root in program_files_dirs():
        candidates.extend(root / relative for relative in installs)
    for candidate in candidates:
        if (candidate / vendor / "machine").is_dir():
            return candidate
    raise FlattenError(
        f"Could not find the {vendor} profiles. Install "
        + ("ElegooSlicer or set ELEGOO_PROFILES_ROOT." if elegoo else "Bambu Studio or set BAMBU_PROFILES_ROOT.")
    )


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FlattenError(f"Profile is not an object: {path}")
    return data


def build_name_index(profiles_root: Path, vendor: str = "BBL") -> dict[str, dict[str, Any]]:
    if vendor == "OrcaSlicer":
        # OrcaSlicer's tree nests per vendor/printer; index every JSON preset by
        # its file name so `inherits` can resolve within the same tree.
        index: dict[str, dict[str, Any]] = {}
        for path in sorted(profiles_root.rglob("*.json")):
            if not any(kind in path.parts for kind in ("machine", "process", "filament")):
                continue
            index.setdefault(path.stem, load_json(path))
        return index
    index: dict[str, dict[str, Any]] = {}
    for kind in ("machine", "process", "filament"):
        directory = profiles_root / vendor / kind
        if not directory.is_dir():
            continue
        # ElegooSlicer nests filament profiles per printer (filament/ECC/...),
        # so walk the whole subtree.
        for path in sorted(directory.rglob("*.json")):
            try:
                data = load_json(path)
            except (OSError, json.JSONDecodeError, FlattenError):
                continue
            name = data.get("name")
            if isinstance(name, str) and name and name not in index:
                index[name] = data
    return index


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        dst[key] = value


def flatten_by_name(leaf_name: str, index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    cursor: str | None = leaf_name
    while cursor:
        if cursor in visited:
            raise FlattenError(
                f"Profile inheritance cycle at {cursor!r} (chain: {' -> '.join(visited)})"
            )
        visited.add(cursor)
        entry = index.get(cursor)
        if entry is None:
            raise FlattenError(
                f"Profile {cursor!r} not found. Chain: {' -> '.join(visited)}"
            )
        chain.append(entry)
        parent = entry.get("inherits")
        cursor = parent if isinstance(parent, str) and parent else None

    merged: dict[str, Any] = {}
    for node in reversed(chain):
        _merge(merged, node)
        includes = node.get("include")
        if isinstance(includes, list):
            for include_name in includes:
                if not isinstance(include_name, str) or not include_name:
                    continue
                included = index.get(include_name)
                if included is None:
                    raise FlattenError(
                        f"Included profile {include_name!r} not found (from {node.get('name')!r})"
                    )
                _merge(merged, included)
    return merged


def infer_extruder_count(flat: dict[str, Any]) -> int:
    for key in ("nozzle_diameter", "extruder_type", "extruder_variant_list"):
        value = flat.get(key)
        if isinstance(value, list) and value:
            return len(value)
    return 1


def derive_nozzle_volume_type(
    flat: dict[str, Any], override: str | None = None
) -> None:
    count = infer_extruder_count(flat)
    if override:
        flat["nozzle_volume_type"] = [override] * count
        return
    existing = flat.get("nozzle_volume_type")
    if isinstance(existing, list) and existing:
        return
    default = flat.get("default_nozzle_volume_type")
    if isinstance(default, list) and default:
        flat["nozzle_volume_type"] = list(default)
        return
    flat["nozzle_volume_type"] = ["Standard"] * count


def apply_cli_overlay(flat: dict[str, Any], profiles_root: Path, vendor: str = "BBL") -> bool:
    path = profiles_root / vendor / "cli_config.json"
    if not path.is_file():
        return False
    try:
        cli_config = load_json(path)
    except (OSError, json.JSONDecodeError, FlattenError):
        return False
    printers = cli_config.get("printer")
    if not isinstance(printers, dict):
        return False
    name = flat.get("name")
    printer_model = flat.get("printer_model")
    settings_id = flat.get("printer_settings_id")
    candidates: list[str] = []
    for raw in (printer_model, settings_id, name):
        if isinstance(raw, str) and raw:
            candidates.append(raw)
            stripped = NOZZLE_SUFFIX_RE.sub("", raw)
            if stripped != raw:
                candidates.append(stripped)
    seen: set[str] = set()
    for key in candidates:
        if key in seen:
            continue
        seen.add(key)
        block = printers.get(key)
        if not isinstance(block, dict):
            continue
        limits = block.get("machine_limits")
        if isinstance(limits, dict):
            _merge(flat, limits)
            return True
    return False


def apply_machine_model_bed_metadata(
    machine_flat: dict[str, Any], index: dict[str, dict[str, Any]]
) -> None:
    model_name = machine_flat.get("printer_model")
    if not isinstance(model_name, str):
        return
    model = index.get(model_name)
    if not model:
        return
    for key in ("default_bed_type", "image_bed_type", "not_support_bed_type"):
        if key not in machine_flat and key in model:
            machine_flat[key] = model[key]


def ensure_machine_in_compat(flat: dict[str, Any], machine_leaf: str) -> None:
    printers = flat.get("compatible_printers")
    if not isinstance(printers, list):
        flat["compatible_printers"] = [machine_leaf]
        return
    if machine_leaf not in printers:
        printers.append(machine_leaf)


def normalize_for_cli(flat: dict[str, Any], kind: ProfileKind, leaf_name: str) -> None:
    if kind == "machine":
        flat["printer_settings_id"] = leaf_name
    elif kind == "process":
        flat["print_settings_id"] = leaf_name
    else:
        flat["filament_settings_id"] = [leaf_name]
    for key in ("instantiation", "setting_id", "is_custom_defined", "include"):
        flat.pop(key, None)
    flat["inherits"] = leaf_name
    flat["from"] = "User"
    flat["name"] = leaf_name
    flat["type"] = kind


def _home_relative(path: Path) -> str:
    """Keep the user's home directory out of committed provenance."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def write_profile(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def vendor_version(profiles_root: Path, vendor: str = "BBL") -> str | None:
    """The version of Studio's Bambu Lab profile set, from BBL.json."""
    try:
        version = load_json(profiles_root / f"{vendor}.json").get("version")
    except (OSError, json.JSONDecodeError, FlattenError):
        return None
    return str(version) if version else None


def flatten_presets(
    *,
    profiles_root: Path | None = None,
    output_dir: Path,
    machine_leaf: str = DEFAULT_MACHINE,
    process_leaf: str = DEFAULT_PROCESS,
    filament_leaves: Iterable[str] | None = None,
    bed_type: str = DEFAULT_BED_TYPE,
    nozzle_volume_type: str | None = None,
    slicer_path: str | None = None,
    vendor: str = "BBL",
) -> dict[str, Any]:
    root = profiles_root or detect_profiles_root(slicer_path, vendor)
    filaments = list(filament_leaves or ([DEFAULT_FILAMENT] if vendor != "OrcaSlicer" else []))
    if vendor == "OrcaSlicer" and not (machine_leaf and process_leaf and all(filaments)):
        raise FlattenError(
            "OrcaSlicer names its profiles per printer. Set PRINT3D_ORCA_MACHINE, "
            "PRINT3D_ORCA_PROCESS and PRINT3D_ORCA_FILAMENT for your printer, or add "
            "a PRESETS entry whose names exist in the OrcaSlicer profile tree."
        )
    index = build_name_index(root, vendor)
    machine = flatten_by_name(machine_leaf, index)
    process = flatten_by_name(process_leaf, index)
    filament_profiles = [flatten_by_name(name, index) for name in filaments]

    derive_nozzle_volume_type(machine, nozzle_volume_type)
    apply_machine_model_bed_metadata(machine, index)
    overlay = apply_cli_overlay(machine, root, vendor)

    normalize_for_cli(machine, "machine", machine_leaf)
    normalize_for_cli(process, "process", process_leaf)
    if vendor == "OrcaSlicer":
        machine["print3d_slicer"] = "orca"
    for name, filament in zip(filaments, filament_profiles, strict=True):
        normalize_for_cli(filament, "filament", name)
        ensure_machine_in_compat(filament, machine_leaf)
    ensure_machine_in_compat(process, machine_leaf)
    process["curr_bed_type"] = bed_type

    output_dir.mkdir(parents=True, exist_ok=True)
    machine_path = write_profile(output_dir / "machine.json", machine)
    process_path = write_profile(output_dir / "process.json", process)
    filament_paths: list[Path] = []
    for index_, filament in enumerate(filament_profiles):
        name = "filament.json" if len(filament_profiles) == 1 else f"filament-{index_}.json"
        filament_paths.append(write_profile(output_dir / name, filament))

    provenance = {
        "profiles_root": _home_relative(root),
        "profiles_version": vendor_version(root, vendor),
        "machine": machine_leaf,
        "process": process_leaf,
        "filaments": filaments,
        "bed_type": bed_type,
        "cli_overlay_applied": overlay,
        "machine_keys": len(machine),
        "process_keys": len(process),
        "filament_keys": [len(item) for item in filament_profiles],
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "machine_path": str(machine_path),
        "process_path": str(process_path),
        "filament_paths": [str(path) for path in filament_paths],
        "provenance": provenance,
    }


# Kept for callers from when the X2D was the only printer: the defaults are its presets.
flatten_x2d = flatten_presets


def flatten_printer(
    printer: str,
    *,
    output_dir: Path,
    profiles_root: Path | None = None,
    slicer_path: str | None = None,
) -> dict[str, Any]:
    """Flatten the slicer presets print3d pins for `printer` (a key of printers.PRESETS)."""
    presets = PRESETS.get(printer)
    if presets is None:
        raise FlattenError(
            f"print3d does not know the presets for {printer!r}. Known: {', '.join(sorted(PRESETS))}."
        )
    return flatten_presets(
        profiles_root=profiles_root,
        output_dir=output_dir,
        machine_leaf=presets.machine,
        process_leaf=presets.process,
        filament_leaves=[presets.filament],
        bed_type=presets.bed_type,
        slicer_path=slicer_path,
        vendor=presets.vendor,
    )
