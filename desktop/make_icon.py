"""Rasterize the repository's simple geometric app mark for desktop packages."""

from pathlib import Path
from PIL import Image, ImageDraw


size = 1024
canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(canvas)
scale = size / 256
def pt(box):
    return tuple(round(value * scale) for value in box)

draw.rounded_rectangle(pt((8, 8, 248, 248)), radius=round(56 * scale), fill="#252B31")
draw.arc(pt((44, 42, 212, 141)), 202, 338, fill="#EF771B", width=round(16 * scale))
draw.arc(pt((44, 115, 212, 214)), 22, 158, fill="#EF771B", width=round(16 * scale))
draw.rounded_rectangle(pt((52, 95, 204, 161)), radius=round(17 * scale), fill="#F5F5F2")
draw.polygon([pt((111, 110)), pt((153, 128)), pt((111, 146))], fill="#A64600")
draw.line([pt((128, 95)), pt((128, 161))], fill="#B9BFC1", width=round(3 * scale))
asset_dir = Path(__file__).parent / "FpvDvrCleaner.Desktop" / "Assets"
canvas.resize((256, 256), Image.Resampling.LANCZOS).save(asset_dir / "app.png")
canvas.save(asset_dir / "app.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
