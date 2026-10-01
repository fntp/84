# -*- coding: utf-8 -*-
"""调用点检查：改了函数签名，调用那行忘了跟着改 —— 静态扫出来。

为什么需要这个：
    main/app.py 【在这台机器上 import 不起来】（detector.py 顶层就要
    tensorrt，这台机器上没有），所以它的代码路径一次都跑不到。
    改 _print_banner 的时候给定义加了 follower 参数、调用那行没改，
    测试全绿、本机毫无反应，一直到用户在 Windows 上启动才炸 TypeError。

    这个文件不 import 任何业务模块，只 ast 解析源码，专门盯这类
    "少传/多传参数"，跟能不能 import 无关。

不查什么：
    带 *args / **kwargs 的函数跳过（数量本来就不定）；通过别的名字
    （比如先赋给变量再调）的调用也认不出来。查不全，但零成本、零依赖。
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _definitions(tree):
    """全文件的函数定义 -> {名字: (参数名表, 必填个数)}。

    嵌套函数也算进来：同一个模块里同名函数不止一个时，后一个覆盖前一个，
    可能误报也可能漏报 —— 这里宁可漏，不要误报。
    """
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            names = [x.arg for x in a.posonlyargs + a.args]
            out[n.name] = (names, len(names) - len(a.defaults),
                           a.vararg is not None, a.kwarg is not None)
    return out


def _missing_args(tree):
    """返回 [(行号, 函数名, 缺的参数名), ...]。"""
    defs = _definitions(tree)
    found = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Name):
            continue
        if n.func.id not in defs:
            continue
        names, required, has_varargs, has_kwargs = defs[n.func.id]
        # 参数数量不定，或者调用方把关键字字典展开了，都判不出来
        if has_varargs or has_kwargs or any(k.arg is None for k in n.keywords):
            continue

        filled = set(names[:len(n.args)])
        filled.update(k.arg for k in n.keywords)
        missing = [x for x in names[:required] if x not in filled]
        if missing:
            found.append((n.lineno, n.func.id, missing))
    return found


def _sources():
    """把要被检查的 .py 都找出来（跳过 out\\ 里的东西和本文件自己）。"""
    skip = {'.git', 'out', '__pycache__'}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if fn.endswith('.py'):
                yield os.path.join(dirpath, fn)


def test_every_call_passes_the_required_arguments():
    """全项目扫一遍，不允许"少了必填参数"的调用。"""
    problems = []
    for path in _sources():
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
        for line, name, missing in _missing_args(tree):
            rel = os.path.relpath(path, ROOT)
            problems.append(f'{rel}:{line} 调用 {name}() 少了参数 {missing}')

    assert not problems, '有调用点的参数对不上定义：\n  ' + '\n  '.join(problems)


def test_the_checker_actually_catches_a_missing_argument():
    """反例：故意写一句少传参数的调用，检查器必须报出来。

    没有这条，上面那个测试在检查器整个坏掉的时候也照样"通过"。
    """
    src = (
        'def f(a, b, c):\n'
        '    return a\n'
        '\n'
        'f(1, 2)\n'
        'f(1, 2, 3)\n'
        'f(a=1, b=2, c=3)\n'
    )
    found = _missing_args(ast.parse(src))
    assert found == [(4, 'f', ['c'])], found


# ----------------------------------------------------------------------
# 迷你测试运行器（没有 pytest 时的退路）
# ----------------------------------------------------------------------

def _run_all():
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
