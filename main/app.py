# -*- coding: utf-8 -*-
"""启动函数：抓当前屏幕 -> 跑检测 -> 输出每个 person 的中心像素坐标。

这就是整个程序的入口逻辑，start.py 只负责调用这里的 main()。

只读屏幕，不做任何鼠标/键盘操作，不调用任何第三方 DLL。
抓到的截图只在内存里活一帧，检测完立刻丢掉，不写临时文件。
默认连日志都不写，磁盘上一个字节都不动；要留记录只有调试时加 --record。

数据流：
    grab()            抓屏          -> PIL 图片
    det.infer()       检测          -> 原图像素坐标的框
    fmt_frame()       给人看的文字   -> 终端 / log
    out.emit()        结构化数据     -> jsonl，同时算出这一帧的中心点
    _emit_coords()    拿上面那批中心点 -> stdout / 坐标文件

注意 out.emit() 和坐标输出【共用同一批中心点】：
    emit() 算一次，坐标输出直接取 out.get_centers()。
    不能再算第二遍 —— 去重用的是随机步长，算两遍会得出两组不一样的数，
    同一帧的 jsonl 和 stdout 就对不上了。
"""

import gc
import json
import os
import sys
import time
import buke_km

from . import runmode
from .args import parse_args
from .component.coord_stream import CoordStream
from .component.detection_output import DetectionOutput
from .component.detector import Detector
from .component.report import fmt_cleanup, fmt_frame, fmt_summary
from .component.screen import dpi_aware, grab
from .config import DEFAULT_JSONL, DEFAULT_LOG

# 初始化实验参数
buke_km.configure(scale=1.25, track=(20, 5))

def main(argv=None):
    """程序主流程。argv 传 None 时读真实命令行；测试时可以传一个列表。"""
    a = parse_args(argv)

    # --stop / --status 就地办完；默认的"一直监听"在这里转成后台进程。
    # 返回 True 说明这个进程的活已经干完，不用再往下走。
    if runmode.dispatch(a):
        return

    if not os.path.isfile(a.engine):
        sys.exit(f'engine 文件不存在: {a.engine}\n'
                 f'先在 weights\\ 下放好 engine，或用 --engine 指定路径。')

    # 纯净模式：stdout 上只许有坐标，其余提示全部走 stderr，
    # 这样别的程序直接读 stdout 就能解析，不会被多余文字干扰。
    coords_mode = a.stdout_coords or a.json_coords or bool(a.coords_file)
    info = sys.stderr if coords_mode else sys.stdout

    def say(*parts):
        print(*parts, file=info)

    # DPI 声明必须在抓屏之前，否则坐标会被系统缩放一次
    dpi_aware()

    det = Detector(a.engine)
    _print_banner(say, a, det, coords_mode)

    log, out, coords = _open_recording(a, coords_mode)

    try:
        total_ms = _run_frames(a, det, out, log, coords, coords_mode, say)
    finally:
        # 不管中间出什么错，都必须释放资源
        if log:
            log.close()
        coords.close()
        out.close()
        det.close()

    summary = fmt_summary(total_ms)
    if summary:
        say(summary)
    if a.log:
        say(f'文本记录: {a.log}')
    if a.jsonl:
        say(f'JSON 记录: {a.jsonl}')
    if coords.enabled:
        say(f'坐标文件: {a.coords_file}')


# ----------------------------------------------------------------------
# 内部小函数
# ----------------------------------------------------------------------

def _print_banner(say, a, det, coords_mode):
    """启动时打印几行说明，让人确认参数没填错。"""
    say(f'engine  {os.path.basename(a.engine)}  {det.describe()}')
    say(f'抓屏区域  {a.region if a.region else "整屏"}')
    if a.loop > 0:
        say(f'帧数/间隔  {a.loop} 帧 / {a.interval} 秒')
    else:
        say(f'一直监听  每帧间隔 {a.interval} 秒，Ctrl+C 停止')
    if coords_mode:
        if a.coords_file:
            say(f'坐标文件  {a.coords_file}（一行一帧，每行一个 JSON 数组）')
        else:
            mode = 'stdout-coords' if a.stdout_coords else 'json-coords'
            say(f'模式  {mode}（坐标走 stdout，本提示走 stderr）')
    say('只读屏幕，不做任何鼠标/键盘操作。')


def _open_recording(a, coords_mode):
    """按参数决定要不要写文件。返回 (log 句柄或 None, DetectionOutput, CoordStream)。

    默认三个都不写，也就是磁盘上什么都不产生。
    --record 会把没指定的 log / jsonl 补成 out\\ 下面的默认路径。
    """
    if a.record:
        a.log = a.log or DEFAULT_LOG
        a.jsonl = a.jsonl or DEFAULT_JSONL

    log = None
    if a.log:
        os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
        log = open(a.log, 'w', encoding='utf-8')

    out = DetectionOutput(
        a.jsonl or None,
        offset=a.region[:2] if a.region else (0, 0),
        echo=not a.no_echo and not coords_mode,
        keep_lines=a.keep_lines,
    )
    # a.coords_file 为 None 时它什么都不写，这里不用额外判断
    coords = CoordStream(a.coords_file, keep_lines=a.keep_lines)
    return log, out, coords


def _emit_coords(a, targets, coords):
    """把这一帧的中心坐标送出去。

    ★ 这一帧最终的 JSON 字符串就在这个函数里的 payload 变量（下面标了框）。
      想接自己的程序，就在那儿写，全项目只有这一个地方需要改。

    送出去的东西【统一是一个 JSON 数组】，一帧一个，不是每个目标一个：
        没检出目标   -> []                          空的也是数组
        1 个目标     -> [{...}]
        3 个目标     -> [{...}, {...}, {...}]
    所以外面解析永远只有一套代码，不用按目标个数分情况。

    送到哪里，取决于命令行加了哪个参数（判定的是"送到哪"，不看目标个数）：
        --stdout-coords / --json-coords -> 终端，一行一帧
        都没加                           -> 写进 out\\coords.jsonl（后台默认走这条）
    两个 stdout 参数输出完全一样，只是历史上留了两个名字。

    参数：
        a        命令行参数，靠它判断该走哪条分支
        targets  这一帧的中心坐标 [{'x':.., 'y':.., 'confidence':..}, ...]
                 由 out.emit() 算好传进来。没检出目标时是空列表 []。
                 【这里不重算】—— 重算会得出另一组随机去重后的 confidence。
        coords   坐标文件对象（CoordStream）。没开文件时它自己什么都不做。
    """
    # 每帧先拼一次，拼出来的这一串就是最终结果，下面两条分支共用它，
    # 保证"你拿到的"和"写进文件的 / 打到屏幕上的"永远一模一样。
    payload = json.dumps(targets, ensure_ascii=False, separators=(',', ':'))

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  ★★★ 要接你自己的程序，就写在这一行下面 ★★★                      ║
    # ║                                                                  ║
    # ║  payload 就是这一帧最终的 JSON 字符串，长这样：                   ║
    # ║      '[]'                                                        ║
    # ║      '[{"x":1250,"y":438,"confidence":0.94}]'                     ║
    # ║      3 个目标就是数组里 3 个元素，一行里全都有                     ║
    # ║                                                                  ║
    # ║  targets 是同一批数据的 Python 列表，想直接取字段就用它：          ║
    # ║      [{'x': 1250, 'y': 438, 'confidence': 0.94}, ...]             ║
    # ║      targets[0]['x'] / ['y'] / ['confidence']                     ║
    # ║      没检出目标时 targets == []，直接 for 循环就是空转，不用特判    ║
    # ║                                                                  ║
    # ║  这个函数每帧都被调用一次，所以写在这里 = 每帧都执行一次。          ║
    # ║  注意：这里是主循环里，写耗时的代码会拖慢检测帧率。               ║
    # ╚══════════════════════════════════════════════════════════════════╝
    # 你自己的代码写在这里 ↓↓↓
    fntp_res = buke_km.move_from_json(payload)
    if not fntp_res:
        print('error msg：', fntp_res.reason)
        return
    # 你自己的代码写在这里 ↑↑↑

    if a.stdout_coords or a.json_coords:
        # 一行一帧，整行就是一个 JSON 数组，目标全在这个数组里。
        # flush=True：立刻吐出去，不攒在缓冲区里 ——
        # 否则外面按行阻塞读 stdout 的程序会一直等，看起来像卡死。
        print(payload, flush=True)
    else:
        # 后台默认走这条：往 out\coords.jsonl 追加一行，内容和上面完全一致。
        coords.write_line(payload)


def _run_frames(a, det, out, log, coords, coords_mode, say):
    """主循环。返回每帧的耗时列表（毫秒）。

    a.loop <= 0（默认）时这个循环不会自己结束，
    要靠 Ctrl+C 或另一个进程执行 --stop 把它停掉。
    """
    off = a.region[:2] if a.region else (0, 0)
    total_ms = []
    continuous = a.loop <= 0

    try:
        for i in runmode.frame_numbers(a.loop):
            t0 = time.perf_counter()
            img = grab(a.region)
            boxes, scores, classes = det.infer(img, a.conf, a.iou)
            dt = (time.perf_counter() - t0) * 1000
            total_ms.append(dt)

            # 尺寸要在 close 之前取
            size = img.size
            img.close()          # 截图用完就丢，内存里只活这一帧
            del img

            # 给人看的文字
            for line in fmt_frame(i, size, dt, boxes, scores, off):
                if not coords_mode:
                    print(line)
                if log:
                    log.write(line + '\n')

            # 结构化数据（jsonl）；中心点也在这里算好，下面直接复用
            out.emit(boxes, scores, classes)

            # 坐标输出：stdout 或坐标文件。
            # 无论加没加参数都调 —— 没加参数时 stdout 不打印、文件也没开，
            # 里面两条分支自然都不出声，但 _emit_coords 里那个接自己程序的
            # 位置每帧都会执行到，不用管当前是什么模式。
            _emit_coords(a, out.get_centers(), coords)

            # 定期清理，防止长时间运行内存和文件无限增长
            if a.cleanup_every and i % a.cleanup_every == 0:
                dropped = out.cleanup()
                coords.trim()
                gc.collect()
                msg = fmt_cleanup(i, dropped)
                say(msg)
                if log:
                    log.write(msg + '\n')

            # 一直监听时每帧都等；指定帧数时最后一帧不用等
            if continuous or i < a.loop:
                time.sleep(max(0.0, a.interval))
    except KeyboardInterrupt:
        say('\n收到 Ctrl+C，已停止监听。')

    return total_ms
