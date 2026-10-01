# -*- coding: utf-8 -*-
"""离线看图用的小工具：读一张图片、把检测框画上去存盘。

只在跑 run_infer.py --out 的时候用得上，是给人眼看的，
不参与任何坐标计算，也不会影响线上流程。

注意：画的框用的是【原图像素坐标】，也就是 Detector.infer 的返回值，
所以框的位置和图片内容是严格对齐的，可以直接用来验证坐标对不对。
"""

import os
import sys

from PIL import Image, ImageDraw

# 画框的颜色和粗细，纯显示用，随便改
BOX_COLOR = (255, 0, 0)
BOX_WIDTH = 3
TEXT_COLOR = (255, 0, 0)


def load_image(path):
    """读一张图片，返回 RGB 模式的 PIL.Image。

    文件不存在时直接打印提示并退出（这是命令行工具，
    报错信息越直白越好，不要抛一大串回溯让人看不懂）。
    """
    if not os.path.isfile(path):
        sys.exit(f'图片不存在: {path}')
    return Image.open(path).convert('RGB')


def draw(img, boxes, scores):
    """在原图上画检测框，返回一张新图（不修改传入的 img）。

    参数：
        img     PIL.Image
        boxes   (N, 4) 原图像素坐标 xyxy
        scores  (N,) 置信度

    每个框左上角标一行 "person 0.97"。
    标签画在框上方；框贴着图片顶边时标签改画在框内侧，避免跑出画面。
    """
    out = img.copy()
    d = ImageDraw.Draw(out)

    for (x1, y1, x2, y2), s in zip(boxes, scores):
        d.rectangle([x1, y1, x2, y2], outline=BOX_COLOR, width=BOX_WIDTH)

        # 标签默认在框上方 14 像素处；顶边太窄就压到框里面
        ty = y1 - 14 if y1 - 14 >= 0 else y1 + 2
        d.text((x1 + 3, ty), f'person {s:.2f}', fill=TEXT_COLOR)

    return out
