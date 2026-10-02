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
    --log / --jsonl / --stdout-coords / --json-coords / --coords-file /
    --follow-trace），那就得是前台，不然你什么也看不到。

后台进程怎么停？
    python start.py --stop
    （本质是 taskkill /PID <pid> /F，pid 记在 out\bg.pid）
    python start.py --status  看它还在不在，同时把日志最后几行打出来

    为什么 --status 要带日志尾巴：默认这条路是后台，终端上一个字都没有，
    用户唯一能判断"生没生效"的地方就是那个日志文件。开关的每一次开/关
    都记在里面 —— 按了右键却没有"右键开关：开"，一眼就能看出是那一下
    根本没被认到（多半是权限比游戏低），而不是跟随坏了。
"""

import itertools
import os
import subprocess
import sys
import time

from .config import BG_COORDS, BG_LOG, BG_PID, IDLE_INTERVAL, OUT_DIR, ROOT

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
# 开关：关着的时候一个帧号都不发
# ----------------------------------------------------------------------

def switch_message(on):
    """开关状态变了要说的话。开和关分开写，是为了日志里一眼能看出时间点。"""
    return '右键开关：开，开始跟随' if on else '右键开关：关，已停手'


def gated_frames(a, gate, say, idle_interval=IDLE_INTERVAL, on_open=None):
    """带开关的帧号生成器：开关关着的时候，一个帧号都不发。

    为什么做成生成器，而不是在主循环里 if 一下跳过这一帧：
        关着的时候不是"这一帧不动鼠标"那么轻，而是【整帧都不存在】——
        不抓屏、不推理、不算坐标、不写文件。而且帧号绝对不能照常往下走：
        开着 --loop 100 的话，关着那段时间会把额度白白烧掉，跑一半就退出了；
        定期清理的计数节奏也会跟着乱。生成器把"这一帧算不算数"和
        "主循环干不干活"合成同一件事，主循环那边一行都不用改。

    gate 为 None（--always）时直接转交 frame_numbers，行为跟没有开关时一样。

    on_open 是"开关从关翻到开"那一刻的回调，默认没有。
    现在接的是 Follower.reset()：关着的那段时间一帧都没有，跟随器手里那笔
    "上一帧推了多少、误差变成多少"的配对会一直停在上一次跟随的最后一帧上，
    再开镜时第一帧拿它去跟新误差比，配的不是同一件事 —— 反推出来的灵敏度
    可以错得离谱，第一下就把准星甩出去。这里正是唯一能看见那个上升沿的地方
    （跟随器自己只看得见帧，看不见开关）。
    """
    counter = iter(frame_numbers(a.loop))
    if gate is None:
        yield from counter
        return

    prev = None
    while True:
        on = gate.poll()
        if on != prev:
            say(switch_message(on))
            if on and on_open is not None:
                on_open()
            prev = on
        if not on:
            # 关着的时候空转。这里 sleep 是为了不占满一个核。
            # 间隔本身不影响能不能认出点击 —— 键是 trigger 的读键线程在读，
            # 这里只是"隔多久来看一眼它翻了没有"，也就是按下之后的反应延迟。
            time.sleep(idle_interval)
            continue
        try:
            i = next(counter)
        except StopIteration:
            # 帧数跑满了。异常必须在这里接住 —— 让它从生成器里逃出去的话，
            # Python 会把它变成 RuntimeError（PEP 479），主循环就炸了。
            return
        yield i


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
                     a.stdout_coords, a.json_coords, a.follow_trace]
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

    转发所有会影响子进程行为的参数：检测那几个（engine / region / conf /
    iou / interval）和跟随那几个（always / follow-conf / gain / deadzone /
    no-auto-gain）。
    不转发 --record / --stdout-coords / --json-coords：
    后台模式下坐标固定写进 BG_COORDS，用不上那些。
    --follow-trace 也不用转发 —— 它本身就会把进程留在前台。

    漏转一个的代价：子进程拿自己的默认值跑，用户敲的那个参数在转后台那一步
    就被丢掉了，表现是"参数填了跟没填一样"。而默认就是转后台，
    所以只要漏了，几乎必定踩到，而且没有任何提示。
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

    # 跟随那几个参数也必须转发。不转发的话子进程用的是自己的默认值，
    # 用户敲的 --always / --gain 这些在转后台那一步就被丢掉了 ——
    # 表现是"参数填了跟没填一样"，而且默认是后台，所以几乎必然踩到。
    if a.always:
        cmd += ['--always']
    if a.follow_conf is not None:
        cmd += ['--follow-conf', str(a.follow_conf)]
    if a.gain is not None:
        cmd += ['--gain', str(a.gain)]
    if a.deadzone is not None:
        cmd += ['--deadzone', str(a.deadzone)]
    # 关掉自标定是个"反向"开关：不带它 = 开着自标定。没有"带上去"的中间态，
    # 所以只能 when True 才拼，不能照抄 --conf 那种 when not None 的写法。
    if a.no_auto_gain:
        cmd += ['--no-auto-gain']
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
            '看状态：start.py --status    停掉：start.py --stop\n'
            '右键开关开没开、跟随报没报错，都记在上面那个日志里。\n'
            '跟随没反应先自检：start.py --check    '
            '想看每一帧的判定：start.py --follow-trace（会在前台跑）'
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


def tail(path, lines=6, window=4096):
    """读一个文本文件的最后几行，读不到就返回空表。

    为什么要专门写一个，不直接 readlines()：bg.log 是【追加写】的，
    跨很多次运行一直往里加，可能已经有几百兆。整个读进内存会把 --status
    这个本该一眨眼就返回的命令拖住。所以只从末尾读一小段。
    从中间截断可能把某个汉字切成两半，解码时按替换字符处理，日志无所谓。
    """
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - window))
            data = f.read()
    except OSError:
        return []
    rows = [x.rstrip() for x in data.decode('utf-8', 'replace').splitlines()]
    return [x for x in rows if x.strip()][-lines:]


def status_text():
    """看一眼后台在不在跑，返回一句给人看的话。

    末尾要带上日志的最后几行：默认跑法就是后台，终端上一句话都没有，
    用户判断"到底生没生效"只能看这个。开关的每一次开/关都记在里面，
    按了右键但这里没有"右键开关：开"，就说明那一下根本没被认到 ——
    这一条能直接分掉"没按到"和"按到了但没跟随"两种完全不同的毛病。
    """
    pid = read_pid()
    if not pid:
        return '后台没有在跑。要启动就直接运行 start.py'

    if _alive(pid):
        text = ('后台在跑，进程号 %d。\n'
                '坐标文件：%s\n'
                '输出日志：%s\n'
                '停掉：start.py --stop' % (pid, BG_COORDS, BG_LOG))
        recent = tail(BG_LOG)
        if recent:
            text += ('\n\n日志最后 %d 行（右键开关的开/关、跟随报的错都在这里）：\n'
                     % len(recent))
            text += '\n'.join('  ' + row for row in recent)
        return text

    return ('后台没在跑（记录里是进程号 %d，但那个进程已经不在了）。\n'
            '重新启动：start.py' % pid)
