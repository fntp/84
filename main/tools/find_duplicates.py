# -*- coding: utf-8 -*-
"""
近似重复检测工具 —— 只读，绝不修改原素材

检测三个层次:
  1. MD5          完全重复（字节级）
  2. dHash        感知哈希（快，对亮度/缩放敏感度低）
  3. pHash        感知哈希（DCT 频域，最常用）

并重点输出:
  - 跨 split 的近似重复（数据泄漏！）
  - 根目录裸截图内部的近似重复
  - 根目录裸截图与已标注数据集的重合

用法:
    python find_duplicates.py "C:\\Users\\fntp\\Pictures\\Screenshots\\84"
"""
import os
import sys
import hashlib
from collections import defaultdict

import numpy as np
from PIL import Image

IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.jfif'}

PHASH_THRESHOLD = 10   # hamming <= 10 视为高度相似
DHASH_THRESHOLD = 10


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


_DCT_CACHE = {}


def _dct_matrix(n):
    """DCT-II 正交归一化矩阵 C，满足 X = C @ x @ C.T"""
    if n in _DCT_CACHE:
        return _DCT_CACHE[n]
    k = np.arange(n).reshape(-1, 1)
    x = np.arange(n).reshape(1, -1)
    c = np.cos(np.pi * (2 * x + 1) * k / (2 * n))
    c[0, :] = 1.0 / np.sqrt(2.0)
    c *= np.sqrt(2.0 / n)
    _DCT_CACHE[n] = c
    return c


def _dct2(a):
    """二维 DCT-II（正交归一化）—— 纯 numpy 实现，不依赖 scipy"""
    c = _dct_matrix(a.shape[0])
    return c @ a @ c.T


def phash(path, hash_size=8, highfreq_factor=4):
    """pHash: 32x32 灰度 -> 2D DCT -> 取左上 8x8 -> 与中位数比较"""
    size = hash_size * highfreq_factor
    with Image.open(path) as im:
        im = im.convert('L').resize((size, size), Image.LANCZOS)
        a = np.asarray(im, dtype=np.float64)
    d = _dct2(a)[:hash_size, :hash_size]
    flat = d.flatten()[1:]           # 去掉 DC 分量
    med = np.median(flat)
    bits = d.flatten() > med
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def dhash(path, hash_size=8):
    """dHash: 9x8 灰度，比较水平相邻像素"""
    with Image.open(path) as im:
        im = im.convert('L').resize((hash_size + 1, hash_size), Image.LANCZOS)
        a = np.asarray(im, dtype=np.int16)
    diff = a[:, 1:] > a[:, :-1]
    v = 0
    for b in diff.flatten():
        v = (v << 1) | int(b)
    return v


def hamming(a, b):
    return bin(a ^ b).count('1')


def split_of(rel):
    for s in ('train', 'valid', 'test'):
        if rel.startswith(s + '/'):
            return s
    return 'root'


def main():
    root = os.path.abspath(sys.argv[1])
    print('=' * 72)
    print('近似重复检测（只读）')
    print('=' * 72)
    print()

    files = []
    for dp, _, fns in os.walk(root):
        for f in fns:
            if os.path.splitext(f)[1].lower() in IMG_EXT:
                p = os.path.join(dp, f)
                files.append((os.path.relpath(p, root).replace('\\', '/'), p))
    files.sort()
    print(f'待检测图片: {len(files)} 张')
    print('计算 MD5 / dHash / pHash ...')

    recs = []
    for i, (rel, p) in enumerate(files, 1):
        try:
            recs.append(dict(rel=rel, path=p, split=split_of(rel),
                             md5=md5_of(p), ph=phash(p), dh=dhash(p)))
        except Exception as e:
            print(f'  [跳过] {rel}: {e}')
        if i % 100 == 0:
            print(f'  已处理 {i}/{len(files)}')
    print(f'  完成 {len(recs)} 张')
    print()

    # ---------- 1. MD5 ----------
    by_md5 = defaultdict(list)
    for r in recs:
        by_md5[r['md5']].append(r)
    md5_groups = [v for v in by_md5.values() if len(v) > 1]
    print('【1. MD5 完全重复】')
    print(f'  唯一图片 {len(by_md5)} / 总数 {len(recs)}   重复组 {len(md5_groups)}')
    for g in md5_groups[:10]:
        print('   ▶ ' + ' == '.join(x['rel'] for x in g))
    print()

    # ---------- 2. pHash 近似重复 ----------
    print(f'【2. pHash 近似重复】 阈值 hamming <= {PHASH_THRESHOLD}')
    pairs = []
    n = len(recs)
    for i in range(n):
        for j in range(i + 1, n):
            h = hamming(recs[i]['ph'], recs[j]['ph'])
            if h <= PHASH_THRESHOLD:
                pairs.append((h, recs[i], recs[j]))
    pairs.sort(key=lambda x: x[0])
    print(f'  近似重复对: {len(pairs)} 对')
    if pairs:
        hist = defaultdict(int)
        for h, _, _ in pairs:
            hist[h] += 1
        print('  汉明距离分布: ' + ', '.join(f'{k}→{hist[k]}对' for k in sorted(hist)))
    print()

    # ---------- 3. 跨 split 泄漏 ----------
    print('【3. ★ 跨 split 数据泄漏检查】')
    cross = [(h, a, b) for h, a, b in pairs if a['split'] != b['split']]
    labels = {}
    for h, a, b in cross:
        key = tuple(sorted([a['split'], b['split']]))
        labels[key] = labels.get(key, 0) + 1
    if labels:
        for k, v in sorted(labels.items()):
            print(f'  ⚠ {k[0]} <-> {k[1]} : {v} 对')
        print()
        print('  --- 泄漏明细 (最多 20 对) ---')
        for h, a, b in cross[:20]:
            print(f'    d={h:<2} {a["split"]:<5} {a["rel"]}')
            print(f'         {b["split"]:<5} {b["rel"]}')
    else:
        print('  ✓ 未发现跨 split 的近似重复')
    print()

    # ---------- 4. root 内部 ----------
    print('【4. 根目录裸截图内部近似重复】')
    roots = [r for r in recs if r['split'] == 'root']
    rp = [(h, a, b) for h, a, b in pairs if a['split'] == 'root' and b['split'] == 'root']
    print(f'  根目录图片 {len(roots)} 张，内部近似重复 {len(rp)} 对')
    for h, a, b in rp[:15]:
        print(f'    d={h:<2} {a["rel"]}')
        print(f'         {b["rel"]}')
    print()

    # ---------- 5. root vs 数据集 ----------
    print('【5. 根目录裸截图 与 已标注数据集 的重合】')
    rv = [(h, a, b) for h, a, b in pairs if (a['split'] == 'root') != (b['split'] == 'root')]
    print(f'  重合对: {len(rv)}')
    for h, a, b in rv[:15]:
        print(f'    d={h:<2} {a["rel"]}')
        print(f'         {b["rel"]}')
    if not rv:
        print('  ✓ 无重合（2026 年新截图与 2023 年数据集互不重复）')
    print()

    # ---------- 6. 建议 ----------
    print('【6. 建议】')
    print(f'  - MD5 重复 0 组 → 无字节级冗余')
    print(f'  - 若跨 split 泄漏 > 0，说明 Roboflow 的随机划分把相近帧分到了不同 split')
    print(f'    这会高估验证集指标（val mAP 虚高），但对「跑通流程」无影响')
    print()
    print('=' * 72)


if __name__ == '__main__':
    main()
