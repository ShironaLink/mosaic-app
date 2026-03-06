"""MosaicApp用アイコン生成スクリプト"""
from PIL import Image, ImageDraw
import random

def create_mosaic_icon():
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 角丸背景
    margin = 8
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=40, fill=(50, 50, 60, 255)
    )

    # モザイクグリッド
    grid_margin = 32
    grid_size = size - grid_margin * 2
    block = grid_size // 6

    colors = [
        (66, 133, 244),   # blue
        (52, 168, 83),    # green
        (251, 188, 4),    # yellow
        (234, 67, 53),    # red
        (138, 180, 248),  # light blue
        (129, 201, 149),  # light green
        (253, 214, 99),   # light yellow
        (242, 139, 130),  # light red
    ]

    random.seed(42)
    for row in range(6):
        for col in range(6):
            x0 = grid_margin + col * block
            y0 = grid_margin + row * block
            x1 = x0 + block - 3
            y1 = y0 + block - 3
            color = random.choice(colors)
            draw.rounded_rectangle([x0, y0, x1, y1], radius=4, fill=color)

    # スポイトアイコン（右下）
    cx, cy = 195, 195
    draw.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(255, 255, 255, 220))
    draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(234, 67, 53))

    # 複数サイズで ico 保存
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    icons[0].save(
        "mosaic_app.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=icons[1:]
    )
    print("mosaic_app.ico created")

if __name__ == "__main__":
    create_mosaic_icon()
