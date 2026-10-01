# -*- coding: utf-8 -*-
"""
数据集路径 / 标签 运行时验证器（零依赖，纯标准库）

为什么需要它：
    静态扫描（scan_legacy_api.py）只能查"API 被删了"，查不出"路径解析错了"。
    上一轮 Colab 报的 `Exception: Dataset not found ❌` 就是后者 ——
    它不在任何源码里，而在 data.yaml 的 path 语义里。

本脚本把 ultralytics/yolov5 v7.0 的两段逻辑【原样搬过来】跑一遍：

  1) utils/general.py:520-542  check_dataset 的路径解析
  2) utils/dataloaders.py      LoadImagesAndLabels 的列表文件解析
  3) utils/dataloaders.py:989  verify_image_label 的逐张校验

跑通了，就说明到了 Colab 上（把 path 换成 /content/yolo_dataset）也能跑通。

用法：
    python tools/verify_dataset_paths.py <数据集目录>
"""
import os
import posixpath
import struct
import sys

# v7.0 utils/dataloaders.py:23
IMG_FORMATS = ('bmp', 'dng', 'jpeg', 'jpg', 'mpo', 'png', 'tif', 'tiff', 'webp', 'pfm')


# ============================================================
# 极简 YAML 读取（只支持本项目 data.yaml 用到的那点语法）
# ============================================================
def load_simple_yaml(path):
    data = {}
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.split('#')[0].rstrip()
            if not line.strip():
                continue
            if ':' not in line:
                continue
            k, v = line.split(':', 1)          # 只按第一个冒号切，兼容 C:/... 这种路径
            k, v = k.strip(), v.strip()
            if v.startswith('[') and v.endswith(']'):
                v = [x.strip().strip("'\"") for x in v[1:-1].split(',') if x.strip()]
            elif v.isdigit():
                v = int(v)
            else:
                v = v.strip("'\"")
            data[k] = v
    return data


# ============================================================
# v7.0 utils/general.py:520-542  —— 路径解析（逐行照搬）
# ============================================================
def resolve_paths(data, repo_root, check_exists=True, posix=False):
    """
    返回 (resolved_data, error_msg)。

    repo_root : 相当于 Colab 的 /content/yolov5
    check_exists : False 时跳过存在性检查（用于 Windows 上模拟 Linux 路径）
    posix : True 时用 posixpath 做纯字符串解析
            （Windows 上没法表示 /content/yolo_dataset 这种无盘符绝对路径，
              所以"Colab 视角"只能做纯字符串推演，不能真的去 exists()）
    """
    join = posixpath.join if posix else os.path.join
    norm = posixpath.normpath if posix else os.path.normpath
    is_abs = (lambda s: s.startswith('/')) if posix else os.path.isabs
    exists = (lambda p: True) if not check_exists else os.path.exists

    path = data.get('path') or ''
    if not is_abs(path):
        # v7.0 原文：path = (ROOT / path).resolve()
        # ROOT 是 yolov5 仓库根，不是当前工作目录 —— 这就是上次踩的坑
        path = norm(join(repo_root, path))
        data['path'] = path
    for k in ('train', 'val', 'test'):
        if data.get(k):
            if isinstance(data[k], str):
                x = norm(join(path, data[k]))
                if not exists(x) and data[k].startswith('../'):
                    x = norm(join(path, data[k][3:]))
                data[k] = x
            else:
                data[k] = [norm(join(path, x)) for x in data[k]]

    # v7.0 原文：val 必须全部存在，否则 raise Exception('Dataset not found ❌')
    val = data.get('val')
    if val:
        vals = val if isinstance(val, list) else [val]
        missing = [x for x in vals if not exists(x)]
        if missing:
            return data, f"Dataset not found ❌  缺失: {missing}"
    return data, None


# ============================================================
# v7.0 utils/dataloaders.py  —— 列表文件解析
# ============================================================
def read_list_file(p):
    """v7.0 原文：
        parent = str(p.parent) + os.sep
        f += [x.replace('./', parent) if x.startswith('./') else x for x in t]
    """
    with open(p, 'r', encoding='utf-8') as f:
        lines = [x for x in f.read().strip().splitlines() if x]
    parent = os.path.dirname(os.path.abspath(p)) + os.sep
    return [x.replace('./', parent) if x.startswith('./') else x for x in lines]


def gather_files(p):
    """v7.0 LoadImagesAndLabels 里根据 train/val 取值收集图片列表"""
    if os.path.isdir(p):
        out = []
        for root, _, files in os.walk(p):
            for fn in files:
                if os.path.splitext(fn)[1].lower().lstrip('.') in IMG_FORMATS:
                    out.append(os.path.join(root, fn))
        return sorted(out)
    if os.path.isfile(p):
        return read_list_file(p)
    return []


# ============================================================
# 图像头解析（纯标准库，替代 PIL）
# ============================================================
def img_info(path):
    """返回 (fmt, w, h)；解析失败返回 (None, None, None)"""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                w, h = struct.unpack('>II', head[16:24])
                return 'png', w, h
            if head[:2] == b'\xff\xd8':
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b'\xff':
                        b = f.read(1)
                    if not b:
                        return None, None, None
                    marker = f.read(1)
                    while marker == b'\xff':
                        marker = f.read(1)
                    if not marker:
                        return None, None, None
                    m = marker[0]
                    if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                        continue
                    ln = struct.unpack('>H', f.read(2))[0]
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        d = f.read(5)
                        h, w = struct.unpack('>HH', d[1:5])
                        return 'jpeg', w, h
                    f.seek(ln - 2, 1)
            if head[:2] == b'BM':
                w, h = struct.unpack('<ii', head[18:26])
                return 'bmp', abs(w), abs(h)
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                return 'webp', None, None
    except Exception:
        return None, None, None
    return None, None, None


def jpeg_ends_ok(path):
    """v7.0 verify_image_label: 读最后 2 字节，必须是 \\xff\\xd9，否则算 corrupt JPEG"""
    try:
        with open(path, 'rb') as f:
            f.seek(-2, 2)
            return f.read() == b'\xff\xd9'
    except OSError:
        return False


def jpeg_exif_orientation(path):
    """
    找 JPEG 的 EXIF Orientation 标签（0x0112）。
    这个值 > 1 时，v7.0 的 exif_transpose() 才会去用 Image.ROTATE_* 常量
    —— 也就是 Pillow 10 删掉的那批。返回 None 表示没有/解析失败。
    """
    try:
        with open(path, 'rb') as f:
            if f.read(2) != b'\xff\xd8':
                return None
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
                if m == 0xDA:      # SOS，后面是压缩数据
                    return None
                if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                    continue
                ln = struct.unpack('>H', f.read(2))[0]
                seg = f.read(ln - 2)
                if m == 0xE1 and seg[:6] == b'Exif\x00\x00':
                    return _tiff_orientation(seg[6:])
    except Exception:
        return None
    return None


def _tiff_orientation(t):
    if len(t) < 8:
        return None
    if t[:2] == b'II':
        e = '<'
    elif t[:2] == b'MM':
        e = '>'
    else:
        return None
    off = struct.unpack(e + 'I', t[4:8])[0]
    if off + 2 > len(t):
        return None
    n = struct.unpack(e + 'H', t[off:off + 2])[0]
    for i in range(n):
        base = off + 2 + i * 12
        if base + 12 > len(t):
            return None
        tag, typ, cnt = struct.unpack(e + 'HHI', t[base:base + 8])
        if tag == 0x0112:
            val = struct.unpack(e + 'H', t[base + 8:base + 10])[0]
            return val
    return None


def img2label_paths(im):
    """
    v7.0 utils/dataloaders.py 原文：
        sa, sb = f'{os.sep}images{os.sep}', f'{os.sep}labels{os.sep}'
        return [sb.join(x.rsplit(sa, 1)).rsplit('.', 1)[0] + '.txt' for x in img_paths]

    注意 sa/sb 用的是 os.sep。本脚本要跑在 Windows 上做本地验证，
    而 read_list_file 的 x.replace('./', parent) 会产出 `...\\images/train/x.jpg`
    这种混合分隔符，rsplit('\\images\\') 匹配不上 —— 所以这里先统一成 os.sep。
    （Colab 是 Linux，路径本来就全是 '/'，不存在这个问题。）
    """
    im = im.replace('/', os.sep)
    sa, sb = f'{os.sep}images{os.sep}', f'{os.sep}labels{os.sep}'
    return sb.join(im.rsplit(sa, 1)).rsplit('.', 1)[0] + '.txt'


# ============================================================
# v7.0 utils/dataloaders.py:989 verify_image_label —— 逐条照搬
# ============================================================
def verify_pair(im_file, lb_file, nc):
    """返回 (状态, 说明)  状态 ∈ {'ok','empty','nolabel','corrupt'}"""
    fmt, w, h = img_info(im_file)
    if fmt is None:
        return 'corrupt', '图像头解析失败 / 文件损坏'
    if fmt not in IMG_FORMATS:
        return 'corrupt', f'格式 {fmt} 不在 IMG_FORMATS'
    if w is not None and (w <= 9 or h <= 9):
        return 'corrupt', f'尺寸 {w}x{h} < 10 像素'
    if fmt == 'jpeg' and not jpeg_ends_ok(im_file):
        return 'corrupt', 'JPEG 结尾不是 FFD9（v7.0 会尝试修复并重写文件）'

    if not os.path.isfile(lb_file):
        return 'nolabel', '缺标签文件'

    with open(lb_file, 'r', encoding='utf-8', errors='replace') as f:
        raw = [x.split() for x in f.read().strip().splitlines() if x.strip()]

    if not raw:
        return 'empty', '空标签'

    for i, row in enumerate(raw, 1):
        if len(row) != 5:
            return 'corrupt', f'第 {i} 行 {len(row)} 列（应为 5）'
        try:
            vals = [float(x) for x in row]
        except ValueError:
            return 'corrupt', f'第 {i} 行有非数字'
        if any(v < 0 for v in vals):
            return 'corrupt', f'第 {i} 行有负值'
        if any(v > 1 for v in vals[1:]):
            return 'corrupt', f'第 {i} 行坐标越界 >1'
        if not float(vals[0]).is_integer():
            return 'corrupt', f'第 {i} 行 class 不是整数'
        if int(vals[0]) >= nc:
            return 'corrupt', f'第 {i} 行 class={int(vals[0])} >= nc={nc}'

    if len(set(tuple(r) for r in raw)) < len(raw):
        return 'ok', f'{len(raw)} 个框（含重复行，v7.0 会自动去重）'
    return 'ok', f'{len(raw)} 个框'


# ============================================================
def main():
    root = sys.argv[1] if len(sys.argv) > 1 else \
        r'C:\Users\fntp\Pictures\Screenshots\84\yolo_dataset'
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f'数据集目录不存在: {root}')
        return 1

    print('=' * 78)
    print(f'数据集根目录 : {root}')
    print(f'模拟仓库根   : /content/yolov5   （Colab 上的情况）')
    print('=' * 78)

    # ---- 反例演示：这正是上次报 Dataset not found 的原因 ----
    print('\n### 反例：如果 data.yaml 写成 `path: .`（第 1.5 步没跑）会怎样')
    bad = resolve_paths({'path': '.', 'train': 'images/train', 'val': 'images/val'},
                        repo_root='/content/yolov5', check_exists=False, posix=True)[0]
    print(f'  path: .           →  {bad["path"]}')
    print(f'  val:  images/val  →  {bad["val"]}')
    print('  ✗ 被拼到了 yolov5 仓库根上，/content/yolov5/images/val 当然不存在')
    print('  ✓ 所以第 1.5 步把 path 改写成 /content/yolo_dataset 是必需的')

    overall_ok = True
    exif_rotated = []
    all_stats = {}

    for yname in ('data.yaml', 'data_nomeonly.yaml'):
        ypath = os.path.join(root, yname)
        if not os.path.isfile(ypath):
            print(f'\n[{yname}] 不存在，跳过')
            continue

        print(f'\n{"=" * 78}\n### {yname}\n{"=" * 78}')
        data = load_simple_yaml(ypath)
        print(f'原始配置: {data}')

        # 关键演示：把 path 换成 Colab 上的位置，纯字符串推演 v7.0 会解析成什么
        colab_data = dict(data)
        colab_data['path'] = '/content/yolo_dataset'
        resolved, err = resolve_paths(colab_data, repo_root='/content/yolov5',
                                      check_exists=False, posix=True)
        print('\n--- 按 v7.0 逻辑解析后（Colab 视角，纯字符串推演）---')
        for k in ('path', 'train', 'val', 'test'):
            print(f'  {k:<6} = {resolved.get(k)}')
        print('  （路径里 /content/yolo_dataset 是绝对路径，不会被拼上 /content/yolov5）')
        print('  → 只要第 1.5 步跑过，val 就存在，不会抛 Dataset not found')

        # 本地视角：把 path 换回真实目录，真的去检查文件
        local = dict(data)
        local['path'] = root
        local, err = resolve_paths(local, repo_root='/content/yolov5')
        if err:
            print(f'\n  ✗ 本地视角解析失败: {err}')
            overall_ok = False
            continue
        print(f'\n  本地视角 path = {local["path"]}  （存在性检查已通过）')

        nc = resolved.get('nc', 1)
        print(f'\n--- 逐张校验（v7.0 verify_image_label 的逻辑）---')
        for sp in ('train', 'val', 'test'):
            target = local.get(sp)
            if not target:
                continue
            files = gather_files(target)
            st = {'ok': 0, 'empty': 0, 'nolabel': 0, 'corrupt': 0}
            nobj = 0
            msgs = []
            for im in files:
                lb = img2label_paths(im)
                s, m = verify_pair(im, lb, nc)
                st[s] += 1
                if s == 'ok' and '个框' in m:
                    try:
                        nobj += int(m.split(' ')[0])
                    except ValueError:
                        pass
                if s != 'ok':
                    msgs.append(f'{os.path.basename(im)}: {m}')
                if s == 'ok' and im.lower().endswith(('.jpg', '.jpeg')):
                    o = jpeg_exif_orientation(im)
                    if o and o > 1:
                        exif_rotated.append((im, o))

            all_stats[(yname, sp)] = (len(files), nobj, st)
            flag = '✓' if st['corrupt'] == 0 and st['nolabel'] == 0 else '✗'
            if st['corrupt'] or st['nolabel']:
                overall_ok = False
            print(f'  {flag} {sp:<6} 图片 {len(files):>4}  目标 {nobj:>4}  '
                  f'ok {st["ok"]:>4}  空标签 {st["empty"]:>2}  '
                  f'缺标签 {st["nolabel"]:>2}  损坏 {st["corrupt"]:>2}')
            for m in msgs[:6]:
                print(f'        - {m}')
            if len(msgs) > 6:
                print(f'        ... 还有 {len(msgs) - 6} 条')

    # ---- EXIF 汇总（决定 Pillow 补丁对我们这份数据是否真的必要）----
    print(f'\n{"=" * 78}\n### EXIF Orientation 检查\n{"=" * 78}')
    if exif_rotated:
        print(f'⚠️ {len(exif_rotated)} 张 JPEG 的 EXIF Orientation > 1')
        print('   → 这些图会让 v7.0 的 exif_transpose() 走到 Image.ROTATE_* / FLIP_* 分支')
        print('   → 也就是 Pillow 10 删掉的那批常量（补丁已改写为 Image.Transpose.*）')
        for p, o in exif_rotated[:8]:
            print(f'     orientation={o}  {os.path.basename(p)}')
        print('   注意：训练时 verify_image_label 只读 exif_size()，不调用 exif_transpose()，')
        print('         所以【训练不受影响】；但之后跑 detect.py 推理会走到，补丁已覆盖。')
    else:
        print('✓ 没有任何图片带 EXIF 旋转信息 —— Pillow 那处补丁对当前数据不触发')

    print(f'\n{"=" * 78}')
    print('结论:', '✅ 路径与标签全部就绪，Colab 上不会再报 Dataset not found'
          if overall_ok else '❌ 有问题，见上面明细')
    print('=' * 78)
    return 0 if overall_ok else 1


if __name__ == '__main__':
    sys.exit(main())
