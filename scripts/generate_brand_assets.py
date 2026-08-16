from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "web" / "app"
PUBLIC_DIR = ROOT / "apps" / "web" / "public"


CANVAS = (8, 11, 14)
CANVAS_2 = (20, 27, 34)
PANEL = (12, 17, 22)
PANEL_2 = (17, 24, 31)
GRID = (99, 112, 124)
TRACE = (136, 150, 162)
BLUE = (52, 119, 255)
ORANGE = (244, 132, 35)
GREEN = (38, 169, 103)
GREEN_DARK = (9, 92, 59)
YELLOW = (229, 182, 47)
WHITE = (240, 246, 243)
MUTED = (173, 186, 198)
RED = (222, 84, 72)


def _linear_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    img = Image.new("RGB", size, top)
    pixels = img.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        row = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        for x in range(width):
            pixels[x, y] = row
    return img.convert("RGBA")


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _scale_points(points: Iterable[tuple[float, float]], box: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    x0, y0, x1, y1 = box
    return [(round(x0 + x * (x1 - x0)), round(y0 + y * (y1 - y0))) for x, y in points]


def _draw_grid(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], alpha: int = 80, width: int = 2) -> None:
    x0, y0, x1, y1 = box
    for i in range(1, 6):
        x = x0 + (x1 - x0) * i / 6
        draw.line((x, y0, x, y1), fill=(*GRID, alpha), width=width)
    for i in range(1, 5):
        y = y0 + (y1 - y0) * i / 5
        draw.line((x0, y, x1, y), fill=(*GRID, alpha), width=width)


def _draw_chart(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    scale: float,
    show_forecast_label: bool = False,
) -> None:
    x0, y0, x1, y1 = box
    forecast_x = round(x0 + (x1 - x0) * 0.74)
    draw.rectangle((forecast_x, y0, x1, y1), fill=(126, 201, 155, 24))
    for offset in range(0, x1 - forecast_x, max(round(20 * scale), 10)):
        draw.line((forecast_x + offset, y0, forecast_x + offset - round(26 * scale), y1), fill=(126, 201, 155, 26), width=max(1, round(2 * scale)))

    _draw_grid(draw, box, alpha=70, width=max(1, round(2 * scale)))

    metric_points = _scale_points(
        [
            (0.00, 0.82),
            (0.11, 0.76),
            (0.24, 0.66),
            (0.36, 0.59),
            (0.50, 0.54),
            (0.62, 0.48),
            (0.74, 0.38),
            (0.87, 0.31),
            (1.00, 0.22),
        ],
        box,
    )
    draw.polygon(metric_points + [(x1, y1), (x0, y1)], fill=(*GREEN_DARK, 192))
    draw.line(metric_points, fill=(*GREEN, 238), width=max(3, round(7 * scale)), joint="curve")

    normal = _scale_points(
        [(0.00, 0.51), (0.15, 0.45), (0.30, 0.35), (0.48, 0.31), (0.64, 0.26), (0.80, 0.19), (1.00, 0.14)],
        box,
    )
    fair = _scale_points(
        [(0.00, 0.70), (0.17, 0.64), (0.34, 0.58), (0.52, 0.50), (0.69, 0.43), (0.84, 0.35), (1.00, 0.29)],
        box,
    )
    price = _scale_points(
        [
            (0.00, 0.66),
            (0.09, 0.57),
            (0.17, 0.61),
            (0.28, 0.44),
            (0.39, 0.37),
            (0.50, 0.56),
            (0.60, 0.40),
            (0.70, 0.43),
            (0.80, 0.26),
            (0.90, 0.17),
            (1.00, 0.30),
        ],
        box,
    )
    dividend = _scale_points([(0, 0.88), (0.24, 0.83), (0.50, 0.76), (0.76, 0.69), (1, 0.62)], box)

    draw.line(normal, fill=(*BLUE, 255), width=max(4, round(9 * scale)), joint="curve")
    draw.line(fair, fill=(*ORANGE, 255), width=max(4, round(9 * scale)), joint="curve")
    draw.line(dividend, fill=(*YELLOW, 235), width=max(2, round(5 * scale)), joint="curve")
    draw.line(price, fill=(0, 0, 0, 255), width=max(7, round(16 * scale)), joint="curve")
    draw.line(price, fill=(*WHITE, 250), width=max(2, round(5 * scale)), joint="curve")

    for point, color in [
        (normal[-1], BLUE),
        (fair[-1], ORANGE),
        (metric_points[-1], GREEN),
        (price[-2], WHITE),
    ]:
        px, py = point
        radius = max(3, round(6 * scale))
        draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=(*PANEL, 255), outline=(*color, 245), width=max(1, round(2 * scale)))

    if show_forecast_label:
        font = _font("segoeuib.ttf", max(16, round(20 * scale)))
        draw.rounded_rectangle(
            (forecast_x + round(12 * scale), y0 + round(12 * scale), forecast_x + round(92 * scale), y0 + round(44 * scale)),
            radius=round(12 * scale),
            fill=(18, 32, 27, 210),
            outline=(*GREEN, 155),
            width=max(1, round(1 * scale)),
        )
        draw.text((forecast_x + round(25 * scale), y0 + round(17 * scale)), "FY1-5", font=font, fill=(213, 242, 222))


def _draw_trace_strip(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, scale: float) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=round(20 * scale), fill=(8, 14, 19, 218), outline=(85, 98, 111, 118), width=max(1, round(2 * scale)))
    xs = [round(x0 + (x1 - x0) * n) for n in (0.10, 0.34, 0.58, 0.82)]
    cy = round((y0 + y1) / 2)
    colors = [BLUE, MUTED, ORANGE, GREEN]
    for index, x in enumerate(xs):
        if index < len(xs) - 1:
            draw.line((x, cy, xs[index + 1], cy), fill=(*TRACE, 140), width=max(2, round(4 * scale)))
        radius = round(15 * scale)
        draw.ellipse((x - radius, cy - radius, x + radius, cy + radius), fill=(*PANEL_2, 255), outline=(*colors[index], 255), width=max(2, round(5 * scale)))
    draw.ellipse(
        (xs[-1] - round(6 * scale), cy - round(6 * scale), xs[-1] + round(6 * scale), cy + round(6 * scale)),
        fill=(*GREEN, 255),
    )


def _draw_mark(canvas_size: int) -> Image.Image:
    scale = canvas_size / 512
    base = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    bg = _linear_gradient((canvas_size, canvas_size), CANVAS, CANVAS_2)
    mask = _rounded_mask((canvas_size, canvas_size), round(74 * scale))
    base.alpha_composite(Image.composite(bg, Image.new("RGBA", bg.size, (0, 0, 0, 0)), mask))
    draw = ImageDraw.Draw(base, "RGBA")

    # Favicon-first mark: a large valuation-map V remains readable at 16px.
    shell = (round(44 * scale), round(48 * scale), round(468 * scale), round(464 * scale))
    draw.rounded_rectangle(
        shell,
        radius=round(58 * scale),
        fill=(8, 13, 18, 244),
        outline=(106, 122, 136, 168),
        width=max(2, round(4 * scale)),
    )
    chart_box = (round(76 * scale), round(92 * scale), round(436 * scale), round(366 * scale))
    draw.rounded_rectangle(
        chart_box,
        radius=round(30 * scale),
        fill=(10, 16, 22, 230),
        outline=(77, 92, 107, 150),
        width=max(1, round(2 * scale)),
    )
    _draw_grid(draw, chart_box, alpha=52, width=max(1, round(2 * scale)))

    x0, y0, x1, y1 = chart_box
    forecast_x = round(x0 + (x1 - x0) * 0.72)
    draw.rectangle((forecast_x, y0 + round(8 * scale), x1 - round(8 * scale), y1 - round(8 * scale)), fill=(126, 201, 155, 24))
    eps = _scale_points(
        [
            (0.00, 0.86),
            (0.14, 0.74),
            (0.30, 0.68),
            (0.46, 0.54),
            (0.62, 0.50),
            (0.78, 0.33),
            (1.00, 0.23),
        ],
        chart_box,
    )
    draw.polygon(eps + [(x1, y1), (x0, y1)], fill=(*GREEN_DARK, 202))
    draw.line(eps, fill=(*GREEN, 238), width=max(3, round(8 * scale)), joint="curve")

    normal = _scale_points([(0.02, 0.55), (0.24, 0.47), (0.48, 0.37), (0.72, 0.30), (0.98, 0.20)], chart_box)
    fair = _scale_points([(0.02, 0.72), (0.26, 0.64), (0.50, 0.55), (0.74, 0.43), (0.98, 0.35)], chart_box)
    price_v = _scale_points([(0.07, 0.31), (0.32, 0.64), (0.50, 0.40), (0.68, 0.69), (0.94, 0.22)], chart_box)

    draw.line(normal, fill=(*BLUE, 255), width=max(5, round(13 * scale)), joint="curve")
    draw.line(fair, fill=(*ORANGE, 255), width=max(5, round(13 * scale)), joint="curve")
    draw.line(price_v, fill=(0, 0, 0, 255), width=max(12, round(26 * scale)), joint="curve")
    draw.line(price_v, fill=(*WHITE, 255), width=max(5, round(11 * scale)), joint="curve")

    for point, color in [(price_v[-1], WHITE), (normal[-1], BLUE), (fair[-1], ORANGE), (eps[-1], GREEN)]:
        px, py = point
        radius = max(4, round(10 * scale))
        draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=(*PANEL, 255), outline=(*color, 250), width=max(1, round(3 * scale)))

    strip = (round(76 * scale), round(388 * scale), round(436 * scale), round(438 * scale))
    draw.rounded_rectangle(strip, radius=round(23 * scale), fill=(7, 12, 17, 242), outline=(91, 106, 120, 148), width=max(1, round(2 * scale)))
    xs = [round(strip[0] + (strip[2] - strip[0]) * n) for n in (0.18, 0.42, 0.66, 0.84)]
    cy = round((strip[1] + strip[3]) / 2)
    for index, x in enumerate(xs):
        if index < len(xs) - 1:
            draw.line((x, cy, xs[index + 1], cy), fill=(*TRACE, 138), width=max(2, round(4 * scale)))
    for x, color in zip(xs, [BLUE, ORANGE, YELLOW, GREEN], strict=False):
        radius = round(10 * scale)
        draw.ellipse((x - radius, cy - radius, x + radius, cy + radius), fill=(*PANEL_2, 255), outline=(*color, 255), width=max(2, round(4 * scale)))
    check_x, check_y = xs[-1], cy
    draw.line(
        (
            check_x - round(5 * scale),
            check_y,
            check_x - round(1 * scale),
            check_y + round(5 * scale),
            check_x + round(7 * scale),
            check_y - round(7 * scale),
        ),
        fill=(219, 250, 226, 255),
        width=max(2, round(3 * scale)),
        joint="curve",
    )

    return base


def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts") / name,
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _draw_pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], label: str, color: tuple[int, int, int]) -> int:
    font = _font("segoeuib.ttf", 18)
    x, y = xy
    box = draw.textbbox((0, 0), label, font=font)
    width = box[2] - box[0] + 30
    draw.rounded_rectangle((x, y, x + width, y + 38), radius=17, fill=(*color, 34), outline=(*color, 190), width=1)
    draw.text((x + 15, y + 8), label, font=font, fill=(237, 244, 240))
    return width


def _draw_og() -> Image.Image:
    size = (1200, 630)
    img = _linear_gradient(size, (228, 235, 236), (204, 215, 218))
    draw = ImageDraw.Draw(img, "RGBA")

    draw.rounded_rectangle((42, 38, 1158, 592), radius=30, fill=(244, 248, 247, 250), outline=(151, 168, 180, 190), width=2)
    draw.rounded_rectangle((72, 68, 548, 562), radius=24, fill=(9, 14, 19, 252), outline=(69, 84, 98, 175), width=1)

    mark = _draw_mark(176)
    img.alpha_composite(mark, (102, 84))
    draw.text((300, 106), "ValuTrace", font=_font("segoeuib.ttf", 52), fill=(244, 250, 247))
    draw.text((304, 168), "Underwriting Terminal", font=_font("segoeuib.ttf", 23), fill=(195, 211, 221))
    draw.text((304, 203), "Adjusted EPS | FY1-5 | audit", font=_font("segoeui.ttf", 18), fill=(141, 158, 171))

    x = 106
    for label, color in [("S1", BLUE), ("XBRL", ORANGE), ("FY1-5", GREEN), ("Audit", YELLOW)]:
        x += _draw_pill(draw, (x, 270), label, color) + 12

    card = (106, 340, 516, 522)
    draw.rounded_rectangle(card, radius=18, fill=(9, 14, 19, 235), outline=(69, 84, 98, 150), width=1)
    rows = [
        ("Metric", "Adjusted Operating EPS"),
        ("Policy", "Street / Core / Custom"),
        ("Forecast", "Consensus + AI + manual"),
        ("Trace", "filing | formula | flags"),
    ]
    y = 366
    for key, value in rows:
        draw.text((132, y), key, font=_font("segoeui.ttf", 20), fill=(143, 158, 172))
        draw.text((236, y), value, font=_font("segoeuib.ttf", 19), fill=(239, 246, 242))
        y += 36

    chart_panel = (590, 74, 1128, 554)
    draw.rounded_rectangle(chart_panel, radius=26, fill=(10, 15, 20, 248), outline=(83, 98, 112, 162), width=1)
    draw.rounded_rectangle((614, 94, 1104, 134), radius=12, fill=(18, 28, 36, 230), outline=(76, 91, 105, 110), width=1)
    draw.text((632, 103), "Historical Valuation Map", font=_font("segoeuib.ttf", 18), fill=(229, 238, 234))
    draw.text((942, 104), "Forecast + overlays", font=_font("segoeui.ttf", 16), fill=(142, 219, 166))
    _draw_chart(draw, (630, 148, 1092, 474), scale=1.0, show_forecast_label=True)

    _draw_trace_strip(draw, (644, 496, 1080, 548), scale=0.72)
    labels = [("Price", WHITE), ("Normal", BLUE), ("Fair", ORANGE), ("EPS", GREEN)]
    lx = 658
    for label, color in labels:
        draw.ellipse((lx, 532, lx + 10, 542), fill=(*color, 255))
        draw.text((lx + 16, 526), label, font=_font("segoeui.ttf", 15), fill=(174, 188, 199))
        lx += 92

    draw.text((624, 560), "Vercel-first | Neon Postgres | Blob-cached chart renders", font=_font("segoeui.ttf", 16), fill=(91, 104, 116))
    return img


def _write_svg(path: Path) -> None:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72" role="img" aria-label="ValuTrace underwriting valuation terminal mark">
  <defs>
    <linearGradient id="vt-bg" x1="5" y1="4" x2="67" y2="68" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#080b0e"/>
      <stop offset="1" stop-color="#151f28"/>
    </linearGradient>
    <linearGradient id="vt-eps" x1="9" y1="49" x2="65" y2="16" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#095c3b"/>
      <stop offset="1" stop-color="#26a967"/>
    </linearGradient>
  </defs>
  <rect x="3" y="3" width="66" height="66" rx="13" fill="url(#vt-bg)"/>
  <rect x="9" y="10" width="54" height="43" rx="7" fill="#0b1015" stroke="#627282" stroke-width="1.2"/>
  <path d="M13 47V17M24 47V17M35 47V17M46 47V17M57 47V17M13 47H60M13 38H60M13 29H60M13 20H60" fill="none" stroke="#63717c" stroke-width=".8" opacity=".38"/>
  <path d="M50 18h10v29H50z" fill="#7ec99b" opacity=".18"/>
  <path d="M13 47C20 42 26 40 32 35C39 29 47 24 60 16V49H13Z" fill="url(#vt-eps)" opacity=".9"/>
  <path d="M13 31C25 28 37 23 48 21C53 20 57 18 60 16" fill="none" stroke="#3477ff" stroke-width="4" stroke-linecap="round"/>
  <path d="M13 43C25 39 36 35 47 29C53 26 57 23 60 21" fill="none" stroke="#f48423" stroke-width="4" stroke-linecap="round"/>
  <path d="M13 26C21 36 28 45 36 33C42 23 48 42 60 20" fill="none" stroke="#010203" stroke-width="7.6" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M13 26C21 36 28 45 36 33C42 23 48 42 60 20" fill="none" stroke="#f0f6f3" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="11" y="58" width="50" height="6" rx="3" fill="#081016" stroke="#5f6f7d" stroke-width=".8"/>
  <path d="M19 61H54" stroke="#8896a2" stroke-width="1" opacity=".7"/>
  <circle cx="19" cy="61" r="3" fill="#111923" stroke="#3477ff" stroke-width="1.8"/>
  <circle cx="33" cy="61" r="3" fill="#111923" stroke="#f48423" stroke-width="1.8"/>
  <circle cx="47" cy="61" r="3" fill="#111923" stroke="#e5b62f" stroke-width="1.8"/>
  <circle cx="55" cy="61" r="3.2" fill="#26a967" stroke="#d8f4df" stroke-width="1.6"/>
  <path d="M53.2 61.1l1.2 1.3 2.7-3.1" fill="none" stroke="#dbfae2" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    _write_svg(APP_DIR / "icon.svg")
    _write_svg(PUBLIC_DIR / "valuetrace-mark.svg")

    mark = _draw_mark(1024)
    mark.resize((512, 512), Image.Resampling.LANCZOS).save(PUBLIC_DIR / "valuetrace-mark.png", optimize=True)
    mark.save(
        APP_DIR / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    og = _draw_og()
    og.save(PUBLIC_DIR / "valuetrace-og.png", optimize=True)

    print("Generated ValuTrace brand assets:")
    print(f"- {APP_DIR / 'icon.svg'}")
    print(f"- {APP_DIR / 'favicon.ico'}")
    print(f"- {PUBLIC_DIR / 'valuetrace-mark.svg'}")
    print(f"- {PUBLIC_DIR / 'valuetrace-mark.png'}")
    print(f"- {PUBLIC_DIR / 'valuetrace-og.png'}")


if __name__ == "__main__":
    main()
