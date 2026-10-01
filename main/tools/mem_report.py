# -*- coding: utf-8 -*-
"""一个 engine 跑起来到底吃掉多少内存和显存。诊断用脚本。

分阶段量，每阶段打印"这一刻用了多少"，相邻两阶段的差就是这一步的增量：

    阶段 0  裸解释器，什么都还没 import
    阶段 1  import numpy / tensorrt / cuda-python / PIL
    阶段 2  import 本项目的 component 包
    阶段 3  建 CUDA 上下文（cudaSetDevice）
    然后对每个 engine：反序列化 -> 建 context -> 分配 IO 缓冲 -> 跑 N 次

为什么要用 nvidia-smi 和 cudaMemGetInfo 两个来源？
    cudaMemGetInfo 是"整卡还剩多少"，nvidia-smi 是"整卡用了多少"。
    两个视角对不上，说明桌面上还有别的程序在吃显存。
    单看 cudaMemGetInfo 会把别人的占用算到自己头上。

重要：阶段 1、2 的 import 必须写在 main() 里面，不能提到文件头。
    文件头一旦 import numpy，进程一启动就把它吃进内存了，
    阶段 0 那个"裸解释器"的基线就再也量不准。

具体怎么量在 main/component/engine_probe.py，这里只负责打印。

用法：
    python main\\tools\\mem_report.py --engine weights\\best_fp16.engine
    python main\\tools\\mem_report.py --engine weights\\best_fp16.engine weights\\best_fp32.engine
"""

import argparse
import os
import sys

# 直接运行本文件时，项目根目录不在搜索路径里，手动加进去才能 import main 包。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.meminfo import (  # noqa: E402
    fmt_mb,
    mb,
    nvsmi_used,
    rss,
)

LINE = '=' * 64


# ----------------------------------------------------------------------
# 打印小工具
# ----------------------------------------------------------------------

def stage(n, title):
    """打印一个阶段的标题。"""
    print(LINE)
    print(f'阶段 {n} · {title}')
    print(LINE)


def cpu(cur, diff=None, peak=None):
    """打印一行 CPU 占用。diff 是"比上一阶段多了多少字节"。"""
    text = f'  CPU {fmt_mb(cur):>9}'
    if diff is not None:
        text += f'   ({delta_mb(diff)})'
    if peak is not None:
        text += f'   峰值 {fmt_mb(peak)}'
    print(text)


def delta_mb(nbytes):
    """字节差 -> '+12.3 MB'。"""
    return f'{mb(nbytes):+.1f} MB'


def delta_opt(cur, base):
    """两个字节数相减；任何一个取不到（nvidia-smi 失败）就写'未知'。"""
    if cur is None or base is None:
        return '未知'
    return delta_mb(cur - base)


# ----------------------------------------------------------------------
# 单个 engine 的报告
# ----------------------------------------------------------------------

def report_one(m, c_comp):
    """把一个 engine 的测量结果打印成一整块。数字全部来自 engine_probe。"""
    L, C, R = m['load'], m['ctx'], m['run']

    print('-' * 64)
    print(f'{m["name"]}   文件 {m["file_mb"]:.1f} MB')
    print('-' * 64)

    print(f'  反序列化  {m["load_ms"]:.0f} ms')
    print(f'    CPU {fmt_mb(L["cpu"]):>9} ({delta_mb(L["cpu_delta"])})   '
          f'GPU {fmt_mb(L["gpu"]):>9}  <- 权重')

    print('  建 execution context')
    print(f'    CPU {fmt_mb(C["cpu"]):>9} ({delta_mb(C["cpu_delta"])})   '
          f'GPU {fmt_mb(C["gpu"]):>9}  <- 激活 / scratch')

    print('  设备缓冲 + 跑若干次推理')
    print(f'    CPU {fmt_mb(R["cpu"]):>9} (峰值 {fmt_mb(R["cpu_peak"])})   '
          f'GPU {fmt_mb(R["gpu"]):>9}  <- IO 缓冲')
    print(f'    输入缓冲 {R["in_bytes"] / 1e6:.1f} MB   '
          f'输出缓冲 {R["out_bytes"] / 1e6:.1f} MB（两端各一份）')
    print()

    total_gpu = L['gpu'] + C['gpu'] + R['gpu']
    print('  ══> 这个 engine 独占')
    print(f'      GPU {mb(total_gpu):6.0f} MB = 权重 {mb(L["gpu"]):.0f}'
          f' + 激活 {mb(C["gpu"]):.0f} + IO {mb(R["gpu"]):.0f}')
    print(f'      CPU 累计 {mb(R["cpu"]):.0f} MB'
          f'（较 import 完 component 增量 {delta_mb(R["cpu"] - c_comp)}）')
    print()


# ----------------------------------------------------------------------
# 主体
# ----------------------------------------------------------------------

def main():
    a = _parse_args()

    # ---- 阶段 0：裸解释器 ----
    stage(0, '裸解释器')
    c_base = rss()
    cpu(c_base)
    used0 = nvsmi_used()
    print(f'  整卡已用 {fmt_mb(used0)}（含桌面其它程序，还不是我们的）')
    print()

    # ---- 阶段 1：第三方库（必须在函数体里 import，见文件头说明）----
    import numpy as np          # noqa: F401
    import tensorrt as trt      # noqa: F401
    from cuda.bindings import runtime as cudart
    from PIL import Image

    c_imp = rss()
    stage(1, 'import numpy / tensorrt / cuda-python / PIL')
    cpu(c_imp, c_imp - c_base)
    print()

    # ---- 阶段 2：本项目组件 ----
    from main.component.engine_probe import probe_engine
    from main.component.preprocess import preprocess

    c_comp = rss()
    stage(2, 'import main.component')
    cpu(c_comp, c_comp - c_imp)
    print()

    # ---- 阶段 3：CUDA 上下文 ----
    total = cudart.cudaMemGetInfo()[2]
    cudart.cudaSetDevice(0)
    free_now = cudart.cudaMemGetInfo()[1]
    used1 = nvsmi_used()

    stage(3, '建 CUDA 上下文（cudaSetDevice）')
    print(f'  总显存 {fmt_mb(total)}')
    print(f'  整卡已用 {fmt_mb(used1)}   (较阶段 0 {delta_opt(used1, used0)})')
    if used0:
        print(f'  本进程上下文约 {fmt_mb(total - free_now - used0)}')
    print()

    # 一张假图，只是为了让输入张量有内容，尺寸跟真实截图一致
    img = Image.new('RGB', (1920, 1080), (60, 60, 60))
    blob, _r, _pad = preprocess(img)

    for path in a.engine:
        report_one(probe_engine(path, blob, a.iters), c_comp)

    used2 = nvsmi_used()
    if used0 and used2:
        print(f'全部跑完并释放后，整卡已用 {fmt_mb(used2)} '
              f'(阶段 0 基线 {fmt_mb(used0)})')


def _parse_args():
    """解析命令行参数。"""
    p = argparse.ArgumentParser(
        description='分阶段量一个或多个 engine 占用的内存和显存。')
    p.add_argument('--engine', nargs='+', required=True,
                   help='一个或多个 engine 路径')
    p.add_argument('--iters', type=int, default=50,
                   help='每个 engine 跑多少次推理，默认 50')
    return p.parse_args()


if __name__ == '__main__':
    main()
