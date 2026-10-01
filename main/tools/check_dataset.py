# -*- coding: utf-8 -*-
"""
数据集质量检查工具 —— 只读

检查项:
  1. label 文件是否存在
  2. 图片是否存在
  3. class id 是否合法
  4. 中心点是否在 0~1
  5. width > 0
  6. height > 0
  7. 框是否越界（中心±半宽/半高 超出 0~1）
  8. 是否存在空 label
  9. 是否存在图片没有 label
 10. 是否存在 label 没有对应图片

另附: 目标尺寸分布（COCO small/medium/large 口径）

用法:
    python check_dataset.py "<yolo_dataset 路径>"
"""
import os
import sys
from collections import Counter, defaultdict

IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.jfif'}
SPLITS = ['train', 'val', 'test']


def main():
    root = os.path.abspath(sys.argv[1])
    print('=' * 72)
    print('数据集质量报告')
    print('=' * 72)
    print(f'路径: {root}')
    print()

    problems = []
    summary = {}

    for sp in SPLITS:
        img_dir = os.path.join(root, 'images', sp)
        lbl_dir = os.path.join(root, 'labels', sp)
        if not os.path.isdir(img_dir):
            print(f'[跳过] {sp}: 无 images 目录')
            continue

        imgs = {os.path.splitext(f)[0]: f for f in os.listdir(img_dir)
                if os.path.splitext(f)[1].lower() in IMG_EXT}
        lbls = {os.path.splitext(f)[0]: f for f in os.listdir(lbl_dir)
                if f.lower().endswith('.txt')} if os.path.isdir(lbl_dir) else {}

        cls_counter = Counter()
        n_obj = 0
        empty = 0
        bad_cls = 0
        bad_center = 0
        bad_w = 0
        bad_h = 0
        oob = 0
        areas = []

        for stem, lf in lbls.items():
            p = os.path.join(lbl_dir, lf)
            with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [l.strip() for l in f if l.strip()]
            if not lines:
                empty += 1
                continue
            for l in lines:
                parts = l.split()
                if len(parts) < 5:
                    problems.append(f'[{sp}] 列数不足5: {lf}')
                    continue
                try:
                    c = int(parts[0])
                    x, y, w, h = (float(v) for v in parts[1:5])
                except ValueError:
                    problems.append(f'[{sp}] 解析失败: {lf} -> {l}')
                    continue
                cls_counter[c] += 1
                n_obj += 1
                if c != 0:
                    bad_cls += 1
                    problems.append(f'[{sp}] 非法 class id={c}: {lf}')
                if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    bad_center += 1
                    problems.append(f'[{sp}] 中心点越界 ({x:.4f},{y:.4f}): {lf}')
                if w <= 0:
                    bad_w += 1
                    problems.append(f'[{sp}] width<=0 ({w}): {lf}')
                if h <= 0:
                    bad_h += 1
                    problems.append(f'[{sp}] height<=0 ({h}): {lf}')
                if x - w / 2 < -0.001 or x + w / 2 > 1.001 or y - h / 2 < -0.001 or y + h / 2 > 1.001:
                    oob += 1
                    problems.append(f'[{sp}] 框越界 ({x:.4f},{y:.4f},{w:.4f},{h:.4f}): {lf}')
                areas.append(w * h)

        img_no_lbl = sorted(set(imgs) - set(lbls))
        lbl_no_img = sorted(set(lbls) - set(imgs))
        for s in img_no_lbl:
            problems.append(f'[{sp}] 图片无标签: {imgs[s]}')
        for s in lbl_no_img:
            problems.append(f'[{sp}] 标签无图片: {lbls[s]}.txt')

        summary[sp] = dict(n_img=len(imgs), n_lbl=len(lbls), n_obj=n_obj, empty=empty,
                           cls=cls_counter, areas=areas)

        print(f'--- {sp} ---')
        print(f'    图片 {len(imgs)}   标签 {len(lbls)}   目标 {n_obj}')
        print(f'    类别分布: ' + (', '.join(f'{k}→{v}' for k, v in sorted(cls_counter.items())) or '无'))
        print(f'    空标签 {empty}   非法class {bad_cls}   中心越界 {bad_center}'
              f'   w<=0 {bad_w}   h<=0 {bad_h}   框越界 {oob}')
        print(f'    图片无标签 {len(img_no_lbl)}   标签无图片 {len(lbl_no_img)}')
        print()

    # ---------- 尺寸分布 ----------
    all_areas = [a for v in summary.values() for a in v['areas']]
    print('【目标尺寸分布】(按归一化面积 w*h，换算到 1920×1080 的等效像素面积)')
    small = [a for a in all_areas if a < (32 * 32) / (1920 * 1080)]
    med = [a for a in all_areas if (32 * 32) / (1920 * 1080) <= a < (96 * 96) / (1920 * 1080)]
    large = [a for a in all_areas if a >= (96 * 96) / (1920 * 1080)]
    print(f'    COCO 口径（area < 32²=1024px² 为 small）:')
    print(f'      small  (等效面积 < 1024 px²)  : {len(small):>4}  ({len(small)/max(1,len(all_areas))*100:.1f}%)')
    print(f'      medium (1024 ~ 9216 px²)      : {len(med):>4}  ({len(med)/max(1,len(all_areas))*100:.1f}%)')
    print(f'      large  (> 9216 px²)           : {len(large):>4}  ({len(large)/max(1,len(all_areas))*100:.1f}%)')
    print()

    print('【结论】')
    if not problems:
        print('    ✓ 全部检查项通过，未发现问题')
    else:
        print(f'    ✗ 共发现 {len(problems)} 个问题（前 30 条）:')
        for p in problems[:30]:
            print(f'      - {p}')
        if len(problems) > 30:
            print(f'      ... 其余 {len(problems)-30} 条省略')
    print()
    print('=' * 72)


if __name__ == '__main__':
    main()
