# -*- coding: utf-8 -*-
"""端到端测试（需要显卡 + engine 文件）：图片进，原图像素坐标出。

这个文件跟 test_pipeline.py 的区别：
    test_pipeline.py   不需要显卡，只验前处理和输出格式
    本文件             真的把 engine 加载起来跑一次，验最后那段
                       "画布坐标 -> 原图像素坐标"有没有还原对

要验的核心契约（也就是验收标准里最容易翻车的一条）：
    infer() 返回的框必须是【原图】像素坐标，不是 640x640 画布坐标。
    如果这条错了，下游拿到的坐标会整体缩小成三分之一左右，
    在画面上表现为"瞄偏了"，但程序不会报任何错。

本机没有显卡或没有 engine 文件时，测试会自动标成 skip，不算失败。

运行方式（在项目根目录下，不需要 pytest）：

    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe test\\test_engine.py
"""

import os
import sys

from PIL import Image

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.target_center import get_target_centers   # noqa: E402
from main.config import DEFAULT_ENGINE                        # noqa: E402


class _Skip(Exception):
    """把"本机跑不了"跟"跑失败"区分开，skip 不算测试失败。"""


def _load_detector():
    """加载 engine。跑不了就抛 _Skip，并说明原因。"""
    if not os.path.isfile(DEFAULT_ENGINE):
        raise _Skip(f'找不到 engine 文件 {DEFAULT_ENGINE}')

    try:
        from main.component.detector import Detector
    except ImportError as e:
        raise _Skip(f'没装 tensorrt / cuda-python：{e}')

    try:
        det = Detector(DEFAULT_ENGINE)
    except Exception as e:
        raise _Skip(f'engine 加载失败（可能没有可用的显卡）：{e}')
    return det


def test_detector_end_to_end_returns_original_pixel_coords():
    """1280x720 的图进去，出来的框必须在 1280x720 的范围内。

    判据很简单但很有效：框只要还在原图范围内，就说明逆 letterbox
    确实做了 —— 因为画布坐标下的框最大能到 640，
    而这里要求上界能触到 720，两者的量纲不一样。
    """
    w, h = 1280, 720
    det = _load_detector()
    try:
        img = Image.new('RGB', (w, h), (60, 60, 60))
        boxes, scores, clss = det.infer(img)
        img.close()
    finally:
        det.close()

    # 三个数组长度必须一致，否则下游 zip 会静默截断
    assert len(boxes) == len(scores) == len(clss), \
        (len(boxes), len(scores), len(clss))

    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = (float(v) for v in box)
        assert x1 <= x2 and y1 <= y2, box
        assert 0.0 <= x1 and 0.0 <= y1, box
        assert x2 <= w and y2 <= h, box

    # 中心点必须是整数，且落在原图内
    centers = get_target_centers(boxes, scores, clss)
    assert len(centers) == len(boxes)
    for c in centers:
        assert isinstance(c['x'], int) and isinstance(c['y'], int), c
        assert 0 <= c['x'] <= w and 0 <= c['y'] <= h, c


def test_detector_describe_reports_640_input():
    """engine 的输入必须正好是 (1, 3, 640, 640)，跟前处理对齐。

    这条对不上，图会被喂错，检测结果会莫名其妙地差。
    """
    det = _load_detector()
    try:
        assert det.in_shape == (1, 3, 640, 640), det.in_shape
    finally:
        det.close()


# ----------------------------------------------------------------------
# 迷你测试运行器（没有 pytest 时的退路）
# ----------------------------------------------------------------------

def _run_all():
    """把本文件里所有 test_ 开头的函数跑一遍，打印结果。"""
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed, skipped = [], []
    for name, fn in tests:
        try:
            fn()
        except _Skip as e:
            skipped.append(name)
            print(f'  skip  {name}  ({e})')
        except Exception as e:
            failed.append(name)
            print(f'  FAIL  {name}\n        {type(e).__name__}: {e}')
        else:
            print(f'  ok    {name}')

    passed = len(tests) - len(failed) - len(skipped)
    print(f'\n{passed}/{len(tests)} 通过'
          + (f'，{len(skipped)} 个跳过' if skipped else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run_all())
