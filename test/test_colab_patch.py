# -*- coding: utf-8 -*-
"""
验证 Colab notebook 里「第 3.5 步 · 兼容补丁」那一格到底能不能跑通。

做法：
  1. 从 colab/train_yolov5_colab.ipynb 里抠出那一格的源码
  2. 把 /content/yolov5 换成 _ref_aimbot/v7full_patchtest（v7.0 完整源码的副本）
  3. 原样 exec 这一格
  4. 检查：语法自检是否通过、改动是否符合预期、重复跑是否幂等

这样能在用户点 Colab 之前，先把正则写错、缩进写错之类的低级错误挡掉。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
NB = os.path.join(PROJ, "colab", "train_yolov5_colab.ipynb")
SRC = os.path.join(PROJ, "_ref_aimbot", "v7full")
DST = os.path.join(PROJ, "_ref_aimbot", "v7full_patchtest")

MARK = "第 3.5 步 · 兼容补丁"


def extract_cell():
    with open(NB, "r", encoding="utf-8") as f:
        nb = json.load(f)
    for c in nb["cells"]:
        if c["cell_type"] != "code":
            continue
        src = "".join(c["source"])
        if MARK in src and "import pathlib, re, py_compile" in src:
            return src
    raise SystemExit("!! 没找到补丁单元格")


def fresh_copy():
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(DST)
    n = 0
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            s = os.path.join(dirpath, fn)
            r = os.path.relpath(s, SRC)
            d = os.path.join(DST, r)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            n += 1
    return n


def count_occurrences(root, needle):
    tot = 0
    where = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                t = f.read()
            k = t.count(needle)
            if k:
                tot += k
                where.append((os.path.relpath(p, root).replace("\\", "/"), k))
    return tot, where


def install_fake_torch():
    """本地没装 torch，塞一个桩进去，好让补丁格的 torch.amp 分支也能被测到。"""
    import types
    if "torch" in sys.modules:
        return
    m = types.ModuleType("torch")
    m.__version__ = "2.6.0+cu124 (stub)"
    amp = types.ModuleType("torch.amp")
    amp.autocast = object()
    amp.GradScaler = object()
    m.amp = amp
    cuda = types.ModuleType("torch.cuda")
    cuda.is_available = lambda: False
    m.cuda = cuda
    sys.modules["torch"] = m
    sys.modules["torch.amp"] = amp
    sys.modules["torch.cuda"] = cuda


def run_cell(src):
    print("=" * 70)
    print(">>> 执行补丁单元格")
    print("=" * 70)
    install_fake_torch()
    g = {"__name__": "__main__"}
    exec(compile(src, "<patch_cell>", "exec"), g)
    return g


def main():
    cell = extract_cell()
    print(f"[ok ] 从 notebook 抠出补丁单元格：{len(cell.splitlines())} 行")

    tmp_pyc = os.path.join(tempfile.gettempdir(), "_pyc_check.pyc").replace("\\", "/")
    cell = cell.replace("'/content/yolov5'", repr(DST.replace("\\", "/")))
    cell = cell.replace("'/tmp/_pyc_check.pyc'", repr(tmp_pyc))

    n = fresh_copy()
    print(f"[ok ] 复制 v7.0 源码 {n} 个 .py 到测试目录")
    print(f"     {DST}")
    print()

    before_load, _ = count_occurrences(DST, "torch.load(")
    before_wonly, _ = count_occurrences(DST, "weights_only=False")
    before_pil, pil_where = count_occurrences(DST, "Image.ROTATE_")
    before_npfloat, _ = count_occurrences(DST, "np.float)")
    print(f"[in ] 补丁前: torch.load={before_load}  weights_only=False={before_wonly} "
          f" Image.ROTATE_={before_pil}  np.float)={before_npfloat}")
    print()

    run_cell(cell)

    print()
    print("=" * 70)
    print(">>> 校验结果")
    print("=" * 70)
    after_load, _ = count_occurrences(DST, "torch.load(")
    after_wonly, _ = count_occurrences(DST, "weights_only=False")
    after_pil, _ = count_occurrences(DST, "Image.ROTATE_")
    after_npfloat, _ = count_occurrences(DST, "np.float)")
    print(f"[out] 补丁后: torch.load={after_load}  weights_only=False={after_wonly} "
          f" Image.ROTATE_={after_pil}  np.float)={after_npfloat}")

    ok = True
    if after_wonly != before_load:
        print(f"  ✗ weights_only 数量 {after_wonly} != torch.load 数量 {before_load}")
        ok = False
    else:
        print(f"  ✓ {before_load} 处 torch.load 全部补上 weights_only=False")
    if after_pil != 0:
        print(f"  ✗ 还有 {after_pil} 处 Image.ROTATE_*")
        ok = False
    else:
        print("  ✓ Image.ROTATE_* 已全部改写")
    if after_npfloat != 0:
        print(f"  ✗ 还有 {after_npfloat} 处 np.float)")
        ok = False
    else:
        print("  ✓ np.float) 已全部改写")

    # ---- 幂等性：再跑一次，文件内容不应再变 ----
    snap1 = {}
    for dirpath, _, filenames in os.walk(DST):
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                snap1[p] = open(p, "r", encoding="utf-8").read()

    print()
    print("=" * 70)
    print(">>> 第二次执行（幂等性检查）")
    print("=" * 70)
    run_cell(cell)

    changed = [p for p, t in snap1.items()
               if open(p, "r", encoding="utf-8").read() != t]
    print()
    if changed:
        print(f"  ✗ 第二次执行又改了 {len(changed)} 个文件，不是幂等的：")
        for p in changed[:10]:
            print("     ", os.path.relpath(p, DST))
        ok = False
    else:
        print("  ✓ 幂等：第二次执行没有任何文件被改动")

    print()
    print("=" * 70)
    print("结论:", "✅ 补丁可用" if ok else "❌ 补丁有问题，需要修")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
