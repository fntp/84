# -*- coding: utf-8 -*-
"""置信度去重测试：多个目标分数撞车时，怎么把它们拆开。

需求方原话（就是这个算法的规格）：

    "在 0.1~0.2 之间取一个随机数，精确度是 0.001。
     把重复的数据挨个去减……第几个重复的，就减去随机数乘以第几。
     最大值不能超过 100%。"

落地成代码就是 target_center._dedupe_scores()：
    先按分数从高到低走一遍，某一档跟前面撞上（cand >= prev），
    就减掉【同一个】随机步长 r 的一个倍数，r 每次调用只抽一次。

这里要钉死四件事，任何一条破了都算 bug：

    1. 去重后【两两不相等】（这是这个函数存在的唯一理由）
    2. 只减不加，所以【永远不超过原来的分数】（也就永远不超过 100%）
    3. 一次调用里所有目标共用同一个 r，且 r 落在 [0.100, 0.200]，
       精确到 0.001
    4. 返回顺序 = 输入顺序（不能因为内部排过序就把结果顺序搞乱）

运行方式（在项目根目录下，不需要 pytest）：

    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe test\\test_dedupe.py
"""

import os
import sys

import numpy as np

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.target_center import (                       # noqa: E402
    DEDUPE_STEP_MAX,
    DEDUPE_STEP_MIN,
    _dedupe_scores,
    get_target_centers,
)


def _step_of(values):
    """从一串去重结果里反推出这次抽到的随机步长 r。

    结果一定是 a, a-r, a-2r ... 这样的等差下降，
    所以"最大的减第二大的"就是 r。
    """
    desc = sorted(values, reverse=True)
    return round(desc[0] - desc[1], 3), desc


# ----------------------------------------------------------------------
# 一、基本形状
# ----------------------------------------------------------------------

def test_dedupe_leaves_single_target_alone():
    """只有一个目标（或者一个都没有）时不该动分数。

    这条很关键：单目标是最常见的情况，动了就等于把所有历史数据都改了。
    """
    assert _dedupe_scores([0.9]) == [0.9]
    assert _dedupe_scores([]) == []


def test_dedupe_five_identical_scores_become_distinct():
    """需求方说的"最多同时四五个重复"—— 五个一模一样的分数必须全拆开。"""
    out = _dedupe_scores([0.8] * 5)
    assert len(out) == 5
    assert len(set(out)) == 5, out


def test_dedupe_mixed_batch_all_distinct():
    """一半重复一半不重复的批次，去重后仍然两两不同。"""
    scores = [0.5, 0.9, 0.5, 0.9, 0.5]
    out = _dedupe_scores(scores)
    assert len(set(out)) == 5, out


# ----------------------------------------------------------------------
# 二、步长：0.001 精度、落在 [0.100, 0.200]、全批共用一个
# ----------------------------------------------------------------------

def test_step_precision_and_range():
    """抽到的 r 必须落在 [0.100, 0.200]，而且精确到 0.001（千分位是整数）。"""
    step, _desc = _step_of(_dedupe_scores([0.9] * 5))
    assert DEDUPE_STEP_MIN / 1000.0 <= step <= DEDUPE_STEP_MAX / 1000.0, step
    assert abs(step * 1000 - round(step * 1000)) < 1e-9, step


def test_all_gaps_use_the_same_step():
    """5 个重复分数减出来的是等差序列：相邻差值必须完全相等。

    每个都重新抽一次随机数也能做到"不重复"，但那不是需求要的东西
    （需求原话是"减去随机数乘以第几"，乘数才是第几，随机数只有一个）。
    """
    step, desc = _step_of(_dedupe_scores([0.9] * 5))
    for i in range(len(desc) - 1):
        gap = round(desc[i] - desc[i + 1], 3)
        assert abs(gap - step) < 1e-6, (desc, step, gap)


# ----------------------------------------------------------------------
# 三、只减不加 / 顺序不变
# ----------------------------------------------------------------------

def test_dedupe_never_exceeds_original_scores():
    """只减不加 —— 所以每个结果都不超过它原来的分数，也不会超过 100%。"""
    scores = [0.9] * 5
    out = _dedupe_scores(scores)
    assert max(out) <= max(scores)
    for got, before in zip(out, scores):
        assert got <= before + 1e-9, (got, before)


def test_dedupe_keeps_input_order_and_max_stays_put():
    """返回顺序必须跟输入一致，且第一个出现的最高分保持原值。"""
    scores = [0.4, 0.9, 0.4, 0.9]
    out = _dedupe_scores(scores)

    assert out[1] == 0.9        # 下标 1 是第一个 0.9，原值不动
    assert out[3] < 0.9         # 下标 3 是重复的那个，被减下去
    assert out[0] == 0.4        # 0.4 这一档的第一个也保持原值
    assert out[2] < 0.4         # 重复的那个被减
    assert len(set(out)) == 4, out


def test_dedupe_holds_up_over_many_runs():
    """随机步长意味着每次结果都不一样，跑几百次都不能出现撞车或超标。"""
    for _ in range(300):
        out = _dedupe_scores([0.77] * 5)
        assert len(set(out)) == 5, out
        assert max(out) <= 0.77, out
        step, desc = _step_of(out)
        assert DEDUPE_STEP_MIN / 1000.0 <= step <= DEDUPE_STEP_MAX / 1000.0, step
        for i in range(len(desc) - 1):
            assert abs(round(desc[i] - desc[i + 1], 3) - step) < 1e-6, desc


# ----------------------------------------------------------------------
# 四、接到真实输出上：坐标不受影响，只有 confidence 被拆开
# ----------------------------------------------------------------------

def test_centers_confidence_split_but_coords_untouched():
    """走完整条链路再看一眼：去重只碰 confidence，坐标一个像素都不能变。"""
    boxes = np.array([[0.0, 0.0, 100.0, 100.0],
                      [200.0, 200.0, 300.0, 300.0]], np.float32)
    scores = np.array([0.9, 0.9], np.float32)

    ts = get_target_centers(boxes, scores)

    assert [t['x'] for t in ts] == [50, 250]
    assert [t['y'] for t in ts] == [50, 250]
    assert len({t['confidence'] for t in ts}) == 2, ts


def test_centers_five_targets_five_distinct_confidences():
    """五个目标全撞在 0.8 上，输出数组里必须是五个不同的分数。"""
    boxes = np.array([[i * 10.0, 0.0, i * 10.0 + 4.0, 4.0] for i in range(5)],
                     np.float32)
    scores = np.array([0.8] * 5, np.float32)

    confs = [t['confidence'] for t in get_target_centers(boxes, scores)]
    assert len(set(confs)) == 5, confs


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
