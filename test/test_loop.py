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
import tempfile

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
    no_auto_gain 是唯一一个"False 才是默认"的跟随字段，见 _child_cmd。
    follow_trace 默认 False 但【必须在】，因为 is_foreground 要读它：
    少了这个字段整个文件都是 AttributeError，而不是某一条测试失败。
    """
    base = dict(fg=False, bg=False, loop=0, coords_file=None, record=False,
                log=None, jsonl=None, stdout_coords=False, json_coords=False,
                follow_trace=False, always=False, follow_conf=None, gain=None,
                deadzone=None, no_auto_gain=False,
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
    真的空转那一下在程序里有用（等开关翻过来），在测试里纯属浪费时间。"""
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


def test_opening_callback_fires_once_on_the_rising_edge():
    """开关从关翻到开的那一瞬间回调一次，一直开着不会再叫。

    跟随器就靠这一下把"上一帧推了多少、误差变成多少"那笔配对清掉：关着的那段
    时间一帧都没有，那笔配对还停在上一次开镜的最后一帧上，不清的话再开镜第一帧
    拿它去跟新误差比，配的不是同一件事，反推出来的灵敏度能差几十倍。
    跟随器自己只看得见帧，看不见开关，所以这个上升沿只能在这里认。
    """
    opened = []
    gate = _Gate(False, False, True, True)
    list(runmode.gated_frames(_args(loop=4), gate, [].append,
                             idle_interval=0,
                             on_open=lambda: opened.append(1)))
    assert len(opened) == 1


def test_every_new_engagement_gets_its_own_reset():
    """按两次右键就是两个上升沿，两次都要回调。

    用户原话是"我右键可能点无数次"—— 每一轮都得算新的一轮，
    不能只在第一次按的时候清。
    """
    opened = []
    gate = _Gate(True, True, False, False, True)
    assert list(runmode.gated_frames(_args(loop=2), gate, [].append,
                                    idle_interval=0,
                                    on_open=lambda: opened.append(1))) == [1, 2]
    assert len(opened) == 2


def test_no_opening_callback_without_a_gate():
    """--always 没有开关，压根没有"开镜"这一刻，一次都不许回调。"""
    opened = []
    assert list(runmode.gated_frames(_args(loop=3), None, [].append,
                                     idle_interval=0,
                                     on_open=lambda: opened.append(1))) == [1, 2, 3]
    assert opened == []


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


def test_child_cmd_forwards_no_auto_gain():
    """--no-auto-gain 也要跟着走。

    它和别的跟随参数不一样：【不转发】就是"没关"，所以漏了不会有任何报错，
    只是子进程照样自标定 —— 用户明明加了 --no-auto-gain，行为却当没加。
    默认又是走后台，所以这个漏法几乎必然发生，而且没人会注意到。
    """
    assert '--no-auto-gain' in runmode._child_cmd(_args(no_auto_gain=True))


def test_child_cmd_omits_follow_flags_nobody_set():
    """用户没填的跟随参数不要转发。

    转发一个 None 会拼出 "--gain None"，子进程的参数解析直接报错退出，
    而且报错只在 out\\bg.log 里，用户看到的是"启动完什么都没发生"。
    """
    cmd = runmode._child_cmd(_args())
    assert '--always' not in cmd
    for flag in ('--follow-conf', '--gain', '--deadzone', '--no-auto-gain'):
        assert flag not in cmd


# ----------------------------------------------------------------------
# 五、--status 里带出来的日志尾巴
# ----------------------------------------------------------------------

def test_tail_returns_the_last_lines_in_order():
    """取的是最后几行，而且顺序不能反 —— 反了就看不出"最后发生了什么"。"""
    with _temp_file('a\nb\nc\nd\ne\n') as path:
        assert runmode.tail(path, lines=2) == ['d', 'e']
        assert runmode.tail(path, lines=10) == ['a', 'b', 'c', 'd', 'e']


def test_tail_skips_blank_lines():
    """空行不算一行。

    bg.log 是追加写的，一轮一轮之间会留下空行。不跳过的话，
    "最后 6 行"可能全是空行 —— 用户看到的是一段空白，
    比不说还糟。
    """
    with _temp_file('a\n\n\nb\n\n') as path:
        assert runmode.tail(path, lines=2) == ['a', 'b']


def test_tail_reads_only_the_end():
    """只从文件末尾读一小段，不整个读进内存。

    这是写这个函数的原因：bg.log 跨很多次运行一直追加，几百兆很正常，
    整个读会把 --status 这个本该立刻返回的命令拖住。所以开头那一大段
    根本不该被读到 —— 用一个开头特有的标记来证明它确实没读。
    """
    head = 'BEGIN-OF-FILE\n' + ('填\n' * 200000)      # 约 600KB
    with _temp_file(head + 'LAST-1\nLAST-2\n') as path:
        got = runmode.tail(path, lines=2)
        assert got == ['LAST-1', 'LAST-2']
        assert 'BEGIN-OF-FILE' not in got


def test_tail_survives_a_character_cut_in_half():
    """从中间截断可能把某个汉字切成两半，不能因此抛异常。

    截断点落在哪个字上完全看文件长度，是必然会遇到的情况，
    不是"万一"。日志内容本来就不重要，坏掉的那个字用替换字符顶掉就行 ——
    但要是这里抛了 UnicodeDecodeError，--status 就整个不能用。
    """
    with _temp_file('中文行\n' * 500 + '最后一行\n', binary=True) as path:
        got = runmode.tail(path, lines=1)
        assert got == ['最后一行']


def test_tail_of_a_missing_file_is_empty_not_an_error():
    """文件不存在就返回空表。

    后台从没起过的时候 bg.log 就是不存在，那正是 --status 最常见的用法。
    这里抛异常的话，用户第一次敲 --status 看到的是回溯。
    """
    assert runmode.tail('/nonexistent/definitely/not/here.log') == []


def test_status_text_says_what_to_do_when_nothing_is_running():
    """没有 pid 文件时，要直接告诉用户怎么启动。"""
    with _patched(runmode, read_pid=lambda: None):
        text = runmode.status_text()
    assert 'start.py' in text


def test_status_text_shows_the_tail_of_the_log():
    """后台在跑的时候，把日志最后几行一起打出来。

    这是"跟随到底生没生效"唯一的可见出口：默认就是后台跑，
    终端上什么都不会有。开关的开/关每次都记在日志里，
    按了右键而这里没有"右键开关：开"，就说明那一下没被认到 ——
    这一条能把"没按到"和"按到了但没跟随"直接分开，
    而这两件事的修法完全不同。
    """
    log = '旧日志\n' + runmode.switch_message(True) + '\n'
    with _temp_file(log) as path:
        with _patched(runmode, read_pid=lambda: 4242,
                      _alive=lambda pid: True, BG_LOG=path):
            text = runmode.status_text()
    assert '4242' in text
    assert runmode.switch_message(True) in text


def test_status_text_notices_a_dead_process():
    """记着 pid 但进程已经不在了 —— 不能说"在跑"，也不能只说"没跑"。

    这种情况（上次没 --stop 就关机了）最容易被误判成"程序还在后台"，
    用户会去等一个永远不会动的程序。
    """
    with _patched(runmode, read_pid=lambda: 4242, _alive=lambda pid: False):
        text = runmode.status_text()
    assert '4242' in text
    assert '重新启动' in text


# ----------------------------------------------------------------------
# 测试用的小工具
# ----------------------------------------------------------------------

class _temp_file:
    """往临时文件里写点东西，用完删掉。"""

    def __init__(self, text, binary=False):
        self._text = text
        self._binary = binary
        self.path = None

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix='.log')
        with os.fdopen(fd, 'wb' if self._binary else 'w',
                       **({} if self._binary else {'encoding': 'utf-8'})) as f:
            f.write(self._text.encode('utf-8') if self._binary else self._text)
        return self.path

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


class _patched:
    """临时换掉模块里的几个名字，出来的时候原样装回去。

    比 monkeypatch 手写一遍省事，也不会因为中途抛异常而把改动留在模块上 ——
    留着的话后跑的测试会莫名其妙地失败，查起来很费劲。
    """

    def __init__(self, module, **values):
        self._module = module
        self._new = values
        self._old = {}

    def __enter__(self):
        for k, v in self._new.items():
            self._old[k] = getattr(self._module, k)
            setattr(self._module, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            setattr(self._module, k, v)
        return False


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
