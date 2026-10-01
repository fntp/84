# -*- coding: utf-8 -*-
"""后处理：网络吐出来的原始数组 -> 原图上的检测框。

engine 的输出 output0 形状是 (1, 25200, 6)：
    25200 是候选框数量（80x80 + 40x40 + 20x20 三个尺度的格子加起来）
    6 列分别是：cx, cy, w, h, obj_conf, cls_conf
    坐标已经是 letterbox 画布（640x640）上的像素值，不用再乘 anchors

这一步要做三件事：
    1. 算分数 score = obj_conf * cls_conf
    2. 用 score 阈值先筛掉一大批
    3. 剩下的框两两比重叠度，只留最好的（NMS 非极大值抑制）

最后再调 scale_boxes 把坐标从 640x640 画布还原回原图。
"""

import numpy as np

# 小于这个宽或高的框直接丢，属于噪点
MIN_BOX_SIZE = 1.0


def xywh2xyxy(x):
    """中心点+宽高 (cx,cy,w,h) 换成左上+右下 (x1,y1,x2,y2)。

    参数：
        x  (N, 4) 数组

    返回：
        新的 (N, 4) 数组，不修改原数组
    """
    y = np.empty_like(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2   # x1 = cx - w/2
    y[:, 1] = x[:, 1] - x[:, 3] / 2   # y1 = cy - h/2
    y[:, 2] = x[:, 0] + x[:, 2] / 2   # x2 = cx + w/2
    y[:, 3] = x[:, 1] + x[:, 3] / 2   # y2 = cy + h/2
    return y


def box_iou_one(box, boxes):
    """算一个框跟一批框的 IoU（交并比）。

    参数：
        box    (4,) 单个框 xyxy
        boxes  (N, 4) 一批框

    返回：
        (N,) 每项是 0~1 的重叠比例，0 表示完全不重叠

    IoU = 交集面积 / 并集面积。
    并集 = 两个框各自面积之和 - 交集，所以 union 一定是正数；
    这里仍用 np.maximum 兜一下底，防止退化成 0 时除出 inf。
    """
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])

    # 宽高可能是负数（完全不重叠时），clip 到 0
    iw = np.maximum(0.0, x2 - x1)
    ih = np.maximum(0.0, y2 - y1)
    inter = iw * ih

    area_a = (box[2] - box[0]) * (box[3] - box[1])
    area_b = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    union = area_a + area_b - inter

    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def nms(boxes, scores, iou_thres):
    """非极大值抑制：同一目标经常被好几个框套住，只留分最高的那个。

    做法很朴素：
        1. 所有框按分数从高到低排队
        2. 第一名肯定留下
        3. 剩下的跟第一名比 IoU，重叠太多的（> iou_thres）认为是同一个目标，淘汰
        4. 对活下来的重复第 2 步，直到没框了

    参数：
        boxes      (N, 4) xyxy
        scores     (N,)
        iou_thres  重叠超过多少就判为同一个

    返回：
        (K,) 保留下来的框的下标
    """
    if boxes.shape[0] == 0:
        return np.zeros(0, dtype=np.int64)

    order = scores.argsort()[::-1]   # 按下标排序后倒过来 = 从高到低
    keep = []

    while order.size > 0:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        iou = box_iou_one(boxes[i], boxes[rest])
        # 只留下重叠度没超标的
        order = rest[iou <= iou_thres]

    return np.array(keep, dtype=np.int64)


def scale_boxes(boxes, r, pad, orig_wh):
    """把 letterbox 画布上的坐标还原成【原图】坐标。

    前处理做了两步：先乘 r 缩小，再往右下平移 (dw, dh) 居中。
    还原就是反过来：先减平移，再除以 r。

    参数：
        boxes    (N, 4) xyxy，坐标系是 640x640 画布
        r        前处理时的缩放比例
        pad      (dw, dh) 前处理时的补齐像素数
        orig_wh  (w, h) 原图尺寸，用来把超出边界的坐标夹回去

    返回：
        新的 (N, 4) 数组，坐标系是原图像素
    """
    dw, dh = pad
    w0, h0 = orig_wh
    out = boxes.copy()
    out[:, [0, 2]] = (out[:, [0, 2]] - dw) / r
    out[:, [1, 3]] = (out[:, [1, 3]] - dh) / r
    # 还原后可能有负坐标或超出原图，夹回图片范围内
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, w0)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, h0)
    return out


def postprocess(raw, conf_thres=0.25, iou_thres=0.45):
    """一整条后处理流水线。

    参数：
        raw        engine 原始输出，(1, 25200, 6)
        conf_thres 分数低于此值的框直接丢
        iou_thres  NMS 的重叠阈值

    返回：
        boxes   (N, 4) float32，xyxy，【640x640 画布】坐标（还没还原）
        scores  (N,)   float32，最终分数
        clss    (N,)   int64，类别下标。本模型只有 person 一类，恒为 0

    注意：本函数只管到"画布坐标"，还原原图坐标是 Detector.infer 干的，
    因为只有它才知道 r 和 pad。
    """
    pred = raw[0]                       # (25200, 6)
    obj = pred[:, 4]                    # 有没有东西的置信度
    cls = pred[:, 5]                    # 是什么东西的置信度
    scores = obj * cls                  # 两个相乘才是最终分数

    mask = scores > conf_thres
    if not mask.any():
        # 一个都没有，返回三个空数组，形状要对（下游拿 .shape[0] 判断）
        return (np.zeros((0, 4), np.float32),
                np.zeros(0, np.float32),
                np.zeros(0, np.int64))

    p = pred[mask]
    s = scores[mask]
    boxes = xywh2xyxy(p[:, :4])
    keep = nms(boxes, s, iou_thres)

    return boxes[keep], s[keep], np.zeros(keep.size, dtype=np.int64)
