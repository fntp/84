# -*- coding: utf-8 -*-
"""start.py --check：把"跟随"这条链上的每一环单独验一遍。

为什么需要这个文件：
    跟随没生效这件事，从外面看【永远是同一个现象】—— 鼠标不动。
    可原因至少有四个，而且互相之间完全看不出来：
        1. 右键按着却读不到（权限比游戏低，GetAsyncKeyState 收不到输入）
        2. buke_km 起不来（驱动没装 / 不是管理员 / 联网探测不通）
        3. 相对移动发不出去（DLL 能动，但这个接口没通）
        4. 每一环都正常，只是画面上那一帧没检出人 / 分数没过门槛
    一个个试太慢，所以就一条命令全验完，而且【第 1 项和第 3 项要真的动一下】
    —— 光看代码"应该是通的"没用，这个坑就是这么踩出来的。

每一项都做成独立探针，验不了的（比如在 Linux 上）标成 `--` 跳过，
不算失败。函数返回值里"没过的项数"只数真的判定为失败的。

会动鼠标的两项（相对移动、右键开关）都明说要用户看着，并且相对移动
推完立刻推回来，不会把光标扔到别的地方去。
"""

import ctypes
import sys
import time

from .component.screen import dpi_aware, screen_size
from .component.trigger import RightButtonHold
from .config import TRIGGER_VK


# ----------------------------------------------------------------------
# 单独的问询：拿不到就返回 None，交给调用方标成"验不了"
# ----------------------------------------------------------------------

def _admin():
    """本进程是不是管理员。读不到返回 None。

    为什么专挑这一项：游戏基本都是管理员权限跑的。Windows 的 UIPI
    不允许低权限进程读高权限窗口的输入 —— 游戏一开管理员，这边
    GetAsyncKeyState 就再也看不到右键，跟随一次都不触发。
    这个失败【没有任何报错】，现象就是"没反应"，最容易误判成代码坏了。
    """
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def _foreground_title():
    """当前前台窗口的标题。用来确认"你按键的时候，焦点到底在谁身上"。"""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value or '(无标题窗口)'
    except Exception:
        return None


# ----------------------------------------------------------------------
# 三、相对移动：真的推一下再推回来
# ----------------------------------------------------------------------

def probe_move(sleep=time.sleep, push=200):
    """推 +push 再推回 -push，看相对移动通不通。返回 (ok, 说明)。

    为什么要真的动：buke_km 那一层"加载 DLL 成功"和"移动真的发得出去"
    是两回事。起得来但移动没通的话，跟随同样是全程不动，
    而错误只有真调一次才会暴露出来。

    为什么推完必须推回来：这一项会真的动鼠标，推 +200 再 -200 等于原地不动。
    另外锁定视角的游戏里光标本来就被夹在屏幕中心，推 200 像素不会顶到边。
    """
    try:
        import buke_km
        km = buke_km.BukeKm()
    except Exception as e:
        return False, f'起不来：{type(e).__name__}: {e}'

    try:
        km.move_relative(push, 0)
        sleep(0.15)
        km.move_relative(-push, 0)
        return True, (f'已推 +{push} 再推 -{push}（看看光标或视角刚才动没动；'
                      f'没动说明驱动没真接上）')
    except Exception as e:
        return False, f'调用失败：{type(e).__name__}: {e}'
    finally:
        try:
            km.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# 四、右键开关：盯着看它认不认得按下
# ----------------------------------------------------------------------

def watch_presses(gate, seconds, clock=time.monotonic, sleep=time.sleep,
                  interval=0.02):
    """盯着开关看 seconds 秒，返回认到了几次按下。

    数的是 gate.presses（从"没按着"变成"按着"记一次），不是这个循环自己
    轮询到的状态变化。原因：这个循环 20 毫秒才问一次，手快的一下可能整段
    夹在两次问之间（问的时候已经松开了），那时候两次 poll 都看到"没按着"，
    状态变化那一路会把它整个漏掉 —— 而按下次数是读键线程按 5 毫秒的节奏
    数的，漏不掉。

    先 poll 一次把起点记下来：开关可能一进来就已经按着（用户手还停在右键
    上），那一下不是他在这个窗口里按的，不该算。

    用的是程序里真正在用的那个 RightButtonHold，不另写一份判断 ——
    这样"这里认得到、游戏里认不到"才能推出"是权限问题"这个结论；
    两边各写各的判断，就分不清是环境的问题还是逻辑的问题了。

    clock / sleep 是注入的，测的时候不用真的等。
    """
    gate.poll()
    base = gate.presses
    start = clock()
    while True:
        gate.poll()
        if clock() - start >= seconds:
            return gate.presses - base
        sleep(interval)


# ----------------------------------------------------------------------
# 输出格式
# ----------------------------------------------------------------------

def _line(ok, title, detail=''):
    """一行结果。ok 是 True / False / None，None 表示"这项在这台机器上验不了"。"""
    mark = '--' if ok is None else ('OK' if ok else '!!')
    return f'[{mark}] {title}' + (f'  {detail}' if detail else '')


def _countdown(say, seconds, sleep):
    for i in range(int(seconds), 0, -1):
        say(f'  {i} ...')
        sleep(1)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

def run(say, wait=8.0, clock=time.monotonic, sleep=time.sleep, move=True):
    """跑一遍自检。返回"没过"的项数（验不了的不算）。

    参数：
        say     收一句话的回调，一般就是 print
        wait    右键那项等用户按键的秒数
        move    要不要做"真的推一下鼠标"那项。测试里传 False 跳过
    """
    say('=== 跟随自检 ===')
    bad = 0

    def report(ok, title, detail=''):
        nonlocal bad
        if ok is False:
            bad += 1
        say(_line(ok, title, detail))

    if not sys.platform.startswith('win'):
        say(_line(None, f'当前系统 {sys.platform}',
                  '跟随只能用 Windows（要 buke_km 的 DLL 和 Windows 的鼠标接口）。'
                  '检测和坐标输出不受影响。'))
        say('这台机器上验不了鼠标和右键，其余几项跳过。')
        return 0

    # 读屏幕尺寸之前必须先声明 DPI 感知，不然拿到的是被系统缩放过的假尺寸，
    # 准星就取不到真正的屏幕中心。和 app.py 里的顺序一致。
    dpi_aware()

    admin = _admin()
    if admin is None:
        report(None, '管理员权限', '读不到')
    else:
        report(admin, '管理员权限', '是' if admin else
               '不是 —— 游戏通常用管理员启动，那时候系统不许低权限进程读它的输入，'
               '右键永远读不到。用管理员身份开个终端再跑本程序试试。')

    title = _foreground_title()
    report(None, '当前前台窗口', title if title else '读不到')

    try:
        import buke_km
    except Exception as e:
        report(False, 'buke_km 导入', f'{type(e).__name__}: {e}')
    else:
        report(True, 'buke_km 版本', getattr(buke_km, '__version__', '?'))
        try:
            st = buke_km.driver_status()
        except Exception as e:
            report(False, 'HID 驱动', f'查询失败：{type(e).__name__}: {e}')
        else:
            installed = bool(st.get('installed'))
            report(installed, 'HID 驱动', '已装' if installed else
                   '没装 —— 先用管理员跑 buke_km.install_driver()')

    size = screen_size()
    if size is None:
        report(False, '屏幕尺寸', '读不到，准星位置就定不下来')
    else:
        report(True, '屏幕尺寸', f'{size[0]}x{size[1]}，准星取正中心 '
                                 f'({size[0] // 2},{size[1] // 2})')

    if move:
        say('\n3 秒后真的推一下鼠标（+200 再推回 -200，光标会回到原处）。'
            '注意看光标或游戏视角，不想让它动就按 Ctrl+C。')
        _countdown(say, 3, sleep)
        ok, detail = probe_move(sleep=sleep)
        report(ok, '相对移动 MouseMoveR', detail)

    say(f'\n请在接下来 {wait:.0f} 秒内【按住鼠标右键、松开，这样来两遍】'
        f'（按住 = 开镜 = 跟随，松开 = 停手）。')
    gate = RightButtonHold(TRIGGER_VK)
    # 起读键线程，和真正跑起来的时候一模一样（app.py 里也是这么起的）。
    # 这里要是用自己的 0.02 秒轮询去读，就比真跑的时候钝 ——
    # 真跑时那 5 毫秒一次的读键没被验到，而这一项的全部意义就是
    # 【证明按得动】，自检比实际更钝的话，它报"认不到"就等于在骗人。
    gate.start()
    try:
        clicks = watch_presses(gate, wait, clock=clock, sleep=sleep)
    finally:
        gate.close()
    if clicks:
        report(True, f'右键开关 VK 0x{TRIGGER_VK:02X}', f'认到 {clicks} 次按下')
    else:
        report(False, f'右键开关 VK 0x{TRIGGER_VK:02X}',
               '一次都没认到 —— 要么本程序权限比游戏低（用管理员启动本程序），'
               '要么按的不是鼠标右键')

    say('')
    if bad:
        say(f'有 {bad} 项没过，先按上面那几行修。')
    else:
        say('自检全过。跟随这条链是通的 —— 游戏里要是还不跟，'
            '用 start.py --follow-trace 看每一帧到底判成了什么：'
            '没检出人、分数没过门槛、还是已经在死区里。')
    return bad
