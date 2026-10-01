# -*- coding: utf-8 -*-
"""
只读数据集扫描工具 —— 绝不创建/修改/删除任何文件

用法:
    python scan_dataset.py "C:\\Users\\fntp\\Pictures\\Screenshots\\84"

输出:
    1. 总体统计（目录数 / 各类文件数 / 总大小）
    2. 目录树（只到"有文件的目录"层级，标注图片数与标签情况）
    3. 数据集结构识别（images/labels, train/val/test, data.yaml, COCO json）
    4. 图片分辨率分布
    5. 疑似数据集分组
"""
import os
import sys
import struct
import json
from collections import Counter, defaultdict

IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif', '.tif', '.tiff', '.jfif'}
ARCH_EXT = {'.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.xz', '.bz2'}
YAML_EXT = {'.yaml', '.yml'}
SPLIT_NAMES = {'train', 'val', 'valid', 'validation', 'test', 'testing'}


# ---------- 图片尺寸读取（不依赖 PIL，直接读文件头） ----------
def img_size(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                w, h = struct.unpack('>II', head[16:24])
                return w, h
            if head[:2] == b'\xff\xd8':
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b'\xff':
                        b = f.read(1)
                    if not b:
                        return None
                    marker = f.read(1)
                    while marker == b'\xff':
                        marker = f.read(1)
                    if not marker:
                        return None
                    m = marker[0]
                    if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                        continue
                    ln = struct.unpack('>H', f.read(2))[0]
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        data = f.read(5)
                        h, w = struct.unpack('>HH', data[1:5])
                        return w, h
                    f.seek(ln - 2, 1)
            if head[:2] == b'BM':
                w, h = struct.unpack('<ii', head[18:26])
                return abs(w), abs(h)
            if head[:6] in (b'GIF87a', b'GIF89a'):
                w, h = struct.unpack('<HH', head[6:10])
                return w, h
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                if head[12:16] == b'VP8X':
                    w = int.from_bytes(head[24:27], 'little') + 1
                    h = int.from_bytes(head[27:30], 'little') + 1
                    return w, h
                if head[12:16] == b'VP8 ':
                    w = struct.unpack('<H', head[26:28])[0] & 0x3FFF
                    h = struct.unpack('<H', head[28:30])[0] & 0x3FFF
                    return w, h
    except Exception:
        return None
    return None


def is_yolo_label(path):
    """判断 .txt 是否是 YOLO 标签：每行 5 列，首列是整数，其余是浮点"""
    try:
        if os.path.getsize(path) == 0:
            return 'empty'
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            return 'empty'
        for l in lines[:20]:
            p = l.split()
            if len(p) < 5:
                return None
            int(p[0])
            for v in p[1:5]:
                float(v)
        return 'yolo'
    except Exception:
        return None


def rel(root, p):
    return os.path.relpath(p, root).replace('\\', '/')


def main():
    if len(sys.argv) < 2:
        print('用法: python scan_dataset.py <目录>')
        return
    root = os.path.abspath(sys.argv[1])
    if not os.path.isdir(root):
        print(f'[错误] 目录不存在: {root}')
        return

    print('=' * 72)
    print('只读扫描报告（未修改任何文件）')
    print('=' * 72)
    print(f'扫描根目录: {root}')
    print()

    # ---------- 遍历 ----------
    dirs = []
    ext_counter = Counter()
    total_size = 0
    dir_images = defaultdict(list)      # dir -> [img paths]
    dir_other = defaultdict(list)       # dir -> [(name, ext, path)]
    all_files = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirs.append(dirpath)
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                sz = 0
            total_size += sz
            ext = os.path.splitext(fn)[1].lower()
            ext_counter[ext] += 1
            all_files.append(fp)
            if ext in IMG_EXT:
                dir_images[dirpath].append(fp)
            else:
                dir_other[dirpath].append((fn, ext, fp))

    # ---------- 1. 总体统计 ----------
    n_img = sum(len(v) for v in dir_images.values())
    print('【1. 总体统计】')
    print(f'  目录总数          : {len(dirs)}')
    print(f'  文件总数          : {len(all_files)}')
    print(f'  总大小            : {total_size / 1024 / 1024:.1f} MB')
    print(f'  图片总数          : {n_img}')
    for ext in ['.png', '.jpg', '.jpeg', '.jfif', '.bmp', '.webp', '.gif', '.tif', '.tiff']:
        if ext_counter.get(ext):
            print(f'    {ext:<8}         : {ext_counter[ext]}')
    other_img = sum(v for k, v in ext_counter.items() if k in IMG_EXT) - sum(
        ext_counter.get(e, 0) for e in ['.png', '.jpg', '.jpeg', '.jfif', '.bmp', '.webp', '.gif', '.tif', '.tiff'])
    if other_img:
        print(f'    其他图片格式      : {other_img}')

    def cnt(group):
        return sum(ext_counter.get(e, 0) for e in group)

    print(f'  压缩包            : {cnt(ARCH_EXT)}  {[e for e in ARCH_EXT if ext_counter.get(e)]}')
    print(f'  TXT 文件          : {ext_counter.get(".txt", 0)}')
    print(f'  XML 文件          : {ext_counter.get(".xml", 0)}')
    print(f'  JSON 文件         : {ext_counter.get(".json", 0)}')
    print(f'  YAML/YML 文件     : {cnt(YAML_EXT)}')
    print(f'  CSV 文件          : {ext_counter.get(".csv", 0)}')
    print(f'  PY 文件           : {ext_counter.get(".py", 0)}')
    print(f'  IPYNB 文件        : {ext_counter.get(".ipynb", 0)}')
    print(f'  CFG/NAMES 文件    : {ext_counter.get(".cfg", 0) + ext_counter.get(".names", 0)}')
    print(f'  无扩展名文件      : {ext_counter.get("", 0)}')
    known = set(IMG_EXT) | ARCH_EXT | YAML_EXT | {'.txt', '.xml', '.json', '.csv', '.py', '.ipynb', '.cfg',
                                                  '.names', '.md', '.doc', '.docx', '.pdf', '.pt', '.engine',
                                                  '.onnx', '.weights', '.data', '.bin', '.db', '.ini', '.log', ''}
    odd = {k: v for k, v in ext_counter.items() if k not in known}
    if odd:
        print(f'  其他扩展名        : {odd}')
    print()

    # ---------- 2. 目录树 ----------
    print('【2. 目录树】  (只列出含文件的目录)')
    root_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath in sorted(dirs):
        imgs = dir_images.get(dirpath, [])
        others = dir_other.get(dirpath, [])
        if not imgs and not others:
            continue
        depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
        indent = '  ' * depth
        name = os.path.basename(dirpath) or dirpath
        tags = []
        if imgs:
            tags.append(f'{len(imgs)} 张图')
        exts = Counter(os.path.splitext(f)[1].lower() for f in imgs)
        if exts:
            tags.append(' ' .join(f'{k or "(无后缀)"}×{v}' for k, v in exts.most_common(4)))
        # 标签判断
        yolo_lbls = [p for _, e, p in others if e == '.txt']
        good = [p for p in yolo_lbls if is_yolo_label(p) == 'yolo']
        if good:
            tags.append(f'★YOLO标签×{len(good)}')
        elif yolo_lbls:
            tags.append(f'.txt×{len(yolo_lbls)}(非YOLO格式)')
        yamls = [p for _, e, p in others if e in YAML_EXT]
        if yamls:
            tags.append('有YAML')
        jsons = [p for _, e, p in others if e == '.json']
        if jsons:
            tags.append(f'JSON×{len(jsons)}')
        xmls = [p for _, e, p in others if e == '.xml']
        if xmls:
            tags.append(f'XML×{len(xmls)}')
        archs = [p for _, e, p in others if e in ARCH_EXT]
        if archs:
            tags.append(f'压缩包×{len(archs)}')
        other_n = len(others) - len(yolo_lbls) - len(yamls) - len(jsons) - len(xmls) - len(archs)
        if other_n > 0:
            tags.append(f'其他文件×{other_n}')
        print(f'{indent}{name}/   ' + '  |  '.join(tags))
    print()

    # ---------- 3. 数据集结构识别 ----------
    print('【3. 数据集结构识别】')
    has_images_dir = [d for d in dirs if os.path.basename(d).lower() == 'images']
    has_labels_dir = [d for d in dirs if os.path.basename(d).lower() == 'labels']
    has_split = [d for d in dirs if os.path.basename(d).lower() in SPLIT_NAMES]
    yaml_files = [p for p in all_files if os.path.splitext(p)[1].lower() in YAML_EXT]
    json_files = [p for p in all_files if os.path.splitext(p)[1].lower() == '.json']
    coco_like = [p for p in json_files
                 if any(k in os.path.basename(p).lower()
                        for k in ['coco', 'instance', 'annotation', 'label', '_annotations'])]
    print(f'  images/ 目录      : {len(has_images_dir)}  {[rel(root, d) for d in has_images_dir[:10]]}')
    print(f'  labels/ 目录      : {len(has_labels_dir)}  {[rel(root, d) for d in has_labels_dir[:10]]}')
    print(f'  train/val/test 目录: {len(has_split)}  {[rel(root, d) for d in has_split[:12]]}')
    print(f'  YAML 配置文件     : {len(yaml_files)}  {[rel(root, p) for p in yaml_files[:10]]}')
    print(f'  JSON 文件         : {len(json_files)}')
    print(f'  疑似 COCO/Roboflow: {len(coco_like)}  {[rel(root, p) for p in coco_like[:10]]}')
    print()

    if yaml_files:
        print('  --- data.yaml / dataset.yaml 内容 ---')
        for p in yaml_files[:6]:
            print(f'  ▶ {rel(root, p)}  ({os.path.getsize(p)} bytes)')
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f):
                        if i >= 25:
                            print('      ...')
                            break
                        print('      ' + line.rstrip())
            except Exception as e:
                print(f'      [读取失败] {e}')
            print()
    else:
        print('  ✗ 未发现 data.yaml / dataset.yaml')
        print()

    # ---------- 4. 分辨率分布 ----------
    print('【4. 图片分辨率分布】')
    res_counter = Counter()
    sample = []
    for d, imgs in dir_images.items():
        for p in imgs:
            if len(sample) < 400:
                sample.append(p)
    for p in sample:
        s = img_size(p)
        res_counter[s if s else ('未知',)] += 1
    for (w, h), c in res_counter.most_common(12):
        if w == '未知':
            print(f'  {c:>5} 张   (无法解析)')
        else:
            print(f'  {c:>5} 张   {w} × {h}')
    if len(dir_images) and sum(len(v) for v in dir_images.values()) > len(sample):
        print(f'  (抽样 {len(sample)} 张，共 {sum(len(v) for v in dir_images.values())} 张)')
    print()

    # ---------- 5. 疑似数据集分组 ----------
    print('【5. 疑似数据集分组（按目录，递归汇总）】')
    top = sorted([d for d in dirs if d != root], key=lambda x: x.count(os.sep))
    for d in top:
        if os.path.dirname(d) != root:
            continue
        n = sum(len(v) for k, v in dir_images.items() if k == d or k.startswith(d + os.sep))
        o = [f for f in all_files if f.startswith(d + os.sep)]
        if n == 0 and not o:
            continue
        yolo_n = sum(1 for f in o if f.lower().endswith('.txt') and is_yolo_label(f) == 'yolo')
        js = sum(1 for f in o if f.lower().endswith('.json'))
        ya = sum(1 for f in o if os.path.splitext(f)[1].lower() in YAML_EXT)
        ar = sum(1 for f in o if os.path.splitext(f)[1].lower() in ARCH_EXT)
        print(f'  {os.path.basename(d)}/')
        print(f'      图片 {n}  |  YOLO标签 {yolo_n}  |  JSON {js}  |  YAML {ya}  |  压缩包 {ar}')
    # 根目录直接文件
    n_root = len(dir_images.get(root, []))
    if n_root:
        o = dir_other.get(root, [])
        yolo_n = sum(1 for _, e, p in o if e == '.txt' and is_yolo_label(p) == 'yolo')
        print(f'  <根目录直接文件>')
        print(f'      图片 {n_root}  |  YOLO标签 {yolo_n}  |  其他文件 {len(o)}')
    print()

    # ---------- 6. 标签-图片配对（仅当有 YOLO 标签） ----------
    yolo_dirs = {}
    for dirpath, _, filenames in os.walk(root):
        good = [os.path.join(dirpath, f) for f in filenames
                if f.lower().endswith('.txt') and is_yolo_label(os.path.join(dirpath, f)) == 'yolo']
        if good:
            yolo_dirs[dirpath] = good
    if yolo_dirs:
        print('【6. 标签 / 图片 配对情况】')
        for d, lbls in yolo_dirs.items():
            imgs = dir_images.get(d, [])
            stems_img = {os.path.splitext(os.path.basename(p))[0] for p in imgs}
            stems_lbl = {os.path.splitext(os.path.basename(p))[0] for p in lbls}
            print(f'  {rel(root, d)}')
            print(f'      图片 {len(stems_img)}  标签 {len(stems_lbl)}'
                  f'  有标签无图片 {len(stems_lbl - stems_img)}  有图片无标签 {len(stems_img - stems_lbl)}')
        print()
    else:
        print('【6. 标签 / 图片 配对情况】')
        print('  ✗ 未发现任何 YOLO 格式标签')
        print()

    print('=' * 72)
    print('扫描结束。未创建、未修改、未删除任何文件。')
    print('=' * 72)


if __name__ == '__main__':
    main()
