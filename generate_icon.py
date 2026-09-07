"""
Generates high-quality icon assets (.png and .ico) for RTX 5090 Model Cascading
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

size = (256, 256)
img = Image.new("RGBA", size, (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Outer glow / background circle
draw.ellipse([8, 8, 248, 248], fill=(15, 23, 42, 255), outline=(16, 185, 129, 255), width=8)

# Inner accent ring
draw.ellipse([24, 24, 232, 232], outline=(56, 189, 248, 255), width=4)

# Circuit / Core Accent (GPU Die Box)
draw.rounded_rectangle([60, 60, 196, 196], radius=20, fill=(30, 41, 59, 255), outline=(16, 185, 129, 255), width=6)

# Traces connecting core
draw.line([(32, 128), (60, 128)], fill=(16, 185, 129, 255), width=6)
draw.line([(196, 128), (224, 128)], fill=(16, 185, 129, 255), width=6)
draw.line([(128, 32), (128, 60)], fill=(56, 189, 248, 255), width=6)
draw.line([(128, 196), (128, 224)], fill=(56, 189, 248, 255), width=6)

# Text "5090"
# Simple fallback geometric text drawing if default fonts vary
draw.rectangle([80, 95, 176, 160], fill=(16, 185, 129, 255))
draw.rectangle([92, 105, 164, 150], fill=(30, 41, 59, 255))
draw.text((88, 112), "5090", fill=(56, 189, 248, 255), font_size=32)

png_path = ASSETS_DIR / "icon.png"
ico_path = ASSETS_DIR / "icon.ico"

img.save(png_path, format="PNG")
img.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

print(f"Generated {png_path} and {ico_path}")
