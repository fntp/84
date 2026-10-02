# -*- coding: utf-8 -*-
"""启动函数：抓当前屏幕 -> 跑检测 -> 输出坐标 + 跟随鼠标。

这就是整个程序的入口逻辑，start.py 只负责调用这里的 main()。

【跟随是带开关的】：默认关着，什么都不做（不抓屏、不检测、不动鼠标）。
要跟随就按一下鼠标右键，再按一下停手。为什么要有开关 —— 用户自己也在用
鼠标转视角，程序每帧都动鼠标就是两只手抢一个鼠标，手感是"我划到哪它给我拽回来"。
不想用开关加 --always，就是"一直跟随"的老行为。

动鼠标用的是相对移动（MouseMoveR），从准星位置朝目标推一点点，每帧重算。
不是"把鼠标挪到目标坐标"—— 那种做法在锁定视角的游戏里会让镜头一帧甩过去。
细节见 component/aim.py 的说明。

抓到的截图只在内存里活一帧，检测完立刻丢掉，不写临时文件。
默认连日志都不写，磁盘上一个字节都不动，只有调试时加 --record 才留记录。

数据流：
    grab()            抓屏          -> PIL 图片
    det.infer()       检测          -> 原图像素坐标的框
    fmt_frame()       给人看的文字   -> 终端 / log
    out.emit()        结构化数据     -> jsonl，同时算出这一帧的中心点
    follower.update() 拿上面那批中心点 -> 推一下鼠标（Follower 里面）
    _emit_coords()    同一批中心点    -> stdout / 坐标文件

注意 out.emit() 和后面两步【共用同一批中心点】：
    emit() 算一次，跟随和坐标输出都直接取 out.get_centers()。
    不能再算第二遍 —— 去重用的是随机步长，算两遍会得出两组不一样的数，
    同一帧的 jsonl 和 stdout 就对不上了。
"""

import gc
import json
import os
import sys
import time

from . import runmode
from .args import parse_args
from .component.coord_stream import CoordStream
from .component.detection_output import DetectionOutput
from .component.detector import Detector
from .component.follow import Follower
from .component.report import fmt_cleanup, fmt_frame, fmt_summary
from .component.screen import dpi_aware, grab, screen_size
from .component.trigger import RightButtonToggle
from .config import DEFAULT_JSONL, DEFAULT_LOG, TRIGGER_VK

# 这里【故意不调用 buke_km.configure()】。
# 原来写的是 configure(scale=1.25, track=(20, 5))，两个都错：
#   scale  抓屏已经是 DPI 感知的，坐标就是真实像素，再缩放一次反而偏；
#          而且这句在模块导入时执行，跑在 dpi_aware() 之前，口径都不一致。
#   track  这是"分几步挪过去"的慢速滑动。每帧都在发新目标，上一步还没走完
#          下一步就到了，鼠标就一直在飘。不设它 = 瞬间到位，正是我们要的。


def main(argv=None):
    """程序主流程。argv 传 None 时读真实命令行；测试时可以传一个列表。"""
    a = parse_args(argv)

    # --check 就地办完，而且必须排在 dispatch 前面：往下走会判成"该转后台"，
    # 那就跑到一个看不见输出的子进程里去了。也不走到 engine 那一句 ——
    # 自检跟模型没关系，没有 engine 文件也该能跑。
    if a.check:
        from .selfcheck import run as run_check
        run_check(print)
        return

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
    gate, follower, mover = _build_follow(a, say)
    _print_banner(say, a, det, coords_mode, gate, follower)

    log, out, coords = _open_recording(a, coords_mode)

    try:
        total_ms = _run_frames(a, det, out, log, coords, coords_mode, say,
                               gate, follower)
    finally:
        # 不管中间出什么错，都必须释放资源
        if gate is not None:
            gate.close()
        if mover:
            mover.close()
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

def _print_banner(say, a, det, coords_mode, gate, follower):
    """启动时打印几行说明，让人确认参数没填错。

    跟随那两行必须说：开关默认是关的，程序起来之后一动不动。
    不告诉用户"按右键才开始"，他会以为程序坏了 —— 这正是加开关必然带来的代价。
    """
    say(f'engine  {os.path.basename(a.engine)}  {det.describe()}')
    say(f'抓屏区域  {a.region if a.region else "整屏"}')
    if a.loop > 0:
        say(f'帧数/间隔  {a.loop} 帧 / 每帧至少 {a.interval} 秒')
    else:
        say(f'一直监听  每帧至少 {a.interval} 秒（干完活就进下一帧），Ctrl+C 停止')
    if coords_mode:
        if a.coords_file:
            say(f'坐标文件  {a.coords_file}（一行一帧，每行一个 JSON 数组）')
        else:
            mode = 'stdout-coords' if a.stdout_coords else 'json-coords'
            say(f'模式  {mode}（坐标走 stdout，本提示走 stderr）')

    if follower is None:
        say('跟随  关（拿不到屏幕尺寸。只有 Windows 上才会跟随）')
    elif gate is None:
        say('跟随  一直跟随（--always，所以没有开关）')
    else:
        say('跟随开关  鼠标右键：第一次按开始跟随，再按一次停手')
        say('          关着的时候不抓屏、不检测、也不动鼠标')
        gain = (f'{a.gain}（自动标定中）' if not a.no_auto_gain
                else f'{a.gain}（固定，--no-auto-gain）')
        say(f'跟随方式  从准星朝目标推（增益 {gain} '
            f'死区 {a.deadzone} 像素，最低置信度 {a.follow_conf}）')

    if follower is not None and a.follow_trace:
        say('逐帧判定  开（--follow-trace，每帧一句"跟随 ..."）')


class _LazyMover:
    """真要动鼠标的那一刻，才去创建 buke_km。

    三个原因：
      1. 创建它会立刻加载 DLL（驱动没装、不是管理员、联网探测不通，
         都会在这一步炸）。跑 --status / --stop、或者只想看看检测效果时，
         不该被它拖住，更不该因为它没装好就整个程序起不来。
      2. 开关一直关着的话，DLL 一次都不用碰。
      3. buke_km 这个包本身也可能没装好。所以连 import 都放到这里，
         不放模块顶层 —— 顶层 import 失败的话，start.py 一启动就抛回溯，
         连 --check 都跑不起来，而那正是唯一能告诉用户"包没装好"的命令。

    出错不在这里吞：抛给 Follower，由它去重后报一次。
    留着实例不重建，是因为重试的代价只是再调一次接口 ——
    真出问题时用户能当场修（装驱动、提权），修完下一帧就恢复了，不用重启。
    """

    def __init__(self):
        self._km = None

    def __call__(self, dx, dy):
        if self._km is None:
            import buke_km
            self._km = buke_km.BukeKm()
        self._km.move_relative(int(dx), int(dy))

    def close(self):
        if self._km is not None:
            self._km.close()
            self._km = None


def _build_follow(a, say):
    """准备开关和跟随器，返回 (gate, follower, mover)。

    gate     None 表示不做开关判断（--always）
    follower None 表示不动鼠标（拿不到屏幕尺寸时）
    mover    持有 DLL 的那个对象，退出时要 close

    准星取屏幕正中心：锁定视角的游戏会把鼠标夹在那儿，游戏读的就是它的位移。
    注意准星用【整屏】中心，和目标坐标一个口径（target_center 加的也是整屏绝对坐标），
    所以带不带 --region 都算得对。
    """
    gate = None if a.always else RightButtonToggle(TRIGGER_VK)

    size = screen_size()
    if size is None:
        # 非 Windows。检测和坐标输出照常，只是没法跟随。
        # 开关仍然生效 —— 用户要的就是"关着的时候什么都别干"。
        # 这里【不起读键线程】：没有跟随，读到的点击也没人用。
        say('拿不到屏幕尺寸（只有 Windows 能跟随），这次只做检测和坐标输出。')
        return gate, None, None

    # 开关的读键放到自己的线程里，别跟着主循环的节奏走。见 trigger.start()。
    # 必须在返回之前起 —— 主循环一进 gated_frames 就开始问状态了。
    if gate is not None:
        gate.start()

    mover = _LazyMover()
    follower = Follower(
        mover,
        crosshair=(size[0] // 2, size[1] // 2),
        min_confidence=a.follow_conf,
        gain=a.gain,
        deadzone=a.deadzone,
        # 默认自动标定增益，--gain 只当起点。不标定的话，用户填的倍率跟这个
        # 游戏的实际灵敏度对不上时，准星就是一格一格慢慢滑过去 —— 而灵敏度
        # 是多少只有游戏自己知道，猜不出一个通用值。--no-auto-gain 退回老行为。
        auto_gain=not a.no_auto_gain,
        # --follow-trace 时把每帧判定过程说给用户听。
        # 不加就是 None，Follower 里连字符串都不拼。
        trace=say if a.follow_trace else None,
    )
    return gate, follower, mover


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

    只做输出，不动鼠标。要动鼠标的动作放在 _run_frames 里的 follower 那段 ——
    放在这里的话，方向是反的：这个函数负责的是"把结果给你"，
    拿到了结果之后打算干什么，是调用方（主循环）的事。

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

    if a.stdout_coords or a.json_coords:
        # 一行一帧，整行就是一个 JSON 数组，目标全在这个数组里。
        # flush=True：立刻吐出去，不攒在缓冲区里 ——
        # 否则外面按行阻塞读 stdout 的程序会一直等，看起来像卡死。
        print(payload, flush=True)
    else:
        # 后台默认走这条：往 out\coords.jsonl 追加一行，内容和上面完全一致。
        coords.write_line(payload)


def _run_frames(a, det, out, log, coords, coords_mode, say, gate, follower):
    """主循环。返回每帧的耗时列表（毫秒）。

    a.loop <= 0（默认）时这个循环不会自己结束，
    要靠 Ctrl+C 或另一个进程执行 --stop 把它停掉。
    有开关时它会在开关关着的那段时间里原地空转，不产生帧号。
    """
    off = a.region[:2] if a.region else (0, 0)
    total_ms = []
    continuous = a.loop <= 0

    try:
        for i in runmode.gated_frames(a, gate, say):
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

            # 结构化数据（jsonl）；中心点也在这里算好，下面两步直接复用
            out.emit(boxes, scores, classes)

            # 跟随：按瞄好的目标推一下鼠标。
            # 排在坐标输出前面，是因为这一步才是这个程序存在的理由；
            # 万一它拖慢了一点，宁可让坐标晚一点，也别让瞄准慢一帧。
            # Follower 里会把"没目标""已经对准"当成正常情况不吭声，
            # 只有真出错才返回一句话，而且是重复的不说 ——
            # 不然 out\bg.log 会被同一句话刷满。
            # 想看每帧到底判成了什么，加 --follow-trace：那种详细的话由
            # Follower 自己直接 say 出来（走 trace 回调），不从这里返回，
            # 免得和"出错才说"这个去重逻辑搅在一起。
            if follower is not None:
                msg = follower.update(out.get_centers())
                if msg:
                    say(msg)
                    if log:
                        log.write(msg + '\n')

            # 坐标输出：stdout 或坐标文件。
            # 无论加没加参数都调 —— 没加参数时 stdout 不打印、文件也没开，
            # 里面两条分支自然都不出声。
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
                # --interval 是【一帧至少占多久】，不是"干完活再等这么久"。
                # 抓屏加推理本来就要几十毫秒，再无条件睡满一个间隔的话，
                # 帧率只有 1/(耗时+间隔)，准星就是一格一格慢慢滑过去 ——
                # 用户要的是开镜就贴上。所以这里只补剩下的那点时间：
                # 活干得比间隔久就一秒不等，直接进下一帧（全速）。
                spent = time.perf_counter() - t0
                if spent < a.interval:
                    time.sleep(a.interval - spent)
    except KeyboardInterrupt:
        say('\n收到 Ctrl+C，已停止监听。')

    return total_ms
