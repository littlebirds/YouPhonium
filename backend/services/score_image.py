"""Conservative raster preparation: no staff removal or invented detail."""

from math import ceil
from pathlib import Path
from statistics import median
from typing import Optional

import numpy as np
from PIL import Image, ImageOps


def load_score_image(path: Path) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image)
        return background.convert("L")


def staff_spacing(image: Image.Image) -> Optional[float]:
    """Estimate spacing from repeated five-line horizontal groups.

    Return None for photos/skewed/ambiguous pages; do not pretend page width or
    embedded DPI measures musical symbol resolution.
    """
    pixels = np.asarray(image)
    rows = np.flatnonzero(np.mean(pixels < 180, axis=1) > 0.45)
    if len(rows) < 5:
        return None
    runs = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    centers = [float(np.mean(run)) for run in runs]
    spacings = []
    for i in range(len(centers) - 4):
        distances = np.diff(centers[i:i + 5])
        spacing = float(np.median(distances))
        if 3 <= spacing <= 80 and np.max(np.abs(distances - spacing)) <= spacing * 0.2:
            spacings.append(spacing)
    return median(spacings) if spacings else None


def prepare_score_image(source: Path, destination: Path, *, alternate=False) -> dict:
    image = load_score_image(source)
    spacing = staff_spacing(image)
    # Audiveris rejects very small interlines. Integer enlargement preserves the
    # sampling grid; the alternate uses a smaller half-step to test sensitivity.
    scale = 1.0
    if spacing is not None and spacing < 12:
        scale = ceil(12 / spacing * (2 if alternate else 1)) / (2 if alternate else 1)
        scale = min(scale, 4.0)
    if scale != 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)),
                             Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, dpi=(300, 300))
    return {"staff_spacing_pixels": spacing, "scale": scale,
            "width": image.width, "height": image.height}
