"""Generate real PNG figures for EVERY project, with a managed naming
convention, a centered title baked into each image, and controlled canvas size.

We deliberately do NOT render full analytic pixels — a simple, deterministic
sketch is enough. What matters for the pipeline is that these are *real* images
with (a) a stable managed name, (b) a centered title, and (c) a known
size/position, so the template/draft/final-output can track and inspect them.

Data-driven: for each project it reads the EXISTING corpus/graphics.json (the
list of graphic_id/name/caption/source_doc/belongs_in_section), derives a title
from the caption, picks a sketch style, and rewrites graphics.json enriched with
title/file/width/height/align. One managed pipeline for all projects.

Run:  python backend/build_project_graphics.py
Writes: sample_docs/project/<id>/corpus/figures/<name>.png and updates
        sample_docs/project/<id>/corpus/graphics.json
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCEN = REPO / "sample_docs" / "project"

# Standard managed geometry for every project figure (one place defines it).
FIG_WIDTH = 640
FIG_HEIGHT = 360
FIG_ALIGN = "center"

# Pick a deterministic sketch style from keywords in the name/caption so figures
# aren't all identical. Purely cosmetic; the pipeline cares about title/size.
_STYLE_KEYWORDS = [
    ("heatmap", ("thermal", "heat", "temperature", "micrograph", "cross_section", "cross-section", "wear")),
    ("topology", ("topology", "diagram", "schematic", "flow", "layout", "zone", "tray")),
    ("line", ("timeline", "trace", "curve", "plot", "decay", "volume", "pressure", "loss")),
]


def _style_for(name: str, caption: str) -> str:
    hay = f"{name} {caption}".lower()
    for style, kws in _STYLE_KEYWORDS:
        if any(k in hay for k in kws):
            return style
    return "line"


def _title_from_caption(caption: str) -> str:
    """Title Case the caption into a figure title, trimmed of trailing detail."""
    c = (caption or "").strip().rstrip(".")
    # Keep it concise: first clause before " showing/over/around/of the ...".
    for sep in (" showing ", " over ", " around ", " during "):
        if sep in c.lower():
            idx = c.lower().index(sep)
            c = c[:idx]
            break
    # Title-case but preserve short function words lowercased mid-title.
    small = {"a", "an", "the", "of", "vs", "and", "over", "to", "in", "on", "for"}
    words = c.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(w.capitalize() if (i == 0 or lw not in small) else lw)
    return " ".join(out) or "Figure"


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "arialbd.ttf",
        "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_centered_title(draw, width: int, title: str, y: int, size: int = 22) -> None:
    font = _font(size)
    try:
        bbox = draw.textbbox((0, 0), title, font=font)
        tw = bbox[2] - bbox[0]
    except AttributeError:
        tw, _ = draw.textsize(title, font=font)
    draw.text(((width - tw) / 2, y), title, fill="black", font=font)


def _sketch_body(draw, kind: str, w: int, h: int) -> None:
    top = 70
    if kind == "line":
        draw.line([(60, h - 50), (160, h - 110), (280, h - 160),
                   (420, h - 220), (w - 60, h - 250)], fill="red", width=4)
        draw.line([(60, h - 50), (w - 60, h - 50)], fill="black", width=2)
        draw.line([(60, top), (60, h - 50)], fill="black", width=2)
    elif kind == "heatmap":
        import colorsys

        cols, rows = 8, 4
        cw = (w - 120) / cols
        ch = (h - top - 60) / rows
        for r in range(rows):
            for c in range(cols):
                heat = 1 - ((c / cols + r / rows) / 2)
                rr, gg, bb = colorsys.hsv_to_rgb(0.66 * (1 - heat), 0.8, 0.9)
                draw.rectangle(
                    [60 + c * cw, top + 10 + r * ch, 60 + (c + 1) * cw, top + 10 + (r + 1) * ch],
                    fill=(int(rr * 255), int(gg * 255), int(bb * 255)), outline="white",
                )
    elif kind == "topology":
        nodes = [(w // 2, top + 30), (140, h - 90), (w // 2, h - 70), (w - 140, h - 90)]
        for x, y in nodes:
            draw.ellipse([x - 26, y - 26, x + 26, y + 26], outline="black", width=3, fill="#dbe6f5")
        for x, y in nodes[1:]:
            draw.line([(nodes[0][0], nodes[0][1]), (x, y)], fill="black", width=2)


def build_figure(title: str, kind: str, w: int = FIG_WIDTH, h: int = FIG_HEIGHT) -> bytes:
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([2, 2, w - 3, h - 3], outline="#333333", width=2)
    _draw_centered_title(draw, w, title, y=20)
    _sketch_body(draw, kind, w, h)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_project(project_id: str) -> int:
    gjson_path = SCEN / project_id / "corpus" / "graphics.json"
    if not gjson_path.exists():
        return 0
    data = json.loads(gjson_path.read_text(encoding="utf-8"))
    graphics = data.get("graphics", [])
    if not graphics:
        return 0

    fig_dir = SCEN / project_id / "corpus" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    enriched = []
    for g in graphics:
        name = g["name"]
        caption = g.get("caption", "")
        # Preserve an explicit title if one was already set; else derive it.
        title = g.get("title") or _title_from_caption(caption)
        kind = _style_for(name, caption)
        (fig_dir / name).write_bytes(build_figure(title, kind))
        enriched.append({
            "graphic_id": g["graphic_id"],
            "name": name,
            "title": title,
            "caption": caption,
            "source_doc": g.get("source_doc", ""),
            "belongs_in_section": g.get("belongs_in_section", ""),
            "file": f"figures/{name}",
            "width": g.get("width", FIG_WIDTH),
            "height": g.get("height", FIG_HEIGHT),
            "align": g.get("align", FIG_ALIGN),
        })
        print(f"project {project_id}: wrote figures/{name} "
              f"({FIG_WIDTH}x{FIG_HEIGHT}, title='{title}', style={kind})")

    out = {
        "note": "Real PNG figures live under corpus/figures/. Each carries a "
                "managed name, a centered title baked into the image, and a "
                "controlled width/height/alignment that the pipeline tracks and "
                "inspects across template, draft, and final output.",
        "graphics": enriched,
    }
    gjson_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"project {project_id}: updated graphics.json ({len(enriched)} figures)")
    return len(enriched)


def main() -> None:
    project_ids = sorted(p.name for p in SCEN.iterdir()
                          if (p / "corpus" / "graphics.json").exists())
    total = 0
    for sid in project_ids:
        total += build_project(sid)
    print(f"\nDone: {total} figures across {len(project_ids)} projects.")


if __name__ == "__main__":
    main()
