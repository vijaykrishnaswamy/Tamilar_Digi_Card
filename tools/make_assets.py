"""Generate Apple Wallet and Google Wallet image assets from a source logo.

Apple requires PNG at specific point sizes, at 1x/2x/3x. Google takes a single
hosted PNG. Run once when the logo changes:

    python tools/make_assets.py "C:/Users/me/Downloads/tamilar Logo.jpeg"

Outputs into app/assets/. The pass is on a WHITE background, so the logo is
flattened onto white rather than left transparent - a transparent PNG with dark
artwork would be invisible if Wallet ever renders it on a dark surface.
"""

import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "app" / "assets"

# Apple: (filename, max width x max height in px). icon is square-ish and is the
# one Apple mandates; logo appears top-left on the pass face.
APPLE = {
    "icon.png": (29, 29),
    "icon@2x.png": (58, 58),
    "icon@3x.png": (87, 87),
    "logo.png": (160, 50),
    "logo@2x.png": (320, 100),
    "logo@3x.png": (480, 150),
}

# Google hero/logo image - served over HTTPS, so one generous size is enough.
GOOGLE = {"google_logo.png": (660, 660)}


def fit_on_white(source: Image.Image, box: tuple) -> Image.Image:
    """Contain the logo inside `box` preserving aspect ratio, centred on white."""
    target_w, target_h = box
    logo = source.copy()
    logo.thumbnail((target_w, target_h), Image.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    canvas.paste(logo, ((target_w - logo.width) // 2, (target_h - logo.height) // 2))
    return canvas


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    src_path = Path(argv[1])
    if not src_path.exists():
        print(f"source not found: {src_path}")
        return 2

    source = Image.open(src_path).convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)

    for name, box in {**APPLE, **GOOGLE}.items():
        out_path = OUT / name
        fit_on_white(source, box).save(out_path, "PNG", optimize=True)
        print(f"  {name:20} {box[0]}x{box[1]:<5} {out_path.stat().st_size:>7,} bytes")

    print(f"\nwrote {len(APPLE) + len(GOOGLE)} asset(s) to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
