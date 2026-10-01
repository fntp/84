# -*- coding: utf-8 -*-
"""
YOLO 数据集构建工具

把 Roboflow 的 enemy/me 双类数据集转换成单类 person 数据集，
输出到 <源目录>/yolo_dataset/，原始素材完全不改动。

特性:
  - 复制图片（不移动、不改名、不动原文件）
  - 类别重映射: 0(enemy) -> 0(person), 1(me) -> 0(person)
  - 生成 data.yaml
  - 生成 manifest.csv（每张图的 enemy/me 框数，供后续筛选）
  - 生成 *_no_me_only.txt 图片清单 + data_nomeonly.yaml（零成本换数据集）
  - --dry-run 只报告不写盘

用法:
    python build_yolo_dataset.py "C:\\Users\\fntp\\Pictures\\Screenshots\\84"
    python build_yolo_dataset.py "C:\\Users\\fntp\\Pictures\\Screenshots\\84" --dry-run
"""
import os
import sys
import csv
import shutil

IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.jfif'}
SPLIT_MAP = [('train', 'train'), ('valid', 'val'), ('test', 'test')]
CLASS_MERGE = {0: 0, 1: 0}      # enemy -> person, me -> person


def parse_label(path):
    """返回 [(cls, x, y, w, h), ...]"""
    out = []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            p = line.split()
            if len(p) < 5:
                continue
            out.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry = '--dry-run' in sys.argv
    if not args:
        print('用法: python build_yolo_dataset.py <源目录> [--dry-run]')
        return
    src = os.path.abspath(args[0])
    dst = os.path.join(src, 'yolo_dataset')

    print('=' * 72)
    print('YOLO 数据集构建' + ('  [DRY-RUN 不写盘]' if dry else ''))
    print('=' * 72)
    print(f'源目录: {src}')
    print(f'输出  : {dst}')
    print()

    if os.path.exists(dst) and not dry:
        print(f'[警告] 输出目录已存在: {dst}')
        print('       为安全起见，本工具不会覆盖已有目录。')
        print('       如确认要重建，请先手动改名或删除该目录。')
        return

    if not dry:
        for sp_dst in ['train', 'val', 'test']:
            os.makedirs(os.path.join(dst, 'images', sp_dst), exist_ok=True)
            os.makedirs(os.path.join(dst, 'labels', sp_dst), exist_ok=True)

    manifest = []
    stats = {}

    for sp_src, sp_dst in SPLIT_MAP:
        img_dir = os.path.join(src, sp_src, 'images')
        lbl_dir = os.path.join(src, sp_src, 'labels')
        if not os.path.isdir(img_dir):
            print(f'[跳过] {sp_src}: 无 images 目录')
            continue

        imgs = sorted(f for f in os.listdir(img_dir)
                      if os.path.splitext(f)[1].lower() in IMG_EXT)
        n_obj = 0
        n_me_only = 0
        no_me_only_list = []

        for fn in imgs:
            stem = os.path.splitext(fn)[0]
            src_img = os.path.join(img_dir, fn)
            src_lbl = os.path.join(lbl_dir, stem + '.txt')

            objs = parse_label(src_lbl) if os.path.isfile(src_lbl) else []
            n_enemy = sum(1 for o in objs if o[0] == 0)
            n_me = sum(1 for o in objs if o[0] == 1)

            if n_me > 0 and n_enemy == 0:
                n_me_only += 1
            else:
                # 记录【复制后】的目标路径，而不是原始素材路径
                no_me_only_list.append(os.path.join(dst, 'images', sp_dst, fn))

            if not dry:
                shutil.copy2(src_img, os.path.join(dst, 'images', sp_dst, fn))
                with open(os.path.join(dst, 'labels', sp_dst, stem + '.txt'),
                          'w', encoding='utf-8', newline='\n') as f:
                    for c, x, y, w, h in objs:
                        f.write(f'{CLASS_MERGE.get(c, 0)} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n')

            n_obj += len(objs)
            manifest.append(dict(split=sp_dst, image=fn, n_enemy=n_enemy, n_me=n_me,
                                 n_total=len(objs)))

        stats[sp_dst] = dict(imgs=len(imgs), obj=n_obj, me_only=n_me_only,
                             keep=len(no_me_only_list), lst=no_me_only_list)
        print(f'  {sp_src:<6} -> {sp_dst:<6}  图片 {len(imgs):>4}  目标 {n_obj:>4}'
              f'  纯 me 图 {n_me_only:>3}  排除后 {len(no_me_only_list):>4}')

    total_img = sum(v['imgs'] for v in stats.values())
    total_obj = sum(v['obj'] for v in stats.values())
    print()
    print(f'  合计: 图片 {total_img}  目标 {total_obj}')

    if dry:
        print()
        print('[DRY-RUN] 未写入任何文件。')
        return

    # ---------- data.yaml ----------
    # 注意：YOLOv5 的 data.yaml 里 path 若是相对路径，会相对【YOLOv5 仓库根目录】解析
    # (utils/general.py: path = (ROOT / path).resolve())，不是相对当前工作目录。
    # 所以这里必须写绝对路径，否则 train: images/train 会被解析成 <yolov5>/images/train。
    with open(os.path.join(dst, 'data.yaml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(f'path: {dst}\n')
        f.write('train: images/train\n')
        f.write('val: images/val\n')
        f.write('test: images/test\n\n')
        f.write('nc: 1\n')
        f.write('names: [person]\n')
    print(f'  写入 data.yaml  (path: {dst})')

    # ---------- 图片清单（排除纯 me 图） ----------
    # 条目加 './' 前缀：YOLOv5 的 LoadImagesAndLabels 会把 './' 替换成【该 txt 所在目录】，
    # 从而变成绝对路径。不加前缀的话会被当成相对当前工作目录，导致找不到文件。
    for sp_dst, v in stats.items():
        lst_path = os.path.join(dst, f'{sp_dst}_no_me_only.txt')
        with open(lst_path, 'w', encoding='utf-8', newline='\n') as f:
            for p in v['lst']:
                f.write('./' + os.path.relpath(p, dst).replace('\\', '/') + '\n')
    print(f'  写入 train/val/test_no_me_only.txt  (条目带 ./ 前缀)')

    with open(os.path.join(dst, 'data_nomeonly.yaml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(f'path: {dst}\n')
        f.write('train: train_no_me_only.txt\n')
        f.write('val: val_no_me_only.txt\n')
        f.write('test: test_no_me_only.txt\n\n')
        f.write('nc: 1\n')
        f.write('names: [person]\n')
    print(f'  写入 data_nomeonly.yaml')

    # ---------- manifest.csv ----------
    with open(os.path.join(dst, 'manifest.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['split', 'image', 'n_enemy', 'n_me', 'n_total'])
        w.writeheader()
        w.writerows(manifest)
    print(f'  写入 manifest.csv ({len(manifest)} 行)')

    # ---------- README ----------
    with open(os.path.join(dst, 'README.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('# yolo_dataset\n\n')
        f.write('由 `tools/build_yolo_dataset.py` 自动生成。原始素材未改动。\n\n')
        f.write('## 类别\n\n')
        f.write('单一类别 `person`（class_id = 0）。\n\n')
        f.write('来源映射：Roboflow 原数据集 `enemy`(0) 和 `me`(1) **全部合并为** `person`(0)。\n\n')
        f.write('> 注意：`me` 是玩家自己的角色（第三人称视角下固定在屏幕中下方），\n')
        f.write('> 合并后 66.7% 的图片只包含「自己」。若训练效果不佳，改用 `data_nomeonly.yaml`。\n\n')
        f.write('## 目录\n\n')
        f.write('```\n')
        f.write('yolo_dataset/\n')
        f.write('├── images/{train,val,test}/\n')
        f.write('├── labels/{train,val,test}/\n')
        f.write('├── data.yaml              # 全部图片\n')
        f.write('├── data_nomeonly.yaml     # 排除「纯 me 图」的版本\n')
        f.write('├── train/val/test_no_me_only.txt\n')
        f.write('├── manifest.csv           # 每图 enemy/me 框数\n')
        f.write('└── README.md\n')
        f.write('```\n\n')
        f.write('## 统计\n\n')
        f.write('| split | 图片 | 目标 |\n|---|---|---|\n')
        for sp, v in stats.items():
            f.write(f'| {sp} | {v["imgs"]} | {v["obj"]} |\n')
        f.write(f'| **合计** | **{total_img}** | **{total_obj}** |\n')
    print(f'  写入 README.md')

    print()
    print('完成。原始素材未做任何修改。')
    print('=' * 72)


if __name__ == '__main__':
    main()
