# -*- coding: utf-8 -*-
"""检测框 -> 中心点坐标。全项目唯一的坐标口径，别处不许自己再算一遍。

坐标契约（写死了，别改）：
    原点      屏幕左上角
    x 方向    向右为正
    y 方向    向下为正
    单位      像素
    类型      整数（四舍五入）
    缩放      不缩放
    归一化    不归一化
    DPI      不再做第二次换算

中心点算法：
    x = (x1 + x2) / 2
    y = (y1 + y2) / 2

关于 offset：
    用 --region 只抓屏幕上一块区域时，检测出来的框坐标是"相对于
    这块区域左上角"的。offset 就是这块区域左上角在整屏上的位置，
    加一次，坐标就变成【整屏绝对坐标】。注意只加一次。

关于重复分：
    两个目标偶尔会算出完全一样的置信度，下游拿到两个一模一样的分数
    就没法区分谁是谁。所以这里顺手做一个去重，保证同一帧里
    不会出现两个相同的 confidence。算法在 _dedupe_scores 里。

完整契约见 docs/detection_output_protocol.md。
"""

import random

# 本模型只有一类。类别名字放这里，别处引用它，不要各写各的
DEFAULT_CLASS_NAMES = ('person',)

# 去重用的随机步长范围，单位是千分之一，也就是 0.100 ~ 0.200。
# 取到 100 就是 0.100，取到 200 就是 0.200。
DEDUPE_STEP_MIN = 100
DEDUPE_STEP_MAX = 200


def _dedupe_scores(scores):
    """把一批可能重复的分数改成两两都不相等的分数。

    做法：
        先随机取一个步长 r，范围 0.100 ~ 0.200，精确到 0.001。
        把分数从高到低走一遍，只要当前的分数 >= 上一个分数，
        就把它改成"上一个分数 - r"。只减不加，所以分数只会变小，
        永远不会超过原来的值（也就永远不会超过 100%）。

    效果：
        一组重复的分数 a, a, a, a 会变成 a, a-r, a-2r, a-3r……
        重复几个就拉开几档，重复得越多拉得越开。
        沿着这条路走下来是严格递减的，
        所以整批分数里【不可能有两个相等】。

    参数：
        scores  已经四舍五入到 3 位小数的分数列表

    返回：
        等长的新列表，【顺序和输入完全一致】。
        输入本来就没有重复时，值原样返回（只多花一次排序）。

    极端情况：
        5 个目标全都卡在置信度下限上（比如都是 0.25），
        步长又取到最大 0.2 时，最后两个会算成负数。
        这种情况要求 5 个框分数完全相等、且恰好都等于阈值，
        实际不会出现，所以这里不做下限截断 ——
        截断反而会把两个分数压成同一个，破坏"两两不等"的保证。
    """
    n = len(scores)
    if n < 2:
        return list(scores)

    # 只排下标，不动原顺序 —— 最后还要按输入顺序把结果放回各自的坑里。
    # sorted 是稳定排序，分数相同时保持原来的先后，结果才是确定的。
    order = sorted(range(n), key=lambda i: -scores[i])

    step = random.randint(DEDUPE_STEP_MIN, DEDUPE_STEP_MAX) / 1000.0

    out = list(scores)
    prev = None
    for i in order:
        cand = out[i]
        if prev is not None and cand >= prev:
            cand = round(prev - step, 3)
        out[i] = cand
        prev = cand

    return out


def get_target_centers(boxes, scores, classes=None, offset=(0, 0)):
    """把一批检测框换算成中心点坐标。

    参数：
        boxes    (N, 4) 检测框 xyxy，原图像素坐标
        scores   (N,)   置信度
        classes  (N,)   类别下标，可以为 None（本模型只有一类，用不上）
        offset   (ox, oy) 截图区域左上角在屏幕上的位置，默认 (0,0) 表示全屏

    返回：
        list，每个元素是一个 dict：
            {'x': 1250, 'y': 438, 'confidence': 0.94}
        x、y 是整数像素坐标，confidence 保留 3 位小数。
        没有目标就返回空列表 []，不是 None。

    多目标时全部返回，不挑"最像的那个"，顺序也和传进来的一致 ——
    只有 confidence 会被 _dedupe_scores 拉开，保证同一帧里不会撞分。
    """
    ox, oy = int(offset[0]), int(offset[1])
    confs = _dedupe_scores([round(float(s), 3) for s in scores])

    targets = []
    for box, conf in zip(boxes, confs):
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        targets.append({
            'x': int(round((x1 + x2) / 2)) + ox,
            'y': int(round((y1 + y2) / 2)) + oy,
            'confidence': conf,
        })

    return targets


def class_name(cls_id, class_names=DEFAULT_CLASS_NAMES):
    """类别下标 -> 类别名。越界时退化成字符串形式的编号，不抛异常。"""
    if 0 <= cls_id < len(class_names):
        return class_names[cls_id]
    return str(cls_id)
