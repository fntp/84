# -*- coding: utf-8 -*-
r"""前台 / 后台怎么跑，这个文件说了算。

用户想要的效果：
    在项目根目录敲一句 python start.py，就【静默地在后台】跑起来，
    一直监听屏幕，把坐标写进文件，不弹窗、不占着终端。

两条路：
    前台：有窗口、有打印，Ctrl+C 停止。看效果、调试时用。
    后台：用 pythonw 起一个没有窗口的进程，坐标写进 out\coords.jsonl。
          终端立刻就能拿回来干别的。

怎么决定走哪条？看 is_foreground()：
    明确加了 --fg                        -> 前台
    明确加了 --bg                        -> 后台
    --loop 大于 0（说了抓几帧就退）      -> 前台
    其余情况（默认，一直监听）            -> 后台

    一句话：不想要输出就默认后台；只要你要看得见东西（--record /
    --log / --jsonl / --stdout-coords / --json-coords / --coords-file），
    那就得是前台，不然你什么也看不到。

后台进程怎么停？
    python start.py --stop
    （本质是 taskkill /PID <pid> /F，pid 记在 out\bg.pid）
    python start.py --status  看它还在不在
"""

import itertools
import os
import subprocess
import sys

from .config import BG_COORDS, BG_LOG, BG_PID, OUT_DIR, ROOT

# 入口脚本的绝对路径。后台子进程必须用绝对路径，
# 因为它的工作目录是我们指定的，不是用户敲命令的地方。
ENTRY = os.path.join(ROOT, 'start.py')


# ----------------------------------------------------------------------
# 帧号：前台后台共用
# ----------------------------------------------------------------------

def frame_numbers(loop):
    """生成每一帧的编号。

    loop <= 0  一直数下去：1, 2, 3, ... 直到外面喊停（Ctrl+C 或 --stop）
    loop == N  只数 N 个：1, 2, ..., N

    这个函数代替了以前那个 range(1, max(1, a.loop) + 1)，
    那时候 0 和 1 都是"只跑一帧"，现在 0 是"一直跑"。
    """
    if loop <= 0:
        return itertools.count(1)
    return iter(range(1, loop + 1))


# ----------------------------------------------------------------------
# 判断：这次该前台还是后台
# ----------------------------------------------------------------------

def is_foreground(a):
    """根据命令行参数判断这次是前台还是后台。返回 True 表示前台。"""
    if a.fg:
        return True
    if a.bg:
        return False
    # 说了抓几帧就退，那是个"跑一下就结束"的活儿，直接在终端里跑就行
    if a.loop > 0:
        return True
    # 剩下的都是"一直监听"。但如果用户要看得见的输出，就只能前台，
    # 因为后台进程没有窗口，print 出去的东西没人能看见。
    wants_visible = [a.coords_file, a.record, a.log, a.jsonl,
                     a.stdout_coords, a.json_coords]
    return any(wants_visible)


def dispatch(a):
    """执行 --stop / --status，或者决定要不要转后台。

    返回 True  -> 这个进程的活已经干完了，app.main() 直接返回
    返回 False -> 继续走正常的前台流程
    """
    if a.stop:
        print(stop_running())
        return True
    if a.status:
        print(status_text())
        return True
    if is_foreground(a):
        return False

    print(start_background(a))
    return True


# ----------------------------------------------------------------------
# 后台进程：起、查、停
# ----------------------------------------------------------------------

def _pythonw():
    """找一个"没有窗口"的 python。

    pythonw.exe 和 python.exe 装在同一个目录里，区别只有一个：
    pythonw 不弹控制台窗口。找不到就退回 python.exe
    （那会闪一个黑窗口，功能一样，只是难看）。
    """
    exe = sys.executable
    if not exe:
        return 'pythonw.exe'
    cand = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return cand if os.path.isfile(cand) else exe


def _child_cmd(a):
    """拼出后台子进程要执行的完整命令。

    必须带 --fg，否则子进程又会判断成"该转后台"，无限套娃。

    只转发会影响检测结果的参数（engine / region / conf / iou / interval）。
    不转发 --record / --stdout-coords / --json-coords：
    后台模式下坐标固定写进 BG_COORDS，用不上那些。
    """
    cmd = [
        _pythonw(), ENTRY,
        '--fg',                     # 我就是前台，别再转后台
        '--loop', '0',              # 一直监听
        '--interval', str(a.interval),
        '--coords-file', BG_COORDS,
    ]
    if a.engine:
        cmd += ['--engine', a.engine]
    if a.region:
        # region 在参数解析后是个元组，得拼回 "x1,y1,x2,y2" 的样子
        cmd += ['--region', ','.join(str(int(v)) for v in a.region)]
    if a.conf is not None:
        cmd += ['--conf', str(a.conf)]
    if a.iou is not None:
        cmd += ['--iou', str(a.iou)]
    return cmd


def read_pid():
    """读 out\\bg.pid 里记的进程号。文件不在、内容不是数字都返回 None。"""
    try:
        with open(BG_PID, 'r', encoding='utf-8') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _clear_pid():
    """删掉 pid 文件。删不掉也无所谓，说明本来就没有。"""
    try:
        os.remove(BG_PID)
    except OSError:
        pass


def _alive(pid):
    """进程号对应的进程还在不在。

    用系统自带的 tasklist 查，不装任何第三方库。
    直接在字节里找 pid 的数字，不解码 —— 免掉一堆编码麻烦，
    tasklist 在任何编码下输出的 pid 都是 ASCII 数字。
    """
    if not pid:
        return False
    try:
        r = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH'],
                           capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return str(pid).encode('ascii') in (r.stdout or b'')


def start_background(a):
    """起一个后台进程，返回一句给人看的话。已经有一个在跑就不重复起。"""
    pid = read_pid()
    if pid and _alive(pid):
        return ('后台已经在跑了（进程号 %d），不重复启动。\n'
                '看状态：start.py --status\n'
                '停掉：  start.py --stop' % pid)

    os.makedirs(OUT_DIR, exist_ok=True)

    # 新一轮监听，把上一轮留下的坐标清掉，免得新旧混在一起。
    # 坐标文件是"最近这些帧"的实时流，不是历史日志，清了不心疼。
    open(BG_COORDS, 'w', encoding='utf-8').close()

    # 这两个 flag 是"脱离父进程"的关键：
    #   DETACHED_PROCESS          不跟着父进程的控制台一起走
    #   CREATE_NEW_PROCESS_GROUP  Ctrl+C 不会顺手把它也带走
    flags = 0
    for name in ('DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP'):
        flags |= getattr(subprocess, name, 0)

    # 子进程的中文输出要写进日志文件，得先把编码钉死成 utf-8
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    log = open(BG_LOG, 'a', encoding='utf-8')
    try:
        p = subprocess.Popen(
            _child_cmd(a),
            stdin=subprocess.DEVNULL,   # 后台进程不读键盘，也就不会卡住
            stdout=log,
            stderr=log,                 # 报错也记进日志，方便事后看
            cwd=ROOT,
            env=env,
            creationflags=flags,
        )
    finally:
        log.close()

    with open(BG_PID, 'w', encoding='utf-8') as f:
        f.write(str(p.pid))

    return ('已在后台启动，进程号 %d。\n'
            '坐标实时写在：%s\n'
            '启动日志：    %s\n'
            '看状态：start.py --status    停掉：start.py --stop'
            % (p.pid, BG_COORDS, BG_LOG))


def stop_running():
    """停掉后台进程，返回一句给人看的话。"""
    pid = read_pid()
    if not pid:
        return '后台没有在跑（没有 out\\bg.pid）。'

    if not _alive(pid):
        _clear_pid()
        return '后台进程 %d 早就不在了，已经把这个记录清掉。' % pid

    r = subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                       capture_output=True)
    if r.returncode == 0:
        _clear_pid()
        return '已停止后台进程 %d。' % pid

    return ('没停掉（taskkill 返回 %d）。'
            '可以手动执行：taskkill /PID %d /F' % (r.returncode, pid))


def status_text():
    """看一眼后台在不在跑，返回一句给人看的话。"""
    pid = read_pid()
    if not pid:
        return '后台没有在跑。要启动就直接运行 start.py'

    if _alive(pid):
        return ('后台在跑，进程号 %d。\n'
                '坐标文件：%s\n'
                '输出日志：%s\n'
                '停掉：start.py --stop' % (pid, BG_COORDS, BG_LOG))

    return ('后台没在跑（记录里是进程号 %d，但那个进程已经不在了）。\n'
            '重新启动：start.py' % pid)
