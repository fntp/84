# -*- coding: utf-8 -*-
"""
只读标签分析工具 —— 绝不创建/修改/删除任何文件

用法:
    python analyze_labels.py "C:\\Users\\fntp\\Pictures\\Screenshots\\84"

输出:
    1. 根目录非图片文件清单
    2. 每个 split 的类别分布 / 空标签 / 每图目标数
    3. 坐标合法性检查（0~1 / w>0 / h>0 / 越界）
    4. 图片 <-> 标签 配对（跨 images / labels 兄弟目录）
    5. MD5 完全重复检测
"""
import os
import sys
import hashlib
from collections import Counter, defaultdict

IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.jfif'}


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    root = os.path.abspath(sys.argv[1])
    print('=' * 72)
    print('标签深度分析（只读）')
    print('=' * 72)
    print()

    # ---------- 1. 根目录非图片文件 ----------
    print('【1. 根目录非图片文件】')
    for fn in sorted(os.listdir(root)):
        p = os.path.join(root, fn)
        if os.path.isfile(p) and os.path.splitext(fn)[1].lower() not in IMG_EXT:
            print(f'  {fn}   ({os.path.getsize(p)} bytes)')
    print()

    # ---------- 2/3. 标签统计 ----------
    splits = {}
    for sp in ['train', 'valid', 'test']:
        lbl_dir = os.path.join(root, sp, 'labels')
        img_dir = os.path.join(root, sp, 'images')
        if not os.path.isdir(lbl_dir):
            continue
        cls_counter = Counter()
        obj_counts = []
        empty = 0
        bad_norm = 0
        bad_wh = 0
        out_of_range = 0
        total_obj = 0
        n_lbl = 0
        n_img = 0
        if os.path.isdir(img_dir):
            n_img = len([f for f in os.listdir(img_dir)
                         if os.path.splitext(f)[1].lower() in IMG_EXT])
        for fn in os.listdir(lbl_dir):
            if not fn.lower().endswith('.txt'):
                continue
            n_lbl += 1
            p = os.path.join(lbl_dir, fn)
            with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [l.strip() for l in f if l.strip()]
            if not lines:
                empty += 1
                obj_counts.append(0)
                continue
            obj_counts.append(len(lines))
            for l in lines:
                parts = l.split()
                if len(parts) < 5:
                    continue
                try:
                    c = int(parts[0])
                    x, y, w, h = (float(v) for v in parts[1:5])
                except ValueError:
                    continue
                cls_counter[c] += 1
                total_obj += 1
                if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    bad_norm += 1
                if w <= 0 or h <= 0:
                    bad_wh += 1
                if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
                    out_of_range += 1
        splits[sp] = dict(cls=cls_counter, obj=obj_counts, empty=empty, n_lbl=n_lbl,
                          n_img=n_img, bad_norm=bad_norm, bad_wh=bad_wh,
                          out_of_range=out_of_range, total_obj=total_obj)

    print('【2. 每个 split 的统计】')
    for sp, d in splits.items():
        print(f'  --- {sp} ---')
        print(f'      图片 {d["n_img"]}   标签文件 {d["n_lbl"]}   空标签 {d["empty"]}')
        print(f'      目标总数 {d["total_obj"]}')
        print(f'      类别分布: ' + ', '.join(f'{k} → {v} 个' for k, v in sorted(d['cls'].items())))
        if d['obj']:
            nonempty = [c for c in d['obj'] if c > 0]
            avg = sum(d['obj']) / len(d['obj'])
            print(f'      每图目标数: 平均 {avg:.2f}  最大 {max(d["obj"])}  最小 {min(d["obj"])}')
            if nonempty:
                print(f'      非空图平均: {sum(nonempty)/len(nonempty):.2f}')
        print()

    print('【3. 坐标合法性检查】')
    tot_bad_norm = tot_bad_wh = tot_oor = 0
    for sp, d in splits.items():
        print(f'  {sp:<6} 中心点超出[0,1]: {d["bad_norm"]}   w/h<=0: {d["bad_wh"]}   w/h 超出(0,1]: {d["out_of_range"]}')
        tot_bad_norm += d['bad_norm']
        tot_bad_wh += d['bad_wh']
        tot_oor += d['out_of_range']
    verdict = '✓ 全部合法' if (tot_bad_norm + tot_bad_wh + tot_oor) == 0 else '✗ 存在问题'
    print(f'  结论: {verdict}')
    print()

    # ---------- 4. 配对 ----------
    print('【4. 图片 <-> 标签 配对】')
    for sp, d in splits.items():
        img_dir = os.path.join(root, sp, 'images')
        lbl_dir = os.path.join(root, sp, 'labels')
        imgs = {os.path.splitext(f)[0] for f in os.listdir(img_dir)
                if os.path.splitext(f)[1].lower() in IMG_EXT} if os.path.isdir(img_dir) else set()
        lbls = {os.path.splitext(f)[0] for f in os.listdir(lbl_dir) if f.lower().endswith('.txt')} \
            if os.path.isdir(lbl_dir) else set()
        print(f'  {sp:<6} 图片 {len(imgs)}  标签 {len(lbls)}  '
              f'有标签无图 {len(lbls - imgs)}  有图无标签 {len(imgs - lbls)}')
    print()

    # ---------- 5. MD5 重复 ----------
    print('【5. MD5 完全重复检测】')
    hashes = defaultdict(list)
    all_imgs = []
    for dp, _, fns in os.walk(root):
        for f in fns:
            if os.path.splitext(f)[1].lower() in IMG_EXT:
                all_imgs.append(os.path.join(dp, f))
    for p in all_imgs:
        try:
            hashes[md5(p)].append(p)
        except OSError:
            pass
    dup_groups = {h: v for h, v in hashes.items() if len(v) > 1}
    n_dup_files = sum(len(v) for v in dup_groups.values())
    print(f'  图片总数        : {len(all_imgs)}')
    print(f'  唯一图片(MD5)   : {len(hashes)}')
    print(f'  完全重复组数    : {len(dup_groups)}')
    print(f'  重复文件数      : {n_dup_files}')
    print(f'  冗余可省        : {n_dup_files - len(dup_groups)} 张')
    if dup_groups:
        print('  --- 重复组明细 (最多 15 组) ---')
        for i, (h, v) in enumerate(list(dup_groups.items())[:15]):
            print(f'    [{i+1}] {len(v)} 个文件, md5={h[:12]}')
            for p in v:
                print(f'        {os.path.relpath(p, root)}')
    print()
    print('=' * 72)


if __name__ == '__main__':
    main()
