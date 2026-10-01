# -*- coding: utf-8 -*-
"""流水线测试（不需要显卡）：前处理 -> 结构化输出（JSONL）。

test_coords.py 管的是"算得对不对"，这个文件管的是"整条链路接得上"：

    前处理   原图 -> 640x640 画布，像素值归一化到 0~1
    输出      JSONL 里必须有 timestamp / class / confidence / bbox / center

真正要跑 engine 的那部分单独放在 test_engine.py，因为那台机器
必须有显卡和 engine 文件才能跑。

运行方式（在项目根目录下，不需要 pytest）：

    C:\\Users\\fntp\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe test\\test_pipeline.py
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

# 本文件在 <项目根>\test\ 下，把 <项目根> 加到搜索路径才能 import main 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main.component.detection_output import DetectionOutput       # noqa: E402
from main.component.preprocess import preprocess                  # noqa: E402
from main.config import INPUT_SIZE                                # noqa: E402


# ----------------------------------------------------------------------
# 一、前处理
# ----------------------------------------------------------------------

def test_preprocess_output_shape_and_range():
    """输出必须是 (1, 3, 640, 640) 的 float32，且像素值在 0~1 之间。"""
    img = Image.new('RGB', (1920, 1080), (60, 60, 60))
    blob, _r, _pad = preprocess(img)

    assert blob.shape == (1, 3, INPUT_SIZE, INPUT_SIZE), blob.shape
    assert blob.dtype == np.float32
    assert float(blob.min()) >= 0.0 and float(blob.max()) <= 1.0
    img.close()


def test_preprocess_scale_and_pad_are_consistent():
    """1920x1080 按短边缩到 640 是 640x360，上下各补 (640-360)/2 = 140。"""
    img = Image.new('RGB', (1920, 1080), (0, 0, 0))
    _blob, r, pad = preprocess(img)

    assert abs(r - INPUT_SIZE / 1920) < 1e-6, r
    assert pad == (0, 140), pad
    img.close()


def test_preprocess_padding_is_gray_114():
    """灰边必须填 114/255，这是 YOLO letterbox 的标准值。

    填错了不影响形状，但会稍微影响检测精度，很难查，所以钉一下。
    """
    img = Image.new('RGB', (1280, 720), (255, 255, 255))
    blob, _r, _pad = preprocess(img)

    # 第 0 行落在上边灰条里，三个通道都该是 114/255
    assert np.allclose(blob[0, :, 0, 0], 114 / 255.0), blob[0, :, 0, 0]
    img.close()


# ----------------------------------------------------------------------
# 二、JSONL 输出
# ----------------------------------------------------------------------

@contextlib.contextmanager
def _temp_jsonl():
    """开一个临时 jsonl 路径，用完把整个临时目录删掉。"""
    d = tempfile.mkdtemp(prefix='gamemodel_test_')
    try:
        yield os.path.join(d, 'detection_output.jsonl')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_jsonl_has_required_keys():
    """一行 JSON 里必须有 timestamp 和 objects，
    每个 object 必须有 class / confidence / bbox / center。
    """
    with _temp_jsonl() as path:
        out = DetectionOutput(path, offset=(0, 0), echo=False)
        boxes = np.array([[100.0, 200.0, 300.0, 400.0]], np.float32)
        out.emit(boxes, np.array([0.9], np.float32))
        out.close()

        with open(path, 'r', encoding='utf-8') as f:
            rec = json.loads(f.readline())

        assert isinstance(rec['timestamp'], int)
        [obj] = rec['objects']
        assert set(obj) == {'class', 'confidence', 'bbox', 'center'}
        assert obj['class'] == 'person'
        assert obj['confidence'] == 0.9


def test_jsonl_bbox_and_center_share_one_offset():
    """bbox 和 center 用同一个 offset，两者口径必须一致。

    bbox 已经加过 offset，所以 center 正好等于加了 offset 的 bbox 中心。
    这里取 offset=(50, 60)，框是 [100,200,300,400]：
        bbox   = [150, 260, 350, 460]
        center = ((100+300)/2 + 50, (200+400)/2 + 60) = (250, 360)
    """
    with _temp_jsonl() as path:
        out = DetectionOutput(path, offset=(50, 60), echo=False)
        boxes = np.array([[100.0, 200.0, 300.0, 400.0]], np.float32)
        out.emit(boxes, np.array([0.8], np.float32))
        out.close()

        with open(path, 'r', encoding='utf-8') as f:
            obj = json.loads(f.readline())['objects'][0]

        assert obj['bbox'] == [150, 260, 350, 460]
        assert obj['center'] == [250, 360]
        # 两者必须自洽：center 就是 bbox 的中点
        x1, y1, x2, y2 = obj['bbox']
        assert obj['center'] == [(x1 + x2) // 2, (y1 + y2) // 2]


def test_jsonl_cleanup_keeps_recent_lines():
    """cleanup() 把文件裁到只留最近 keep_lines 行，返回丢掉的行数。"""
    with _temp_jsonl() as path:
        out = DetectionOutput(path, echo=False, keep_lines=3)
        boxes = np.array([[10.0, 20.0, 30.0, 40.0]], np.float32)
        scores = np.array([0.5], np.float32)
        for _ in range(5):
            out.emit(boxes, scores, timestamp=1)

        assert out.cleanup() == 2
        out.close()

        with open(path, 'r', encoding='utf-8') as f:
            assert len(f.readlines()) == 3


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
