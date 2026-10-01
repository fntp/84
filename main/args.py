# -*- coding: utf-8 -*-
"""启动程序的命令行参数定义。

参数默认值全部来自 main/config.py，这里只负责"接参数"和"写帮助文字"，
不做任何业务判断。

为什么单独一个文件？
    app.py 里塞一堆 argparse 会让主流程看不清。
    放这里，app.py 打开就能直接看到"启动之后到底干了什么"。
"""

import argparse

from .config import (
    AIM_DEADZONE,
    AIM_GAIN,
    CLEANUP_EVERY,
    DEFAULT_CONF,
    DEFAULT_ENGINE,
    DEFAULT_INTERVAL,
    DEFAULT_IOU,
    DEFAULT_LOOP,
    FOLLOW_MIN_CONFIDENCE,
    KEEP_LINES,
)
from .component.screen import parse_region


def build_parser():
    """构造命令行解析器。返回 argparse.ArgumentParser。"""
    p = argparse.ArgumentParser(
        prog='start.py',
        description='抓当前屏幕 -> 跑 YOLO 检测 -> 输出 person 的中心像素坐标。'
                    '开关打开（默认鼠标右键）时，同时把鼠标朝目标方向推一点点。',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ---- 模型 ----
    p.add_argument('--engine', default=DEFAULT_ENGINE,
                   help='TensorRT engine 文件路径')
    p.add_argument('--conf', type=float, default=DEFAULT_CONF,
                   help='置信度阈值，调高=漏检多，调低=误检多')
    p.add_argument('--iou', type=float, default=DEFAULT_IOU,
                   help='NMS 重叠阈值')

    # ---- 抓屏 ----
    p.add_argument('--region', type=parse_region, default=None,
                   metavar='x1,y1,x2,y2',
                   help='只抓屏幕上一块矩形区域（屏幕绝对像素）。'
                        '不填就是整屏。输出的坐标仍然是整屏坐标')
    p.add_argument('--loop', type=int, default=DEFAULT_LOOP,
                   help='抓几帧。默认 0 = 一直监听，直到 Ctrl+C 或 --stop；'
                        '填 N 就抓 N 帧然后自动退出。注意只统计开关打开时的帧，'
                        '不按右键它就一直等着')
    p.add_argument('--interval', type=float, default=DEFAULT_INTERVAL,
                   help='多帧之间的间隔，单位秒。默认 0.1 秒，约每秒 10 次')

    # ---- 跟随瞄准 ----
    p.add_argument('--always', action='store_true',
                   help='不要右键开关，一直跟随。调试用；也用于按键读不到的场合 '
                        '（比如程序权限比游戏低，GetAsyncKeyState 收不到输入）')
    p.add_argument('--follow-conf', type=float, default=FOLLOW_MIN_CONFIDENCE,
                   help='跟随的最低置信度。比 --conf 高得多，宁可漏瞄也别瞄错人；'
                        '填 0 表示不设门槛')
    p.add_argument('--gain', type=float, default=AIM_GAIN,
                   help='跟随增益：屏幕像素差 -> 鼠标计数。调大追得猛、容易过冲，'
                        '调小追得平滑。只影响手感，每帧都在重新算，都能对准')
    p.add_argument('--deadzone', type=int, default=AIM_DEADZONE,
                   help='死区半径（像素）。目标离准星这么近就不动鼠标了，'
                        '免得准星跟着检测框一起抖')
    p.add_argument('--follow-trace', action='store_true',
                   help='每帧打印一句判定：这帧有没有检出人、分数够不够、'
                        '推了多少、还是已经在死区里。用来确认跟随到底有没有生效 —— '
                        '不加的话"没检出人"和"跟丢了"从外面看一模一样，'
                        '都是鼠标不动、什么都不打印')

    # ---- 运行方式（前台 / 后台）----
    p.add_argument('--fg', action='store_true',
                   help='强制在前台跑（有窗口、有打印）')
    p.add_argument('--bg', action='store_true',
                   help='强制在后台跑（无窗口，坐标写进 out\\coords.jsonl）')
    p.add_argument('--stop', action='store_true',
                   help='停掉后台进程，然后退出')
    p.add_argument('--status', action='store_true',
                   help='看看后台进程还在不在，然后退出')
    p.add_argument('--check', action='store_true',
                   help='只做一次自检然后退出：权限、驱动、屏幕、鼠标能不能动、'
                        '右键认不认得到。不用 engine，也不开机检测')

    # ---- 坐标输出（给别的程序读）----
    group = p.add_mutually_exclusive_group()
    group.add_argument('--stdout-coords', action='store_true',
                       help='纯净模式：stdout 上每帧一行 JSON 数组，其余信息走 '
                            'stderr。和 --json-coords 输出一样，只是个旧名字')
    group.add_argument('--json-coords', action='store_true',
                       help='纯净模式：stdout 上每帧一行 JSON 数组 '
                            '[{"x":..,"y":..,"confidence":..}]，没目标就是 []')
    p.add_argument('--coords-file', default=None, metavar='PATH',
                   help='把每帧坐标追加写进这个文件（一行一帧，一行一个 JSON '
                        '数组）。后台模式默认写 out\\coords.jsonl')

    # ---- 记录（默认全关，一个字节都不写）----
    p.add_argument('--record', action='store_true',
                   help='调试用：打开记录，写 log 和 jsonl。'
                        '不填则磁盘上不产生任何文件')
    p.add_argument('--log', default=None,
                   help='结果文本写到哪里。不给路径就不写')
    p.add_argument('--jsonl', default=None,
                   help='每帧一行 JSON 写到哪里。不给路径就不写')
    p.add_argument('--no-echo', action='store_true',
                   help='只写 jsonl，不打印结构化的 bbox/center')

    # ---- 缓存控制 ----
    p.add_argument('--cleanup-every', type=int, default=CLEANUP_EVERY,
                   help='每多少帧清理一次（gc + 裁 jsonl）。0 表示不清理')
    p.add_argument('--keep-lines', type=int, default=KEEP_LINES,
                   help='jsonl 最多保留多少行，超出的从最老的开始丢')

    return p


def parse_args(argv=None):
    """解析参数并做基本校验。argv 传 None 时读真实命令行（测试时可传列表）。"""
    a = build_parser().parse_args(argv)

    if a.loop < 0:
        build_parser().error('--loop 不能是负数')
    if a.interval < 0:
        build_parser().error('--interval 不能是负数')
    if a.follow_conf < 0:
        build_parser().error('--follow-conf 不能是负数')
    if a.gain < 0:
        build_parser().error('--gain 不能是负数')
    if a.deadzone < 0:
        build_parser().error('--deadzone 不能是负数')

    return a
