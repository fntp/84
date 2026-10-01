# -*- coding: utf-8 -*-
"""
标注可视化预览工具 —— 只读原图，输出到独立目录

从数据集中随机抽取 N 张图，把 YOLO 标签画成框并导出。
按目标尺寸着色，便于人工判断标注质量：
    绿色 = large  (边长 > 96px 等效)  —— 多半是「自己的角色」
    橙色 = medium (32~96px 等效)
    红色 = small  (边长 < 32px 等效)  —— 远处敌人

用法:
    python make_preview.py "<yolo_dataset 路径>" [输出目录] [数量]
"""
import os
import sys
import random

from PIL import Image, ImageDraw, ImageFont

IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.jfif'}
SPLITS = ['train', 'val', 'test']

COLOR_LARGE = (60, 220, 90)
COLOR_MED = (255, 165, 30)
COLOR_SMALL = (240, 60, 60)


def load_font(size=18):
    for p in ['C:/Windows/Fonts/arialbd.ttf', 'C:/Windows/Fonts/arial.ttf',
              'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf']:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def main():
    if len(sys.argv) < 2:
        print('用法: python make_preview.py <yolo_dataset> [输出目录] [数量]')
        return
    root = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else \
        os.path.join(os.path.dirname(root), 'yolo_dataset_preview')
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    os.makedirs(out, exist_ok=True)
    font = load_font(20)

    pool = []
    for sp in SPLITS:
        img_dir = os.path.join(root, 'images', sp)
        if not os.path.isdir(img_dir):
            continue
        for f in sorted(os.listdir(img_dir)):
            if os.path.splitext(f)[1].lower() in IMG_EXT:
                pool.append((sp, f))

    random.seed(42)
    pick = random.sample(pool, min(n, len(pool)))
    pick.sort()

    print('=' * 72)
    print('生成标注预览图')
    print('=' * 72)
    print(f'数据集 : {root}')
    print(f'输出   : {out}')
    print(f'抽取   : {len(pick)} / {len(pool)} 张')
    print()

    stats = {'large': 0, 'medium': 0, 'small': 0}

    for i, (sp, fn) in enumerate(pick, 1):
        stem = os.path.splitext(fn)[0]
        img_path = os.path.join(root, 'images', sp, fn)
        lbl_path = os.path.join(root, 'labels', sp, stem + '.txt')

        im = Image.open(img_path).convert('RGB')
        W, H = im.size
        d = ImageDraw.Draw(im)

        n_box = 0
        if os.path.isfile(lbl_path):
            with open(lbl_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    p = line.split()
                    if len(p) < 5:
                        continue
                    x, y, w, h = (float(v) for v in p[1:5])
                    x1 = (x - w / 2) * W
                    y1 = (y - h / 2) * H
                    x2 = (x + w / 2) * W
                    y2 = (y + h / 2) * H
                    side = max(w * W, h * H)
                    if side < 32:
                        col = COLOR_SMALL
                        stats['small'] += 1
                    elif side < 96:
                        col = COLOR_MED
                        stats['medium'] += 1
                    else:
                        col = COLOR_LARGE
                        stats['large'] += 1
                    d.rectangle([x1, y1, x2, y2], outline=col, width=3)
                    d.text((x1 + 4, max(0, y1 - 22)), 'person', fill=col, font=font,
                           stroke_width=2, stroke_fill=(0, 0, 0))
                    n_box += 1

        tag = f'{sp} | {n_box} obj'
        d.text((10, 10), tag, fill=(255, 255, 255), font=font,
               stroke_width=2, stroke_fill=(0, 0, 0))

        out_name = f'preview_{i:04d}_{sp}_{stem[:40]}.jpg'
        im.save(os.path.join(out, out_name), quality=88)
        if i % 15 == 0:
            print(f'  已生成 {i}/{len(pick)}')

    print(f'  完成 {len(pick)} 张 -> {out}')
    print()
    print(f'目标尺寸分布（本次抽样）: large {stats["large"]}  medium {stats["medium"]}  small {stats["small"]}')
    print('=' * 72)


if __name__ == '__main__':
    main()
