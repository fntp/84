# -*- coding: utf-8 -*-
"""
把 notebook 里的「第 3.5 步 · 兼容补丁」单元格抽成独立模块 colab/patch_yolov5.py

为什么要抽：
  原来补丁代码只存在于 make_notebook.py 的字符串里，notebook 有、脚本没有。
  现在抽成一个真文件，两个地方共用：
    1. make_notebook.py 读它，塞进 notebook 单元格
    2. run_all.py `import patch_yolov5` 直接执行
  这样永远不会出现"notebook 修了、脚本没修"的不一致。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, '..'))
NB = os.path.join(PROJ, 'colab', 'train_yolov5_colab.ipynb')
OUT = os.path.join(PROJ, 'colab', 'patch_yolov5.py')

HEADER = '''# -*- coding: utf-8 -*-
"""
YOLOv5 v7.0 兼容补丁 —— 就地修改 /content/yolov5 里的源码
============================================================
修的是 PyTorch 2.6 / NumPy 2.x / Pillow 11 / Python 3.13 的代差。

两个用法：
  1. Colab notebook 单元格：整段粘贴进去运行
  2. run_all.py 里 `import patch_yolov5` 直接执行

【为什么必须改源码，不能在 notebook 里 monkey-patch？】
  训练用的是 `!python train.py`。`!` 前缀会让 Colab 另开一个
  【全新的 Python 子进程】去跑 train.py。
  你在 notebook 内核里写 `torch.load = 包装函数`，那个子进程根本看不见。
  所以只能直接改源文件。

本脚本幂等：重复运行不会重复改。
"""
'''

MARK = '第 3.5 步 · 兼容补丁'


def main():
    with open(NB, encoding='utf-8') as f:
        nb = json.load(f)
    src = None
    for c in nb['cells']:
        if c['cell_type'] != 'code':
            continue
        s = ''.join(c['source'])
        if MARK in s and 'py_compile' in s:
            src = s
            break
    if not src:
        raise SystemExit('!! notebook 里没找到补丁单元格')

    text = HEADER + src.strip('\n') + '\n'
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print(f'写入 {OUT}')
    print(f'  {len(text.splitlines())} 行 / {len(text)} 字节')
    # 语法自检
    compile(text, OUT, 'exec')
    print('  语法检查通过 ✓')


if __name__ == '__main__':
    main()
