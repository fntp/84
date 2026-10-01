# -*- coding: utf-8 -*-
"""运行方式测试（不需要显卡、不需要真实抓屏）：帧号生成 + 开关 + 前台后台判断。

只管三件事，都是纯计算：

    1. frame_numbers()  -- 默认一直数下去（一直监听），指定帧数就数到那儿停
    2. gated_frames()   -- 开关关着的时候一个帧号都不发（右键开关就接在这儿）
    3. is_foreground()  -- 这次该在终端里跑，还是丢到后台去

这几条决定了"敲一句 start.py 到底会发生什么"，改错了很难发现
（比如又会变成只跑一帧就退，或者开关关着还在偷偷烧帧数），所以单独钉一下。

运行方式（在项目根目录下，不需要 pytest）：

    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe test\\test_loop.py
"""

import argparse
import itertools
import os
import sys

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import runmode                                              # noqa: E402


def _args(**over):
    """造一个"参数解析完"的对象。只写测试用得上的字段，其余给默认值。

    默认值刻意跟真实的"什么都不填"一致：一直监听、没要任何输出、跟随参数没动过。

    跟随那几个字段（always / follow_conf / gain / deadzone）默认都是 None，
    和 args.py 里一致 —— None 表示"用户没说"，_child_cmd 就是靠这个决定转不转发。
    follow_trace 默认 False 但【必须在】，因为 is_foreground 要读它：
    少了这个字段整个文件都是 AttributeError，而不是某一条测试失败。
    """
    base = dict(fg=False, bg=False, loop=0, coords_file=None, record=False,
                log=None, jsonl=None, stdout_coords=False, json_coords=False,
                follow_trace=False, always=False, follow_conf=None, gain=None,
                deadzone=None,
                # 下面几个只有 _child_cmd 用得上，默认值和 args.py 一样
                interval=0.05, engine='weights/best.engine', region=None,
                conf=None, iou=None)
    base.update(over)
    return argparse.Namespace(**base)


# ----------------------------------------------------------------------
# 一、帧号：默认一直监听
# ----------------------------------------------------------------------

def test_default_loop_is_infinite():
    """loop=0 表示一直监听：帧号数不完，不是一个"只有一帧"的列表。"""
    it = runmode.frame_numbers(0)
    # 无限流不能用 len()，那是它的特征，不是缺点
    assert isinstance(it, itertools.count)
    assert [next(it) for _ in range(5)] == [1, 2, 3, 4, 5]


def test_negative_loop_also_means_infinite():
    """负数当作 0 处理，也是一直监听（参数校验在前面的 args.py 拦掉）。"""
    it = runmode.frame_numbers(-1)
    assert [next(it) for _ in range(3)] == [1, 2, 3]


def test_loop_one_is_exactly_one_frame():
    """loop=1 是"只跑一帧"，跑完就退出 —— 和 0 完全不是一回事。"""
    assert list(runmode.frame_numbers(1)) == [1]


def test_loop_n_gives_one_to_n():
    """填 N 就跑 N 帧，编号从 1 开始，正好 N 个。"""
    assert list(runmode.frame_numbers(3)) == [1, 2, 3]
    assert list(runmode.frame_numbers(100))[-1] == 100


# ----------------------------------------------------------------------
# 二、开关：关着的时候一个帧号都不发
# ----------------------------------------------------------------------

class _Gate:
    """假装是右键开关：按顺序吐出这些状态，吐到最后一个就一直吐它。"""

    def __init__(self, *states):
        self._states = list(states)
        self.polls = 0

    def poll(self):
        self.polls += 1
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]


def _drive(a, gate, said):
    """跑一遍开关门控。idle_interval=0 是为了让测试不真的睡 ——
    真的空转那 0.05 秒在程序里有用，在测试里纯属浪费时间。"""
    return list(runmode.gated_frames(a, gate, said.append, idle_interval=0))


def test_no_gate_means_plain_frame_numbers():
    """--always（gate 为 None）：行为跟没有开关时一模一样，也不报开关状态。"""
    said = []
    assert _drive(_args(loop=3), None, said) == [1, 2, 3]
    assert said == []


def test_gate_off_sends_nothing_until_it_opens():
    """关着的时候一帧都不出：帧号停在 1，等开关打开才从 1 开始数。"""
    said = []
    gate = _Gate(False, False, False, True)
    assert _drive(_args(loop=1), gate, said) == [1]
    # 空转 3 次 + 开门那次 + 发现"帧数跑满了"的那一次。三次空转一帧都没往下发。
    assert gate.polls == 5
    assert said == [runmode.switch_message(False), runmode.switch_message(True)]


def test_gate_off_does_not_burn_the_frame_budget():
    """关着的那段时间【不占帧数】。

    这是最容易写错的地方：要是主循环里简单地 continue 一下、帧号照常往下走，
    --loop 100 会在用户还没按右键的时候就把额度烧光然后退出，
    看起来就是"程序自己关了"，用户根本不知道发生了什么。
    """
    said = []
    gate = _Gate(False, False, True, True)
    assert _drive(_args(loop=2), gate, said) == [1, 2]


def test_gate_reports_state_only_when_it_changes():
    """开着的时候每帧都 poll，但只在状态真的变了的时候说一句。

    否则每秒十行"右键开关：开"，日志全被这句话淹了。
    """
    said = []
    assert _drive(_args(loop=5), _Gate(True), said) == [1, 2, 3, 4, 5]
    assert said == [runmode.switch_message(True)]


def test_finishing_the_loop_is_a_clean_stop():
    """帧数跑满时生成器要干净地结束，不能抛 RuntimeError。

    PEP 479：生成器里逃出去的 StopIteration 会被 Python 变成 RuntimeError，
    那样主循环就炸在收尾上了。这条是那个坑的回归测试。
    """
    assert _drive(_args(loop=3), _Gate(True), []) == [1, 2, 3]
    assert _drive(_args(loop=1), _Gate(False, True), []) == [1]


# ----------------------------------------------------------------------
# 三、前台 / 后台
# ----------------------------------------------------------------------

def test_default_runs_in_background():
    """默认（什么都不填）就是后台静默监听，这是这次改动的核心。"""
    assert runmode.is_foreground(_args()) is False


def test_explicit_flags_win():
    """--fg / --bg 是明说的，优先于自动判断。"""
    assert runmode.is_foreground(_args(fg=True)) is True
    assert runmode.is_foreground(_args(bg=True)) is False


def test_loop_forces_foreground():
    """说了抓几帧就退，那是一锤子买卖，直接在终端里跑。"""
    assert runmode.is_foreground(_args(loop=1)) is True
    assert runmode.is_foreground(_args(loop=50)) is True


def test_wanting_visible_output_forces_foreground():
    """后台进程没有窗口，print 没人看得见。

    所以只要用户要看得见的东西（记录 / 坐标输出 / 坐标文件），
    就必须留在前台，否则他会以为程序没跑。
    """
    for field in ('record', 'stdout_coords', 'json_coords'):
        assert runmode.is_foreground(_args(**{field: True})) is True, field

    assert runmode.is_foreground(_args(log='a.log')) is True
    assert runmode.is_foreground(_args(jsonl='a.jsonl')) is True
    assert runmode.is_foreground(_args(coords_file='c.jsonl')) is True


def test_follow_trace_forces_foreground():
    """--follow-trace 也是"要看得见的东西"，必须留在前台。

    这个开关加出来就是为了排查"跟随到底有没有生效"，而默认是后台跑、
    屏幕上什么都不打。要是它没把进程留在前台，用户加了它还是什么都看不到 ——
    那就等于白加，而且他会以为是自己加错了。
    """
    assert runmode.is_foreground(_args(follow_trace=True)) is True


# ----------------------------------------------------------------------
# 四、转后台时参数要跟着走
# ----------------------------------------------------------------------

def test_child_cmd_forwards_follow_flags():
    """转后台那一步必须把跟随参数原样转发给子进程。

    不转发的话子进程用默认值，用户敲的 --always / --gain 在转后台时被丢掉，
    表现是"参数填了跟没填一样"。而默认就是走后台这条路，所以几乎必然踩到。
    """
    cmd = runmode._child_cmd(_args(always=True, follow_conf=0.3, gain=0.8,
                                   deadzone=5))
    joined = ' '.join(cmd)
    assert '--always' in cmd
    for flag, val in (('--follow-conf', '0.3'), ('--gain', '0.8'),
                      ('--deadzone', '5')):
        assert joined.count(f'{flag} {val}') == 1, (flag, joined)


def test_child_cmd_omits_follow_flags_nobody_set():
    """用户没填的跟随参数不要转发。

    转发一个 None 会拼出 "--gain None"，子进程的参数解析直接报错退出，
    而且报错只在 out\\bg.log 里，用户看到的是"启动完什么都没发生"。
    """
    cmd = runmode._child_cmd(_args())
    assert '--always' not in cmd
    for flag in ('--follow-conf', '--gain', '--deadzone'):
        assert flag not in cmd


# ----------------------------------------------------------------------
# 迷你测试运行器（没有 pytest 时的退路）
# ----------------------------------------------------------------------

def _run_all():
    """把本文件里所有 test_ 开头的函数跑一遍，打印结果。"""
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failed.append(name)
            print(f'  FAIL  {name}\n        {type(e).__name__}: {e}')
        else:
            print(f'  ok    {name}')

    print(f'\n{len(tests) - len(failed)}/{len(tests)} 通过')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run_all())
