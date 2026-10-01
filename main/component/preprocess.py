# -*- coding: utf-8 -*-
"""前处理：任意尺寸的原图 -> 640x640 的网络输入。

YOLO 要求输入必须是固定尺寸（这里是 640x640），但截图可能是
1920x1080 这种比例。直接拉伸会让人变胖变瘦，检测精度掉得厉害。

所以用 letterbox（信箱模式）：先等比缩放，让长边正好是 640，
短边不足的地方用灰色 (114,114,114) 补齐。
就像老电视放宽屏电影，上下留黑边，画面本身不变形。

补齐后，框的坐标还停留在 640x640 的画布上，
所以必须把缩放比例 r 和补齐偏移 (dw, dh) 一起返回，
后面才能还原回原图坐标（见 postprocess.scale_boxes）。
"""

import numpy as np
from PIL import Image

# 补齐用的灰色，和 YOLOv5 官方一致，别改
PAD_COLOR = (114, 114, 114)


def preprocess(img, size=640):
    """把一张 PIL 图片变成网络要的输入张量。

    参数：
        img    PIL.Image，任意尺寸
        size   网络输入边长，默认 640

    返回：
        blob        形状 (1, 3, size, size) 的 float32 数组，NCHW 排布
        r           等比缩放比例（原图 -> 缩放后）
        (dw, dh)    左右 / 上下的补齐像素数

    注意：这个函数不会修改传入的 img 对象。
    """
    # 网络只认 RGB 三通道，别的模式（L / RGBA / P）先转过来
    if img.mode != 'RGB':
        img = img.convert('RGB')

    w0, h0 = img.size

    # 等比缩放的倍率：长边缩放到 size，短边按同比例缩，保证不变形
    r = min(size / w0, size / h0)
    nw, nh = int(round(w0 * r)), int(round(h0 * r))

    # 尺寸有变化才缩放，没变化就别做无用的重采样
    if (nw, nh) != (w0, h0):
        img = img.resize((nw, nh), Image.BILINEAR)

    # 铺一张 640x640 的灰底，把缩好的图贴到正中间
    canvas = Image.new('RGB', (size, size), PAD_COLOR)
    dw, dh = (size - nw) // 2, (size - nh) // 2
    canvas.paste(img, (dw, dh))

    # 转成 float32 并归一化到 0~1；注意这里是除以 255，不是乘 1/255，
    # 浮点结果会差最后一位，为保证结果和之前完全一致，不要改写。
    arr = np.asarray(canvas, dtype=np.float32) / 255.0  # HWC 排布

    # PIL/numpy 给的是 HWC（高,宽,通道），网络要 NCHW（批,通道,高,宽）
    # ascontiguousarray 保证内存连续，否则拷到显存时会报错
    blob = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])

    return blob, r, (dw, dh)
