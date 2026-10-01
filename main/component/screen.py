# -*- coding: utf-8 -*-
"""屏幕相关：DPI 声明、抓图、--region 参数解析。

这里只做"把屏幕像素读进来"这件事，不写盘、不缓存、不做检测。

关于 DPI（很重要）：
    Windows 在缩放不是 100% 时（比如 125%、150%），会把程序"骗"一遍：
    程序以为自己拿到的是 1920x1080，其实系统给了它一张缩放过的图。
    结果是：检测出来的坐标和真实屏幕坐标差一个倍数，鼠标点过去就偏了。

    所以启动时必须先声明"我是 DPI 感知的"（dpi_aware()），
    系统才会把未经缩放的原始像素给你。这一步必须在抓图之前做，
    而且整程序只需要做一次。

抓到的图是 PIL.Image，RGB 模式，坐标原点在左上角。
"""

import ctypes

from PIL import ImageGrab


def dpi_aware():
    """声明本进程是 DPI 感知的，避免坐标被系统二次缩放。

    必须在使用抓图之前调用，而且只调一次。
    这两句都是 Windows 专有 API，失败不影响运行，只是坐标可能有偏差。
    """
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE，Win8.1 及以上推荐
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # 老系统的退路
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def parse_region(text):
    """解析 --region 参数，格式 "x1,y1,x2,y2"。

    例如 "200,150,1480,870" 表示抓屏幕上从 (200,150) 到 (1480,870) 这块矩形。

    参数写错时抛 argparse.ArgumentTypeError，argparse 会把它变成
    友好的报错信息并退出，而不是抛一个看不懂的回溯。

    返回 (x1, y1, x2, y2) 四个整数，单位是屏幕绝对像素。
    """
    # 允许用户写成 "200, 150, 1480, 870"，把空格去掉
    text = text.replace(' ', '')
    parts = text.split(',')

    if len(parts) != 4:
        raise argparse_type_error('region 要 x1,y1,x2,y2 四个整数')

    try:
        x1, y1, x2, y2 = (int(v) for v in parts)
    except ValueError:
        raise argparse_type_error('region 里必须都是整数')

    if x2 <= x1 or y2 <= y1:
        raise argparse_type_error('region 要满足 x2>x1 且 y2>y1')

    return (x1, y1, x2, y2)


def argparse_type_error(msg):
    """构造 argparse 的参数类型错误。

    单独包一层，是为了让本模块不用在顶层 import argparse，
    也让错误构造集中在一处，以后要换实现只改这里。
    """
    import argparse
    return argparse.ArgumentTypeError(msg)


def grab(region=None):
    """抓一屏（或一块区域），返回 PIL.Image（RGB）。

    参数：
        region  None 表示整屏；否则是 (x1, y1, x2, y2) 屏幕绝对像素
                 （用 parse_region 解析出来的格式）

    返回：
        PIL.Image，RGB 模式。用完记得 close()，否则内存里会留着一帧。

    注意 all_screens=False：只抓主显示器。多屏的时候坐标也按主屏算，
    和 buke_km_lib 的 MouseMove 口径一致。
    """
    img = ImageGrab.grab(bbox=region, all_screens=False)
    return img.convert('RGB')
