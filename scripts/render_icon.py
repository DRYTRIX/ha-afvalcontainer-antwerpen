"""Render the brand icon PNGs (same geometry as docs/brand/icon.svg).

Usage: python scripts/render_icon.py   (needs Pillow; installed with the test requirements)
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "custom_components" / "afvalcontainer_antwerpen" / "brand"
S = 4  # supersampling factor on a 512 viewbox


def render() -> Image.Image:
    """Draw the icon at 2048 px."""
    img = Image.new("RGBA", (512 * S, 512 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x0: float, y0: float, x1: float, y1: float) -> list[float]:
        return [x0 * S, y0 * S, x1 * S, y1 * S]

    def circle(cx: float, cy: float, r: float, fill: str) -> None:
        d.ellipse(box(cx - r, cy - r, cx + r, cy + r), fill=fill)

    d.rounded_rectangle(box(216, 70, 296, 104), radius=10 * S, fill="#1B5E20")
    d.rounded_rectangle(box(96, 92, 416, 140), radius=16 * S, fill="#1B5E20")
    d.polygon(
        [(x * S, y * S) for x, y in ((112, 150), (400, 150), (376, 440), (136, 440))],
        fill="#2E7D32",
    )
    d.rounded_rectangle(box(150, 170, 164, 420), radius=7 * S, fill="#388E3C")
    d.rounded_rectangle(box(348, 170, 362, 420), radius=7 * S, fill="#388E3C")
    for cx in (160, 352):
        circle(cx, 450, 34, "#263238")
        circle(cx, 450, 12, "#90A4AE")
    circle(256, 290, 96, "#FFC107")
    # Euro: open arc (gap on the right) + two bars. Arc centre (268, 290), r 58, width 22.
    r, w = 58, 22
    d.arc(
        box(268 - r - w / 2, 290 - r - w / 2, 268 + r + w / 2, 290 + r + w / 2),
        start=40,
        end=320,
        fill="#3E2723",
        width=w * S,
    )
    d.rectangle(box(180, 253, 280, 271), fill="#3E2723")
    d.rectangle(box(180, 309, 280, 327), fill="#3E2723")
    return img


def main() -> None:
    """Write icon.png (256) and icon@2x.png (512), trimmed per brand guidelines."""
    OUT.mkdir(parents=True, exist_ok=True)
    big = render()
    big = big.crop(big.getbbox())
    side = max(big.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(big, ((side - big.width) // 2, (side - big.height) // 2))
    for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
        square.resize((size, size), Image.Resampling.LANCZOS).save(OUT / name, optimize=True)
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
