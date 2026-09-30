"""앱 아이콘(workreport.ico) 생성: 파란 둥근 사각형 + 흰색 W."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make(path: Path) -> Path:
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((16, 16, size - 16, size - 16), radius=56, fill=(37, 99, 235, 255))
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 150)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    draw.text((size / 2, size / 2), "W", fill="white", font=font, anchor="mm")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


if __name__ == "__main__":
    print(make(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("workreport.ico"))))
