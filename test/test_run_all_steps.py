# -*- coding: utf-8 -*-
"""
本地验证 colab/run_all.py 里「不依赖 Colab 环境」的那几步：

    第 5 步  s5_patch       缺 patch_yolov5.py 时从 zip 自愈
    第 6 步  s6_fix_paths   修 data.yaml 的绝对路径 + 列表文件 ./ 前缀
    第 7 步  s7_verify      校验路径
    第 8 步前 drive_restore / DriveMirror   断点续训

这几步是最容易出错、也最贵的（错了要等十几分钟才在 Colab 上发现），
所以拿真数据集在本地先跑一遍。

第 1/3/4/8/9 步依赖 GPU / torch / 网络，本地没法测，只能靠 Colab。
"""
import glob
import os
import pathlib
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(PROJ, 'colab'))

import run_all  # noqa: E402

REAL = r'C:\Users\fntp\Pictures\Screenshots\84\yolo_dataset'
CFG = ['data.yaml', 'data_nomeonly.yaml',
       'train_no_me_only.txt', 'val_no_me_only.txt', 'test_no_me_only.txt']

fails = []


def check(cond, msg):
    print(f'  {"✓" if cond else "✗"} {msg}')
    if not cond:
        fails.append(msg)


def test_rewrite():
    print('\n=== 第 6 步 s6_fix_paths · 改写测试 ===')
    tmp = tempfile.mkdtemp(prefix='yolo_s6_')
    for f in CFG:
        shutil.copy(os.path.join(REAL, f), tmp)
    for sp in ('train', 'val', 'test'):
        os.makedirs(os.path.join(tmp, 'images', sp), exist_ok=True)

    run_all.DATA_DIR = tmp
    args = types.SimpleNamespace(data='data.yaml')
    run_all.s6_fix_paths(args)

    txt = open(os.path.join(tmp, 'data.yaml'), encoding='utf-8').read()
    print('  --- 改写后的 data.yaml ---')
    for line in txt.strip().splitlines():
        print('   ', line)
    check(f'path: {tmp}' in txt, 'path 被改写成本地临时目录（绝对路径）')
    check('train: images/train' in txt, 'train 字段保留')
    check('val: images/val' in txt, 'val 字段保留')
    check('test: images/test' in txt, 'test 字段保留')
    check('nc: 1' in txt, 'nc: 1 保留')
    check('names: [person]' in txt, 'names 保留')

    t2 = open(os.path.join(tmp, 'data_nomeonly.yaml'), encoding='utf-8').read()
    check('train: train_no_me_only.txt' in t2, 'nomeonly 的 train 指向 txt 而非目录')

    for sp in ('train', 'val', 'test'):
        p = os.path.join(tmp, f'{sp}_no_me_only.txt')
        lines = [l for l in open(p, encoding='utf-8').read().splitlines() if l.strip()]
        check(all(l.startswith('./') for l in lines),
              f'{sp}_no_me_only.txt 全部 {len(lines)} 条带 ./ 前缀')

    # 幂等：再跑一次不应再改
    before = open(os.path.join(tmp, 'data.yaml'), encoding='utf-8').read()
    run_all.s6_fix_paths(types.SimpleNamespace(data='data.yaml'))
    after = open(os.path.join(tmp, 'data.yaml'), encoding='utf-8').read()
    check(before == after, '第 6 步幂等（重复跑内容不变）')

    shutil.rmtree(tmp, ignore_errors=True)


def test_verify():
    print('\n=== 第 7 步 s7_verify · 真实数据集校验 ===')
    for yname in ('data.yaml', 'data_nomeonly.yaml'):
        run_all.DATA_DIR = REAL
        args = types.SimpleNamespace(data=yname)
        print(f'  --- {yname} ---')
        try:
            run_all.s7_verify(args)
            ok = True
        except SystemExit:
            ok = False
        check(ok, f'{yname} 校验通过（未触发 sys.exit）')


def test_patch_selfheal():
    """复现用户那次报错：zip 里的散件没解出来，第 5 步 import 失败。"""
    print('\n=== 第 5 步 s5_patch · 缺 patch_yolov5.py 时自愈 ===')
    import zipfile

    colab_dir = os.path.abspath(os.path.join(PROJ, 'colab'))
    saved_path = list(sys.path)
    saved_mod = sys.modules.pop('patch_yolov5', None)
    saved_content = run_all.CONTENT
    tmp = tempfile.mkdtemp(prefix='yolo_s5_')
    try:
        # 屏蔽 colab/ 目录，否则会误导入真补丁（它断言 /content/yolov5 存在）
        sys.path[:] = [p for p in sys.path
                       if os.path.abspath(p or '.') != colab_dir]
        run_all.CONTENT = tmp

        # 现场 A：既没补丁也没 zip → 必须明确退出，不能静默继续
        try:
            run_all.s5_patch()
            gone = False
        except SystemExit:
            gone = True
        check(gone, '补丁和 zip 都没有时明确退出（不静默继续）')

        # 现场 B：有 zip 但没解压 ← 用户遇到的
        stub = os.path.join(tmp, '_stub_patch_src.py')
        with open(stub, 'w', encoding='utf-8') as f:
            f.write('print("STUB PATCH RAN")\n')
        with zipfile.ZipFile(os.path.join(tmp, 'yolo_all_in_one.zip'), 'w') as zf:
            zf.write(stub, 'patch_yolov5.py')

        sys.modules.pop('patch_yolov5', None)
        try:
            run_all.s5_patch()
            healed = True
        except SystemExit:
            healed = False
        check(healed, '只有 zip 时能从 zip 里取出补丁并继续')
        check(os.path.isfile(os.path.join(tmp, 'patch_yolov5.py')),
              'patch_yolov5.py 已解到 /content 等价目录')
        check('patch_yolov5' in sys.modules, '补丁模块确实被导入执行')
    finally:
        sys.path[:] = saved_path
        run_all.CONTENT = saved_content
        sys.modules.pop('patch_yolov5', None)
        if saved_mod is not None:
            sys.modules['patch_yolov5'] = saved_mod
        shutil.rmtree(tmp, ignore_errors=True)


def test_patch_getsize():
    """补丁 2b：Pillow 10 删了 FreeTypeFont.getsize，utils/plots.py:91 会抛
    AttributeError，每个 batch 刷一次堆栈（训练不断，但 train_batch*.jpg 出不来）。

    patch_yolov5.py 顶层断言 /content/yolov5 存在，Windows 上没法 import，
    所以只把「2b 那一段」源码切出来在沙箱里 exec。
    """
    print('\n=== 补丁 2b · FreeTypeFont.getsize -> getbbox ===')
    import py_compile
    import re as _re

    src = open(os.path.join(PROJ, 'colab', 'patch_yolov5.py'),
               encoding='utf-8').read()
    beg = src.index('# ---- 2b)')
    end = src.index('# ---- 3)')
    ns = {'re': _re, 'apply': lambda name, fn: None}
    exec(compile(src[beg:end], 'patch_2b', 'exec'), ns)
    fix = ns['_fix_font_getsize']

    demo = ('class Annotator:\n'
            '    def box_label(self, label):\n'
            '        w, h = self.font.getsize(label)  # text width, height\n'
            '        return w, h\n')
    out, n = fix(demo)
    check(n == 1, 'getsize 那行被改写 1 处')
    check('try:' in out and 'except AttributeError:' in out,
          '包上 try/except AttributeError（Pillow 10 才不炸）')
    check('self.font.getbbox(label)' in out, '回退到 getbbox()')
    check('_bb[2] - _bb[0]' in out and '_bb[3] - _bb[1]' in out,
          '由 getbbox 的 (l, t, r, b) 换算宽高')
    check(out.count('.getsize(') == 1,
          '老的 getsize 调用还留着（Pillow 9 走它，行为不变）')

    # 缩进要是被搞坏，补丁就从「报错」变成「语法错误」，更糟
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'plots.py')
        open(p, 'w', encoding='utf-8').write(out)
        try:
            py_compile.compile(p, doraise=True)
            ok = True
        except py_compile.PyCompileError:
            ok = False
        check(ok, '改写后仍是合法 Python（缩进没被搞坏）')

    out2, n2 = fix(out)
    check(n2 == 0 and out2 == out, '第 2b 步幂等（重复跑不再套一层）')

    # Annotator.text 里是 8 空格缩进，也要认
    deep = 'def f(self):\n    if x:\n        w, h = self.font.getsize(text)\n'
    out3, n3 = fix(deep)
    check(n3 == 1 and '\n        try:' in out3, '深层缩进（8 空格）也能匹配')


def test_resume():
    """断线续训：DriveMirror 抄 checkpoint / drive_restore 恢复并接上 --resume。

    Colab 免费版断线会清空 /content，所以这套逻辑错了的代价是"白练几小时"。
    """
    print('\n=== 第 8 步前 · 断点续训（DriveMirror + drive_restore）===')
    saved = (run_all.CONTENT, run_all.DRIVE_MNT)
    tmp = tempfile.mkdtemp(prefix='yolo_resume_')
    try:
        content = os.path.join(tmp, 'content')
        mnt = os.path.join(tmp, 'drive', 'MyDrive')
        name = 'farlight_person_s'

        run_all.CONTENT = content
        run_all.DRIVE_MNT = mnt

        # --- Drive 没挂：必须提醒，但不能报错 ---
        run_all.DRIVE_MNT = os.path.join(tmp, 'not_mounted')
        check(run_all.drive_root() is None, 'Drive 没挂时 drive_root() 返回 None')
        check(run_all.drive_restore(types.SimpleNamespace(
            name=name, epochs=150, no_drive=False, no_resume=False)) is None,
            'Drive 没挂时 drive_restore() 返回 None（降级为从头练，不崩）')
        run_all.DRIVE_MNT = mnt

        os.makedirs(mnt, exist_ok=True)          # 假装 Drive 已挂载
        src = run_all.run_dir_of(name)
        os.makedirs(os.path.join(src, 'weights'), exist_ok=True)

        check(run_all.drive_root() is not None, 'Drive 挂载点存在时 drive_root() 可用')
        check(run_all.mirror_of(name).endswith(
            os.path.join('yolo_farlight', 'runs', 'train', name)),
            'mirror_of() 落在 yolo_farlight/runs/train/<name> 下')

        # 最关键的不变量：opt.yaml 不跟着 last.pt 走，--resume 就接不上
        check('weights/last.pt' in run_all.SYNC_REL and 'opt.yaml' in run_all.SYNC_REL,
              'SYNC_REL 同时含 weights/last.pt 和 opt.yaml（--resume 的硬要求）')

        # --- 造一轮训练产物（照着 YOLOv5 真实写出的文件）---
        (pathlib.Path(src) / 'opt.yaml').write_text(
            f'epochs: 150\nname: {name}\n', encoding='utf-8')
        (pathlib.Path(src) / 'hyp.yaml').write_text('lr0: 0.01\n', encoding='utf-8')
        (pathlib.Path(src) / 'results.csv').write_text(
            'epoch,loss\n0,1.0\n7,0.5\n8,0.4\n9,0.3\n', encoding='utf-8')
        (pathlib.Path(src) / 'weights' / 'last.pt').write_bytes(b'LAST' * 100)
        (pathlib.Path(src) / 'weights' / 'best.pt').write_bytes(b'BEST' * 50)

        empty = os.path.join(tmp, 'empty_src')
        os.makedirs(empty, exist_ok=True)
        m0 = run_all.DriveMirror(empty, os.path.join(tmp, 'dst0'), 1)
        check(m0._stamp() == 0.0, '什么都没产出时 _stamp() == 0（线程不会空转抄空目录）')

        # --- 镜像 ---
        m = run_all.DriveMirror(src, run_all.mirror_of(name), 1)
        m.sync_once()
        dst = run_all.mirror_of(name)
        for rel in ('opt.yaml', 'hyp.yaml', 'results.csv',
                    'weights/last.pt', 'weights/best.pt'):
            check(os.path.isfile(os.path.join(dst, rel)), f'镜像到 Drive: {rel}')
        check(not glob.glob(os.path.join(dst, '**', '*.part'), recursive=True),
              '没有残留 .part 半成品')
        check(open(os.path.join(dst, 'weights', 'last.pt'), 'rb').read() ==
              open(os.path.join(src, 'weights', 'last.pt'), 'rb').read(),
              'last.pt 内容与源一致')
        check(m.rounds == 1, 'rounds 计数 +1')

        s1 = m._stamp()
        os.utime(os.path.join(src, 'results.csv'), (s1 + 10, s1 + 10))
        check(m._stamp() == s1 + 10, 'results.csv 更新后 _stamp() 跟着变（能触发下一轮同步）')

        check(run_all.drive_epochs(name) == 9,
              'drive_epochs() 读出 results.csv 里最大的 epoch = 9')

        # --- drive_restore 各分支 ---
        check(run_all.drive_restore(types.SimpleNamespace(
            name=name, epochs=150, no_drive=True, no_resume=False)) is None,
            '--no-drive：不恢复')
        check(run_all.drive_restore(types.SimpleNamespace(
            name=name, epochs=150, no_drive=False, no_resume=True)) is None,
            '--no-resume：不恢复')

        # --- 模拟断线：/content 被整个清空，只剩 Drive ---
        shutil.rmtree(content)
        check(not os.path.isdir(run_all.run_dir_of(name)),
              '/content 已清空（模拟 Colab 断线）')

        got = run_all.drive_restore(types.SimpleNamespace(
            name=name, epochs=150, no_drive=False, no_resume=False))
        local_last = os.path.join(run_all.weights_of(name), 'last.pt')
        check(got == local_last, 'drive_restore() 返回本地 last.pt（给 --resume 用）')
        check(os.path.isfile(local_last), 'last.pt 已从 Drive 抄回本地')
        check(os.path.isfile(os.path.join(run_all.run_dir_of(name), 'opt.yaml')),
              'opt.yaml 也抄回了本地 —— 否则 --resume 读不到原始参数')

        check(run_all.drive_restore(types.SimpleNamespace(
            name=name, epochs=150, no_drive=False, no_resume=False)) == local_last,
            '本地已有 checkpoint 时直接用本地的（不重复抄）')

        # 已经练满 → 明确退出，别让人白等两小时
        shutil.rmtree(run_all.run_dir_of(name))
        try:
            run_all.drive_restore(types.SimpleNamespace(
                name=name, epochs=9, no_drive=False, no_resume=False))
            stopped = False
        except SystemExit:
            stopped = True
        check(stopped, 'Drive 上已练满 --epochs 时明确退出（不重复练）')

        check(run_all.drive_restore(types.SimpleNamespace(
            name='never_trained', epochs=150, no_drive=False,
            no_resume=False)) is None, 'Drive 上没有这个 run 时返回 None（首次训练）')
    finally:
        run_all.CONTENT, run_all.DRIVE_MNT = saved
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_patch_getsize()
    test_patch_selfheal()
    test_resume()
    if not os.path.isdir(REAL):
        print(f'\n⚠️ 数据集不存在，跳过第 6/7 步: {REAL}')
    else:
        test_rewrite()
        test_verify()
    print()
    print('=' * 66)
    print('结论:', '✅ run_all.py 第 5/6/7 步 + 断点续训逻辑正确'
          if not fails else f'❌ {len(fails)} 项失败')
    for f in fails:
        print('   -', f)
    print('=' * 66)
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
