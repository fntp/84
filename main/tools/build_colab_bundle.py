# -*- coding: utf-8 -*-
"""
把「数据集 + 一键脚本 + 补丁 + notebook + 说明」打成一个 zip。

用户只上传这一个文件，就什么都有了。

    colab/yolo_all_in_one.zip
      yolo_dataset/                ← 数据集
      run_all.py                   ← 一键脚本
      patch_yolov5.py              ← 兼容补丁
      train_yolov5_colab.ipynb     ← notebook 版（想一格一格学）
      START_HERE.txt               ← 说明

解压到 /content/ 之后，结构就是：
    /content/yolo_dataset/...
    /content/run_all.py
    ...

用法：
    python tools/build_colab_bundle.py [数据集目录]
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, '..'))
COL = os.path.join(PROJ, 'colab')

DEFAULT_DATA = r'C:\Users\fntp\Pictures\Screenshots\84\yolo_dataset'
OUT = os.path.join(COL, 'yolo_all_in_one.zip')

# 放到 zip 根目录的散件（解压后直接在 /content/ 下）
EXTRA = ['run_all.py', 'patch_yolov5.py', 'train_yolov5_colab.ipynb', 'START_HERE.txt']

IMG_EXT = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')


def add_tree(zf, src_dir, arc_prefix):
    n = 0
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__', '.ipynb_checkpoints')]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src_dir).replace('\\', '/')
            zf.write(full, f'{arc_prefix}/{rel}')
            n += 1
    return n


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA
    data_dir = os.path.abspath(data_dir)
    if not os.path.isdir(data_dir):
        print(f'数据集目录不存在: {data_dir}')
        return 1

    # 打包前先自检数据集
    for sp, want in (('train', 265), ('val', 54), ('test', 138)):
        d = os.path.join(data_dir, 'images', sp)
        n = len([f for f in os.listdir(d) if f.lower().endswith(IMG_EXT)]) \
            if os.path.isdir(d) else 0
        if n != want:
            print(f'✗ images/{sp} 有 {n} 张，预期 {want} 张 —— 先确认数据集完整')
            return 1
    print('数据集自检通过（265/54/138）')

    for f in EXTRA:
        if not os.path.isfile(os.path.join(COL, f)):
            print(f'✗ 缺少 colab/{f}')
            return 1

    if os.path.exists(OUT):
        os.remove(OUT)

    print(f'打包中 → {OUT}')
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        n_data = add_tree(zf, data_dir, 'yolo_dataset')
        print(f'  yolo_dataset/  {n_data} 个文件')
        for f in EXTRA:
            zf.write(os.path.join(COL, f), f)
            print(f'  {f}')

    size = os.path.getsize(OUT)
    print(f'\n完成: {OUT}')
    print(f'  {size / 1024 / 1024:.1f} MB')

    # 回读校验
    with zipfile.ZipFile(OUT) as zf:
        names = zf.namelist()
        print(f'  条目数: {len(names)}')
        tops = sorted({n.split('/')[0] for n in names})
        print(f'  顶层: {tops}')
        for f in EXTRA:
            assert f in names, f'zip 里缺 {f}'
        for f in ('yolo_dataset/data.yaml', 'yolo_dataset/data_nomeonly.yaml',
                  'yolo_dataset/train_no_me_only.txt'):
            assert f in names, f'zip 里缺 {f}'
        bad = zf.testzip()
        if bad:
            print(f'  ✗ zip 损坏: {bad}')
            return 1
        print('  ✓ 结构校验通过，zip 完整性 OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
