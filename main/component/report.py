# -*- coding: utf-8 -*-
"""把一帧的检测结果排版成给人看的文字。

只管"怎么显示"，不含任何计算逻辑 —— 坐标该是多少还是多少，
这里最多做四舍五入到整数，绝不改变口径。

打印出来的长这样：

    #1  1920x1080  推理 39.4 ms  检出 1 个
        person 0.973  [703, 544, 966, 1080]  263x536  中心 (835, 812)

第一行是这一帧的概况，后面每个目标一行。
"""


def fmt_frame(idx, size, dt_ms, boxes, scores, offset=(0, 0)):
    """生成一帧要打印的所有行，返回 list[str]。

    参数：
        idx     第几帧，从 1 开始
        size    (宽, 高) 这一帧抓到的图片尺寸
        dt_ms   这一帧总共花了多少毫秒（含抓屏）
        boxes   (N, 4) 检测框，原图像素坐标
        scores  (N,) 置信度
        offset  (ox, oy) 截图区域左上角在屏幕上的位置。
                加上它，打印出来的就是【整屏绝对】坐标，
                和输出的坐标（stdout 的 JSON 数组 / coords.jsonl）口径一致，
                用户不用自己换算。

    没有任何目标时只返回概况那一行。
    """
    ox, oy = offset

    lines = [
        f'#{idx}  {size[0]}x{size[1]}  推理 {dt_ms:.1f} ms  '
        f'检出 {len(boxes)} 个'
    ]

    for (x1, y1, x2, y2), s in zip(boxes, scores):
        # 换算成屏幕绝对坐标（只加一次 offset）
        bx1, by1 = x1 + ox, y1 + oy
        bx2, by2 = x2 + ox, y2 + oy
        cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2

        lines.append(
            f'    person {s:.3f}  '
            f'[{bx1:.0f}, {by1:.0f}, {bx2:.0f}, {by2:.0f}]  '
            f'{bx2 - bx1:.0f}x{by2 - by1:.0f}  '
            f'中心 ({cx:.0f}, {cy:.0f})'
        )

    return lines


def fmt_summary(total_ms):
    """多帧跑完后的一行总结。只有一帧（或没跑）时返回 None。

    注意这里算的是【整条链路】的耗时，包含抓屏，不是模型单独的速度。
    屏幕上看到 30 FPS 左右是正常的，别拿 engine 的 387 FPS 对比。
    """
    if len(total_ms) < 2:
        return None

    avg = sum(total_ms) / len(total_ms)
    return (f'平均 {avg:.1f} ms/帧  ≈ {1000 / avg:.1f} FPS'
            f'（含截屏，不是模型单独的速度）')


def fmt_cleanup(frame_idx, dropped):
    """定期清理时的那行提示。"""
    if dropped:
        return f'    [清理] 第 {frame_idx} 帧 → jsonl 丢弃 {dropped} 行，已 gc'
    return f'    [清理] 第 {frame_idx} 帧 → 已 gc'
