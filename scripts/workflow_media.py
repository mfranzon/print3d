"""Render the photo-to-print workflow as stills, an MP4 and a GIF, from real runs.

Replays the peg benchmark (tests/fixtures/peg.*) through the actual pipeline,
renders a turntable of the resulting mesh in Blender, and lays each step out as
a 1280x720 frame. Needs Blender, Bambu Studio and ffmpeg.

    .venv/bin/python scripts/workflow_media.py        # writes docs/workflow*
"""

from __future__ import annotations

import json
import shutil
import textwrap
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from print3d.blender import blender_arg, find_blender  # noqa: E402
from print3d.photo import grid, open_upright  # noqa: E402
from print3d.pipeline import run_job  # noqa: E402
from test_peg_benchmark import BENCH, CATALOGUE, build_spec  # noqa: E402

W, H = 1280, 720
HEAD = 132
GREEN = (0, 174, 66)
INK = (26, 26, 26)
GREY = (110, 110, 110)
RED = (220, 50, 50)
BLUE = (40, 90, 230)
YELLOW = (255, 214, 0)
FPS = 24
HOLD_S = 3.2
FADE_S = 0.4
PROMPT = "a dolly peg"
SLUG = "a_dolly_peg"
PHOTO = ROOT / "tests" / "fixtures" / "peg.jpg"
CREDIT = "Photo: Auckland Museum, CC BY 4.0"
OUT = ROOT / "docs"

TURNTABLE = r'''
import bpy, math, sys
from mathutils import Vector
argv = sys.argv[sys.argv.index("--") + 1:]
stl, out_dir, frames, reach, rise = argv[0], argv[1], int(argv[2]), float(argv[3]), float(argv[4])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=stl)
part = bpy.context.selected_objects[0]
part.data.materials.append(bpy.data.materials.new("pla"))
part.active_material.diffuse_color = (0.0, 0.68, 0.26, 1.0)
corners = [part.matrix_world @ Vector(c) for c in part.bound_box]
centre = sum(corners, Vector()) / 8
size = max((max(c[i] for c in corners) - min(c[i] for c in corners)) for i in range(3))
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_cavity = True
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = 600, 720
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 70
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
target = bpy.data.objects.new("target", None)
scene.collection.objects.link(target)
target.location = centre
track = cam.constraints.new("TRACK_TO")
track.target = target
distance = reach * size
for i in range(frames):
    a = 2 * math.pi * i / frames + math.radians(-35)
    cam.location = centre + Vector((distance * math.cos(a), distance * math.sin(a), rise * distance))
    scene.render.filepath = f"{out_dir}/turn_{i:03d}.png"
    bpy.ops.render.render(write_still=True)
'''


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = "/System/Library/Fonts/Menlo.ttc" if mono else "/System/Library/Fonts/Helvetica.ttc"
    try:
        return ImageFont.truetype(path, size, index=1 if bold and not mono else 0)
    except OSError:
        return ImageFont.load_default(size=size)


def fit(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    scale = min(box[0] / image.width, box[1] / image.height)
    return image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)


def frame(step: int, total: int, title: str, subtitle: str, credit: str | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([48, 40, 128, 80], radius=20, fill=GREEN)
    draw.text((88, 60), f"{step}/{total}", font=font(22, bold=True), fill="white", anchor="mm")
    draw.text((148, 60), title, font=font(38, bold=True), fill=INK, anchor="lm")
    draw.text((148, 100), subtitle, font=font(22), fill=GREY, anchor="lm")
    if credit:
        draw.text((48, H - 24), credit, font=font(15), fill=GREY, anchor="lm")
    draw.text((W - 40, H - 24), "print3d", font=font(18, bold=True), fill=GREEN, anchor="rm")
    return canvas, draw


def paste(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    fitted = fit(image, (x1 - x0, y1 - y0))
    ox, oy = x0 + (x1 - x0 - fitted.width) // 2, y0 + (y1 - y0 - fitted.height) // 2
    canvas.paste(fitted, (ox, oy), fitted if fitted.mode == "RGBA" else None)


def arrow(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    draw.line([(x - 22, y), (x + 18, y)], fill=GREEN, width=6)
    draw.polygon([(x + 28, y), (x + 12, y - 12), (x + 12, y + 12)], fill=GREEN)


def dimension(draw, a, b, label, colour, fnt, offset=(0, -10)):
    draw.line([a, b], fill=colour, width=3)
    for x, y in (a, b):
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=colour)
    mid = ((a[0] + b[0]) / 2 + offset[0], (a[1] + b[1]) / 2 + offset[1])
    draw.text(mid, label, font=fnt, fill=colour, anchor="mb", stroke_width=3, stroke_fill="white")


def run(work: Path) -> tuple[dict, Path]:
    job = work / "jobs" / SLUG
    build_spec(job)
    report = run_job(PROMPT, script=ROOT / "examples" / "dolly_peg.py", jobs_dir=work / "jobs", execute=True)
    if report["severity"] == "error":
        raise SystemExit(f"The benchmark run is red: {report['advice']}")
    return report, job


def turntable(mesh: Path, out: Path, frames: int, reach: float = 2.3, rise: float = 0.35) -> list[Path]:
    """Orbit the mesh: the camera sits `reach` times its size away and `rise` times that up."""
    out.mkdir(parents=True, exist_ok=True)
    script = out / "turntable.py"
    script.write_text(TURNTABLE, encoding="utf-8")
    binary = find_blender()
    subprocess.run(
        [str(binary), "--background", "--factory-startup", "--python", blender_arg(binary, script),
         "--", blender_arg(binary, mesh), blender_arg(binary, out), str(frames), str(reach), str(rise)],
        check=True, capture_output=True,
    )
    return sorted(out.glob("turn_*.png"))


def build_frames(report: dict, job: Path, turn: list[Path], work: Path) -> list[list[Image.Image]]:
    """Each step as a list of images: one for a still, many for an animated step."""
    total = 8
    photo = open_upright(PHOTO)
    dims = json.loads((job / "spec.json").read_text())["dims"]
    body = (48, HEAD + 16, W - 48, H - 56)
    steps: list[list[Image.Image]] = []

    # 1. The photo.
    canvas, _ = frame(1, total, "A photo of the object", "A museum photo of a wooden peg, taken square-on beside a ruler.", credit=CREDIT)
    paste(canvas, photo, body)
    steps.append([canvas])

    # 2. Grid.
    crop = (925, 375, 975, 425)
    grid_png = Path(grid(PHOTO, work / "grid.png", crop=crop, step=5)["grid"])
    canvas, draw = frame(2, total, "Read pixels off a grid", "print3d grid --crop zooms in; labels are photo pixels, so points are read, not guessed.", credit=CREDIT)
    marked = photo.copy()
    ImageDraw.Draw(marked).rectangle(crop, outline=RED, width=4)
    paste(canvas, marked, (48, HEAD + 40, 640, H - 80))
    arrow(draw, 680, (HEAD + H) // 2)
    paste(canvas, open_upright(grid_png), (730, HEAD + 16, W - 48, H - 56))
    steps.append([canvas])

    # 3. Scale.
    (x0, y0), (x1, y1) = BENCH["ruler"]["points"]
    canvas, _ = frame(3, total, "Scale it from the ruler", "The 6 cm and 20 cm ticks: 140 mm across 905 px. The object never sets its own scale.", credit=CREDIT)
    ruled = photo.copy()
    d = ImageDraw.Draw(ruled)
    d.line([(x0, y0 - 18), (x1, y1 - 18)], fill=RED, width=5)
    for x in (x0, x1):
        d.line([(x, y0 - 34), (x, y0 + 4)], fill=RED, width=5)
    d.text(((x0 + x1) / 2, y0 - 26), "140 mm = 905 px", font=font(30, bold=True), fill=RED, anchor="mb",
           stroke_width=4, stroke_fill="white")
    paste(canvas, ruled, body)
    steps.append([canvas])

    # 4. Measure.
    canvas, draw = frame(4, total, "Measure, with evidence", "Each number keeps its pixels, its reference and a tolerance. What the photo cannot show is labelled.", credit=CREDIT)
    ann = photo.crop((95, 85, 905, 330))
    d = ImageDraw.Draw(ann)
    m = BENCH["measured"]
    f = font(24, bold=True)
    # Horizontal spans are drawn clear of the peg; only their x positions are measured.
    (lx0, _), (lx1, _) = m["length"]
    (sx0, _), (sx1, _) = m["slot_length"]
    dimension(d, (lx0 - 95, 190), (lx1 - 95, 190), f"length {dims['length']['mm']:.1f}", RED, f, (0, 32))
    dimension(d, (sx0 - 95, 35), (sx1 - 95, 35), f"slot {dims['slot_length']['mm']:.1f}", BLUE, f, (0, -6))
    (cx, cy0), (_, cy1) = m["spring_diameter"]
    dimension(d, (cx - 95, cy0 - 85), (cx - 95, cy1 - 85), f"{dims['spring_diameter']['mm']:.1f}", YELLOW, f, (-52, 8))
    (kx, ky0), (_, ky1) = m["knob_diameter"]
    dimension(d, (kx - 95, ky0 - 85), (kx - 95, ky1 - 85), f"{dims['knob_diameter']['mm']:.1f}", YELLOW, f, (-40, 8))
    paste(canvas, ann, (48, HEAD + 20, 700, 420))
    mono = font(17, mono=True)
    rows = [("dimension", "mm", "source")] + [
        (name, f"{v['mm']:.1f}" + (f" ±{v['tolerance_mm']:.1f}" if "tolerance_mm" in v else ""), v["source"])
        for name, v in dims.items()
    ]
    y = HEAD + 30
    for i, (name, mm, source) in enumerate(rows):
        colour = GREY if i == 0 else {"measured": GREEN, "user": INK, "assumed": RED}.get(source, INK)
        draw.text((740, y), name, font=mono, fill=INK if i else GREY)
        draw.text((930, y), mm, font=mono, fill=INK if i else GREY)
        draw.text((1090, y), source, font=mono, fill=colour)
        y += 34 if i == 0 else 30
    unknowns = json.loads((job / "spec.json").read_text())["unknowns"]
    y = 450
    draw.text((48, y), "unknowns", font=font(18, bold=True), fill=GREY)
    for line in unknowns:
        for k, part in enumerate(textwrap.wrap(line, 62)):
            y += 28
            draw.text((48, y), ("· " if k == 0 else "  ") + part, font=font(18), fill=INK)
    steps.append([canvas])

    # 5. Outline.
    canvas, _ = frame(5, total, "Trace the outline", "measure outline: the shape the model has to match, in photo pixels.", credit=CREDIT)
    traced = photo.copy()
    d = ImageDraw.Draw(traced)
    pts = [tuple(p) for p in BENCH["outline"]]
    d.line(pts + [pts[0]], fill=YELLOW, width=4)
    for x, y in pts:
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=RED)
    paste(canvas, traced, body)
    steps.append([canvas])

    # 6. Model (animated).
    code = [
        'd = params["dims"]',
        "# the turned profile, knob up:",
        'knob = d["knob_diameter"] / 2',
        'neck = d["neck_diameter"] / 2',
        'body = d["body_diameter"] / 2',
        "# spin it, then cut the slot:",
        'slot = d["slot_length"], d["slot_width"]',
    ]
    animated = []
    for path in turn:
        canvas, draw = frame(6, total, "Model it from the spec", "A Blender script reads params[\"dims\"]: no number of its own.")
        y = HEAD + 90
        for line in code:
            draw.text((60, y), line, font=font(19, mono=True), fill=GREY if line.startswith("#") else INK)
            y += 36
        paste(canvas, open_upright(path), (640, HEAD + 6, W - 48, H - 40))
        animated.append(canvas)
    steps.append(animated)

    # 7. Likeness.
    [like] = report["likeness"]
    canvas, draw = frame(7, total, "Check it matches the photo", "The model is projected sideways and laid over the photo. A mismatch fails the run.", credit=CREDIT)
    paste(canvas, open_upright(Path(like["overlay"])), (48, HEAD + 16, 720, H - 56))
    draw.text((780, 290), f"{like['iou']:.0%}", font=font(120, bold=True), fill=GREEN, anchor="lm")
    draw.text((784, 380), "overlap with the photo", font=font(26), fill=INK, anchor="lm")
    draw.text((784, 430), f"photo {like['photo_mm'][0]:.1f} x {like['photo_mm'][1]:.1f} mm", font=font(22), fill=GREY, anchor="lm")
    draw.text((784, 464), f"model {like['model_mm'][0]:.1f} x {like['model_mm'][1]:.1f} mm", font=font(22), fill=GREY, anchor="lm")
    draw.text((784, 498), f"photo levelled by {like['tilt_deg']:+.1f}°", font=font(22), fill=GREY, anchor="lm")
    steps.append([canvas])

    # 8. Slice.
    m = report["slice_metrics"]
    canvas, draw = frame(8, total, "Sliced for the Bambu X2D", "Printed standing on its knob. Open the .3mf in Bambu Studio and print.")
    paste(canvas, open_upright(Path(report["plate_png"])), (48, HEAD + 16, 640, H - 56))
    stats = [(f"{m['layer_count']}", "layers"), (m["estimated_time"], "print time"), (f"{m['filament_g']:.1f} g", "PLA")]
    y = 230
    for value, label in stats:
        draw.text((700, y), value, font=font(64, bold=True), fill=INK, anchor="ls")
        draw.text((704, y + 32), label, font=font(22), fill=GREY, anchor="ls")
        y += 120
    slender = next((a for a in report["advice"] if a["code"] == "slender"), None)
    if slender:
        draw.text((700, H - 120), "warn: tall and slender, so add a brim in Studio", font=font(20), fill=RED)
    draw.text((700, H - 86), f"jobs/{SLUG}/slice/{SLUG}.3mf", font=font(18, mono=True), fill=GREEN)
    steps.append([canvas])
    return steps


def results_card(dims: dict) -> Image.Image:
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((W // 2, 110), "Photo in, printable part out", font=font(54, bold=True), fill=INK, anchor="mm")
    draw.text((W // 2, 170), "Scored against the museum's catalogue, which rounds to 5 mm", font=font(26), fill=GREY, anchor="mm")
    length, spring, slot = dims["length"], dims["spring_diameter"], dims["slot_length"]
    rows = [
        ("length", f"{length['mm']:.1f} ± {length['tolerance_mm']:.1f}", f"{CATALOGUE['length']} mm"),
        ("diameter, at the spring", f"{spring['mm']:.1f} ± {spring['tolerance_mm']:.1f}", f"{CATALOGUE['diameter']} mm"),
        ("slot", f"{slot['mm'] / length['mm']:.0%} of the length", "about half"),
    ]
    head = font(24, bold=True)
    for x, label in ((220, ""), (620, "from the photo, mm"), (960, "catalogue")):
        draw.text((x, 260), label, font=head, fill=GREY, anchor="lm")
    y = 320
    for name, got, truth in rows:
        draw.line([(200, y - 30), (1100, y - 30)], fill=(230, 230, 230), width=2)
        draw.text((220, y), name, font=font(30), fill=INK, anchor="lm")
        draw.text((620, y), got, font=font(30, bold=True), fill=GREEN, anchor="lm")
        draw.text((960, y), truth, font=font(30), fill=INK, anchor="lm")
        y += 70
    draw.text((W // 2, H - 110), "github.com/mfranzon/print3d", font=font(26, bold=True), fill=GREEN, anchor="mm")
    draw.text((W // 2, H - 60), CREDIT, font=font(16), fill=GREY, anchor="mm")
    return canvas


def encode(steps: list[list[Image.Image]], work: Path, name: str = "workflow") -> None:
    seq = work / "seq"
    seq.mkdir()
    hold, fade = round(HOLD_S * FPS), round(FADE_S * FPS)
    images: list[Image.Image] = []
    for step in steps:
        # An animated step loops its frames for the hold; a still repeats.
        run_ = [step[j % len(step)] for j in range(max(hold, len(step) * 2))] if len(step) > 1 else [step[0]] * hold
        if images:
            prev = images[-1]
            images += [Image.blend(prev, run_[0], (k + 1) / (fade + 1)) for k in range(fade)]
        images += run_
    images += [images[-1]] * FPS
    for n, image in enumerate(images):
        image.save(seq / f"{n:05d}.png")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit(f"ffmpeg not found; stills are in docs/{name}/")
    pattern = str(seq / "%05d.png")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(OUT / f"{name}.mp4")], check=True)
    palette = work / "palette.png"
    scale = "fps=12,scale=800:-1:flags=lanczos"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern,
                    "-vf", f"{scale},palettegen=stats_mode=diff", str(palette)], check=True)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern, "-i", str(palette),
                    "-lavfi", f"{scale} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                    str(OUT / f"{name}.gif")], check=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        report, job = run(work)
        turn = turntable(Path(report["model"]["mesh"]), work / "turn", frames=36)
        steps = build_frames(report, job, turn, work)
        dims = json.loads((job / "spec.json").read_text())["dims"]
        steps.append([results_card(dims)])

        stills = OUT / "workflow"
        stills.mkdir(parents=True, exist_ok=True)
        names = ["photo", "grid", "scale", "measure", "outline", "model", "likeness", "slice", "results"]
        for n, (name, step) in enumerate(zip(names, steps), start=1):
            step[len(step) // 3].save(stills / f"{n:02d}-{name}.png", optimize=True)
        encode(steps, work)
    for path in [OUT / "workflow.gif", OUT / "workflow.mp4", *sorted((OUT / "workflow").glob("*.png"))]:
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
