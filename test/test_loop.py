# -*- coding: utf-8 -*-
"""运行方式测试（不需要显卡、不需要真实抓屏）：帧号生成 + 前台后台判断。

只管两件事，都是纯计算：

    1. frame_numbers()  -- 默认一直数下去（一直监听），指定帧数就数到那儿停
    2. is_foreground()  -- 这次该在终端里跑，还是丢到后台去

这两条决定了"敲一句 start.py 到底会发生什么"，改错了很难发现
（比如又会变成只跑一帧就退），所以单独钉一下。

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

    默认值刻意跟真实的"什么都不填"一致：一直监听、没要任何输出。
    """
    base = dict(fg=False, bg=False, loop=0, coords_file=None, record=False,
                log=None, jsonl=None, stdout_coords=False, json_coords=False)
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
# 二、前台 / 后台
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
