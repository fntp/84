# -*- coding: utf-8 -*-
"""坐标契约测试：检测框 -> 原图像素坐标 -> 目标中心点。

这是整个项目最不能出错的地方，所以单独一个文件把它钉死。
要验证的契约（原始验收标准原文）：

    1. bbox 始终是原始图片像素坐标 [x1, y1, x2, y2]
    2. center 严格等于 (x1 + x2) / 2, (y1 + y2) / 2
    3. 输出是整数像素坐标
    4. 不允许：640x640 坐标当屏幕坐标 / 归一化 / 再次缩放 / DPI 二次换算
    5. 多目标全部输出，不挑"最佳目标"、不排序

运行方式（在项目根目录下，不需要 pytest）：

    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe test\\test_coords.py

函数名保持 test_ 开头，所以以后装了 pytest 也能直接认。
"""

import os
import sys

import numpy as np

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.postprocess import postprocess, scale_boxes  # noqa: E402
from main.component.screen import parse_region                   # noqa: E402
from main.component.target_center import get_target_centers      # noqa: E402


# ----------------------------------------------------------------------
# 一、letterbox 逆运算：画布坐标 -> 原图像素坐标
# ----------------------------------------------------------------------

def test_scale_boxes_maps_canvas_back_to_original():
    """原图 1280x720 缩放 0.5 倍后是 640x360，上下各补 140 像素灰边。

    画布里的 [0, 140, 640, 500] 正好是那张 1280x720 的图，
    换算回来必须一模一样。
    """
    boxes = np.array([[0.0, 140.0, 640.0, 500.0]], np.float32)
    out = scale_boxes(boxes, 0.5, (0, 140), (1280, 720))
    assert np.allclose(out[0], [0.0, 0.0, 1280.0, 720.0]), out[0]


def test_scale_boxes_clips_to_image_border():
    """超出原图范围的坐标要裁到边界内，不能出现负数或超过宽高。"""
    boxes = np.array([[-100.0, -100.0, 2000.0, 2000.0]], np.float32)
    out = scale_boxes(boxes, 0.5, (0, 140), (1280, 720))
    assert np.allclose(out[0], [0.0, 0.0, 1280.0, 720.0]), out[0]


# ----------------------------------------------------------------------
# 二、后处理：阈值、空结果、坐标口径
# ----------------------------------------------------------------------

def test_postprocess_empty_input_shapes():
    """一个框都没有时返回三个【空数组】，不是 None，调用方可以直接 len()。"""
    raw = np.zeros((1, 25200, 6), np.float32)
    boxes, scores, classes = postprocess(raw)
    assert boxes.shape == (0, 4)
    assert scores.shape == (0,)
    assert classes.shape == (0,)
    assert boxes.dtype == np.float32
    assert classes.dtype == np.int64


def test_postprocess_threshold_is_strict():
    """置信度必须【严格大于】阈值才算命中：等于阈值不算。"""
    raw = np.zeros((1, 3, 6), np.float32)
    raw[0, 0] = [100, 100, 20, 20, 1.0, 0.25]   # score 正好 0.25 -> 排除
    raw[0, 1] = [100, 100, 20, 20, 1.0, 0.26]   # 0.26 -> 保留
    raw[0, 2] = [100, 100, 20, 20, 0.5, 0.40]   # 0.20 -> 排除

    boxes, scores, _ = postprocess(raw, conf_thres=0.25)
    assert len(boxes) == 1, scores
    assert abs(float(scores[0]) - 0.26) < 1e-5


def test_postprocess_returns_canvas_coords():
    """postprocess 给的是 640x640 画布坐标，还没做逆 letterbox。

    这里把中心放在画布正中，xywh = (320,320,40,80)，
    换算成 xyxy 就该是 [300, 280, 340, 360]。
    """
    raw = np.zeros((1, 1, 6), np.float32)
    raw[0, 0] = [320.0, 320.0, 40.0, 80.0, 0.9, 0.9]
    boxes, _s, _c = postprocess(raw, conf_thres=0.25, iou_thres=0.45)
    assert len(boxes) == 1
    assert np.allclose(boxes[0], [300.0, 280.0, 340.0, 360.0]), boxes[0]


def test_postprocess_keeps_all_targets_without_sorting():
    """多目标全部保留，不做"只留最佳"也不重新排序。"""
    raw = np.zeros((1, 3, 6), np.float32)
    raw[0, 0] = [100.0, 100.0, 20.0, 20.0, 0.9, 0.9]   # 中心 (100,100)
    raw[0, 1] = [400.0, 400.0, 20.0, 20.0, 0.9, 0.8]   # 中心 (400,400)
    raw[0, 2] = [600.0, 600.0, 20.0, 20.0, 0.9, 0.7]   # 中心 (600,600)

    boxes, scores, _c = postprocess(raw, conf_thres=0.25, iou_thres=0.45)
    assert len(boxes) == 3, scores


# ----------------------------------------------------------------------
# 三、中心点契约
# ----------------------------------------------------------------------

def test_center_is_mean_of_corners_and_integer():
    """center 必须严格等于 (x1+x2)/2、(y1+y2)/2，并且输出整数。"""
    boxes = np.array([[100.0, 200.0, 300.0, 400.0]], np.float32)
    scores = np.array([0.9], np.float32)

    [t] = get_target_centers(boxes, scores)
    assert t['x'] == 200 and t['y'] == 300
    assert isinstance(t['x'], int) and isinstance(t['y'], int)
    assert t['confidence'] == 0.9


def test_center_applies_offset_exactly_once():
    """抓局部区域时坐标要加回区域左上角，而且【只能加一次】。

    boxes 是区域内的相对坐标，加完 offset 才是屏幕绝对坐标。
    加两次就会整体偏移一倍，是这类代码最典型的 bug。
    """
    boxes = np.array([[100.0, 200.0, 300.0, 400.0]], np.float32)
    scores = np.array([0.5], np.float32)

    [t] = get_target_centers(boxes, scores, offset=(50, 60))
    assert (t['x'], t['y']) == (250, 360)


def test_center_empty_input_gives_empty_list():
    """没有目标时返回空列表，不是 None —— 调用方可以直接 for。"""
    out = get_target_centers(np.zeros((0, 4), np.float32), np.zeros(0, np.float32))
    assert out == []


# ----------------------------------------------------------------------
# 四、--region 参数解析
# ----------------------------------------------------------------------

def test_parse_region_accepts_spaces():
    """允许写成 "200, 150, 1480, 870"，空格自动去掉。"""
    assert parse_region('200,150,1480,870') == (200, 150, 1480, 870)
    assert parse_region(' 200, 150, 1480, 870 ') == (200, 150, 1480, 870)


def test_parse_region_rejects_bad_input():
    """参数写错时抛 ArgumentTypeError，让 argparse 报友好错误而不是崩栈。"""
    import argparse

    def bad(text):
        try:
            parse_region(text)
        except argparse.ArgumentTypeError:
            return True
        return False

    assert bad('1,2,3')              # 不是四个数
    assert bad('a,2,3,4')            # 有非整数
    assert bad('100,100,100,200')    # x2 必须大于 x1
    assert bad('100,100,200,100')    # y2 必须大于 y1


# ----------------------------------------------------------------------
# 迷你测试运行器（没有 pytest 时的退路）
# ----------------------------------------------------------------------

def _run_all():
    """把本文件里所有 test_ 开头的函数跑一遍，打印结果。"""
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failed.append(name)
            print(f'  FAIL  {name}\n        {type(e).__name__}: {e}')
        else:
            print(f'  ok    {name}')

    print(f'\n{len(tests) - len(failed)}/{len(tests)} 通过')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run_all())
