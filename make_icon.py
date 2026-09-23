"""Regenerate Sea Explorer's Windows icon (requires Pillow)."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
SIZE = 1024


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(SIZE):
        for x in range(SIZE):
            shade = 1 - min(1.0, math.hypot(x - 215, y - 150) / 1200)
            depth = y / SIZE
            pixels[x, y] = (
                round(6 + 19 * shade),
                round(24 + 45 * shade - 4 * depth),
                round(39 + 57 * shade - 4 * depth),
                255,
            )

    shape = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(shape).rounded_rectangle((24, 24, 1000, 1000), radius=225, fill=255)
    image.putalpha(shape)

    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((190, 185, 825, 820), outline=(40, 227, 205, 95), width=85)
    glow = glow.filter(ImageFilter.GaussianBlur(75))
    image = Image.alpha_composite(image, glow)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (28, 28, 996, 996), radius=220, outline=(82, 180, 188, 160), width=13
    )

    # A diving mask is legible even at the 16 px taskbar size.
    draw.ellipse((197, 194, 827, 824), fill="#0A2B3A", outline="#42D8C2", width=54)
    draw.arc((217, 213, 807, 803), 198, 305, fill="#B3FFF1", width=18)
    draw.arc((225, 221, 799, 795), 18, 105, fill="#167D84", width=16)

    draw.rounded_rectangle(
        (274, 360, 750, 608), radius=119, fill="#52E0CC", outline="#D3FFF6", width=19
    )
    draw.rounded_rectangle((310, 397, 714, 566), radius=82, fill="#0C4356")
    draw.rounded_rectangle((344, 412, 579, 449), radius=18, fill=(212, 255, 249, 185))
    draw.arc((355, 442, 668, 585), 195, 347, fill="#268FA0", width=34)

    draw.rounded_rectangle(
        (426, 552, 598, 697), radius=66, fill="#0E4150", outline="#64E8D7", width=27
    )
    draw.ellipse((478, 612, 546, 680), fill="#BFFFF2")

    # Gold treasure glint gives the mark its small-size color cue.
    draw.ellipse((680, 690, 866, 876), fill="#E9B958", outline="#FFF2BD", width=18)
    draw.ellipse((718, 728, 828, 838), outline="#8C6228", width=15)
    draw.polygon((773, 742, 787, 771, 815, 785, 787, 799, 773, 827, 759, 799, 731, 785, 759, 771), fill="#FFF4C9")

    draw.ellipse((741, 224, 817, 300), fill="#7DEAD6")
    draw.ellipse((834, 188, 870, 224), fill="#B6FFF1")

    # Reapply the rounded-square alpha after drawing all accents.
    alpha = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(alpha).rounded_rectangle((24, 24, 1000, 1000), radius=225, fill=255)
    image.putalpha(alpha)
    image.resize((512, 512), Image.Resampling.LANCZOS).save(ROOT / "sea_explorer_icon.png")
    image.save(
        ROOT / "sea_explorer.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
