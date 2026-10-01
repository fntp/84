# -*- coding: utf-8 -*-
"""离线单图检测 / 多 engine 对比 / 纯推理测速。命令行工具。

真正的算法都在 main/component/ 里，这里只是外壳。
需要 numpy + Pillow + tensorrt + cuda-python，不需要 torch / opencv。

三种用法（按优先级：--compare > --bench > 单图）：

    跑单张图，打印每个框：
    python main\\tools\\run_infer.py --image G:\\path\\a.jpg

    顺便把画了框的图存下来：
    python main\\tools\\run_infer.py --image a.jpg --out out\\a_boxed.jpg

    对比 fp32 和 fp16 的输出差异（基准是 --engine 那个）：
    python main\\tools\\run_infer.py --image a.jpg --compare weights\\best_fp32.engine

    只测推理速度，不关心结果：
    python main\\tools\\run_infer.py --image a.jpg --bench --iters 200
"""

import argparse
import os
import sys
import time

import numpy as np

# 直接运行本文件时（python main\tools\run_infer.py），
# 项目根目录不在搜索路径里，手动加进去才能 import main 包。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.detector import Detector, engine_size_mb   # noqa: E402
from main.component.meminfo import fmt_mb, gpu_free_bytes      # noqa: E402
from main.component.postprocess import postprocess             # noqa: E402
from main.component.preprocess import preprocess               # noqa: E402
from main.component.visualize import draw, load_image          # noqa: E402
from main.config import DEFAULT_CONF, DEFAULT_ENGINE, DEFAULT_IOU  # noqa: E402


def cmd_single(a):
    """跑一张图，把检测框打印出来，需要的话存一张画了框的图。"""
    img = load_image(a.image)
    det = Detector(a.engine)
    print(f'engine {os.path.basename(a.engine)}  {det.describe()}')

    t0 = time.perf_counter()
    boxes, scores, _classes = det.infer(img, a.conf, a.iou)
    dt = (time.perf_counter() - t0) * 1000

    print(f'原图 {img.size[0]}x{img.size[1]}  推理 {dt:.1f} ms  '
          f'检出 {len(boxes)} 个')
    for (x1, y1, x2, y2), s in zip(boxes, scores):
        print(f'  person {s:.3f}  '
              f'[{x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f}]  '
              f'{x2 - x1:.0f}x{y2 - y1:.0f}')

    if a.out:
        draw(img, boxes, scores).save(a.out)
        print(f'已存: {a.out}')

    det.close()
    img.close()


def _print_diff(name, base, other):
    """打印 other 相对 base 的原始输出差异。纯诊断用。"""
    diff = np.abs(other - base)
    rel = diff.max() / max(float(np.abs(base).max()), 1e-9) * 100

    print(f'  {name}')
    print(f'    max|Δ| {diff.max():.6f}   mean|Δ| {diff.mean():.6f}   '
          f'相对 max|Δ| {rel:.4f}%')
    for col, label in enumerate(('cx', 'cy', 'w', 'h', 'obj', 'cls')):
        print(f'    {label:>3} max|Δ| {diff[..., col].max():.6f}')


def cmd_compare(a):
    """把每个 --compare 的 engine 跟 --engine 基准逐列比一下差异。

    用来确认 fp16 量化后精度掉得可不可接受。
    """
    img = load_image(a.image)
    blob, _r, _pad = preprocess(img)

    det0 = Detector(a.engine)
    r0 = det0.raw_infer(blob).copy()
    b0, s0, _c0 = postprocess(r0, a.conf, a.iou)
    print(f'基准 {os.path.basename(a.engine)}  原始输出形状 {r0.shape}  '
          f'检出 {len(b0)} 个')

    for path in a.compare:
        det = Detector(path)
        r1 = det.raw_infer(blob).copy()
        _print_diff(os.path.basename(path), r0, r1)

        b1, s1, _c1 = postprocess(r1, a.conf, a.iou)
        print(f'    检出数 {len(b0)} -> {len(b1)}')
        if len(b0) and len(b0) == len(b1):
            print(f'    框坐标 max|Δ| {np.abs(b1 - b0).max():.2f} px   '
                  f'置信度 max|Δ| {np.abs(s1 - s0).max():.4f}')
        det.close()

    det0.close()
    img.close()


def cmd_bench(a):
    """只测推理速度。测的是 engine + 显存拷贝，不含抓屏和前处理。"""
    img = load_image(a.image)
    blob, _r, _pad = preprocess(img)
    det = Detector(a.engine)

    # 前 10 次叫 warmup：第一次要建上下文、显存要热身，明显偏慢，不能算进统计
    for _ in range(10):
        det.raw_infer(blob)

    free_before = gpu_free_bytes()
    ts = np.array([_time_once(det, blob) for _ in range(a.iters)])

    print(f'engine {os.path.basename(a.engine)}  '
          f'{engine_size_mb(a.engine):.1f} MB')
    print(f'迭代 {a.iters} 次（已 warmup 10 次）')
    print(f'mean {ts.mean():.2f} ms   median {np.median(ts):.2f} ms   '
          f'p95 {np.percentile(ts, 95):.2f} ms   min {ts.min():.2f} ms')
    print(f'=> {1000 / ts.mean():.1f} FPS（含 host<->device 拷贝）')
    print(f'显存剩余 {fmt_mb(free_before)}')

    det.close()
    img.close()


def _time_once(det, blob):
    """跑一次 raw_infer，返回耗时毫秒。"""
    t0 = time.perf_counter()
    det.raw_infer(blob)
    return (time.perf_counter() - t0) * 1000


def main():
    """解析参数并按优先级分发。"""
    p = argparse.ArgumentParser(
        description='离线单图检测 / engine 对比 / 测速。'
                    '注意：--engine 是基准，--compare 是要跟它比的其它 engine。')
    p.add_argument('--engine', default=DEFAULT_ENGINE, help='engine 路径')
    p.add_argument('--image', required=True, help='输入图片路径')
    p.add_argument('--out', default=None, help='把画了框的图存到这里（仅单图模式）')
    p.add_argument('--conf', type=float, default=DEFAULT_CONF, help='置信度阈值')
    p.add_argument('--iou', type=float, default=DEFAULT_IOU, help='NMS 阈值')
    p.add_argument('--compare', nargs='+', metavar='ENGINE', default=None,
                   help='要跟 --engine 对比的其它 engine，可以给多个')
    p.add_argument('--bench', action='store_true', help='只测速，不看结果')
    p.add_argument('--iters', type=int, default=200, help='测速跑多少次')
    a = p.parse_args()

    if a.compare:
        cmd_compare(a)
    elif a.bench:
        cmd_bench(a)
    else:
        cmd_single(a)


if __name__ == '__main__':
    main()
