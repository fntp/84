# -*- coding: utf-8 -*-
"""检测结果 -> JSONL 文件 / 控制台文字。

这个模块只做三件事：
    1. 把 boxes/scores/classes 组装成一个带时间戳的字典
    2. 需要的话追加一行 JSON 到 jsonl 文件
    3. 需要的话在控制台打印成结构化的文字

它【不】做任何鼠标/键盘/DLL 调用，只管数据。

坐标口径跟 target_center.get_target_centers 完全一致：
    bbox   是原图像素坐标 [x1, y1, x2, y2]
    center 是框中心 [cx, cy]
    传了 offset 就换算成屏幕绝对坐标（只加一次）

契约见 docs/detection_output_protocol.md。
"""

import json
import os
import time

from ..config import KEEP_LINES
from .target_center import DEFAULT_CLASS_NAMES, class_name, get_target_centers


class DetectionOutput:
    """一帧一帧地把检测结果记录下来。

    用法：
        out = DetectionOutput('out/detection_output.jsonl')   # 传 None 就不写文件
        out.emit(boxes, scores, classes)     # 每帧调一次
        out.get_centers()                    # 拿最近一帧的中心点
        out.cleanup()                        # 定期调，防止文件无限长
        out.close()                          # 结束时调
    """

    def __init__(self, jsonl_path=None, offset=(0, 0),
                 class_names=DEFAULT_CLASS_NAMES, echo=True,
                 keep_lines=KEEP_LINES):
        """参数：

        jsonl_path  写到哪个文件。None 表示不写文件，只在内存里留着。
        offset      (ox, oy) 截图区域左上角在屏幕上的位置。
        class_names 类别名，默认 ('person',)。
        echo        True 时每帧在控制台打印 bbox/center。
        keep_lines  jsonl 最多留多少行，超了从最老的开始丢。
        """
        self.offset = (int(offset[0]), int(offset[1]))
        self.class_names = class_names
        self.echo = echo
        self.keep_lines = keep_lines

        # 最近一帧的中心点，给 get_centers() 用
        self._centers = []
        self._fh = None

        if jsonl_path:
            # 目录可能还不存在，先建出来（exist_ok 保证已存在时不报错）
            os.makedirs(os.path.dirname(os.path.abspath(jsonl_path)), exist_ok=True)
            # 'a' 追加模式：多跑几次不会把上次的记录冲掉
            self._fh = open(jsonl_path, 'a', encoding='utf-8')

    # ------------------------------------------------------------------
    # 组装数据
    # ------------------------------------------------------------------

    def build(self, boxes, scores, classes=None, timestamp=None):
        """把一帧的检测结果组装成一个字典。

        返回：
            {'timestamp': 1790824114320,
             'objects': [{'class': 'person', 'confidence': 0.964,
                          'bbox': [694, 572, 946, 1078],
                          'center': [820, 825]}]}

        多目标时 objects 里会有多项，全部保留。

        注意：confidence 取自 centers，也就是 get_target_centers() 去重
        之后的值，不在这里另算一遍 —— 否则同一帧会出现两套不一样的数。
        """
        # 毫秒时间戳。传了就用传的，方便回放/对齐
        ts = int(time.time() * 1000) if timestamp is None else timestamp

        # 中心点和 bbox 共用同一个 offset，保证两者口径一致
        centers = get_target_centers(boxes, scores, classes, self.offset)
        ox, oy = self.offset

        objects = []
        for i, box in enumerate(boxes):
            x1, y1, x2, y2 = (float(v) for v in box[:4])
            cid = 0 if classes is None else classes[i]
            objects.append({
                'class': class_name(cid, self.class_names),
                'confidence': centers[i]['confidence'],
                'bbox': [int(round(x1)) + ox, int(round(y1)) + oy,
                         int(round(x2)) + ox, int(round(y2)) + oy],
                'center': [centers[i]['x'], centers[i]['y']],
            })

        return {'timestamp': ts, 'objects': objects}

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def emit(self, boxes, scores, classes=None, timestamp=None):
        """处理一帧：记下中心点，写 jsonl，打印。返回组装好的字典。"""
        data = self.build(boxes, scores, classes, timestamp)

        # 缓存这一帧的中心点
        self._centers = [{'x': o['center'][0],
                          'y': o['center'][1],
                          'confidence': o['confidence']}
                         for o in data['objects']]

        if self._fh:
            # ensure_ascii=False 让中文原样写出，不转成 \uXXXX
            self._fh.write(json.dumps(data, ensure_ascii=False) + '\n')
            self._fh.flush()

        if self.echo:
            for line in self.format_console(data):
                print(line)

        return data

    def format_console(self, data):
        """把一帧数据排版成给人看的几行文字。

        格式（每个目标三段）：
            person
            bbox:
            694,572,946,1078
            center:
            820,825
        """
        lines = []
        for obj in data['objects']:
            x1, y1, x2, y2 = obj['bbox']
            cx, cy = obj['center']
            lines.append(f'    {obj["class"]}')
            lines.append('    bbox:')
            lines.append(f'    {x1},{y1},{x2},{y2}')
            lines.append('    center:')
            lines.append(f'    {cx},{cy}')
        return lines

    def get_centers(self):
        """拿最近一帧的中心点列表。返回的是副本，改它不影响内部状态。"""
        return [dict(c) for c in self._centers]

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self):
        """把 jsonl 裁到只留最近 keep_lines 行，返回丢掉了多少行。

        防止长时间运行时文件无限增长。没开文件时直接返回 0。
        """
        if not self._fh:
            return 0

        path = self._fh.name
        self._fh.flush()

        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        dropped = max(0, len(lines) - self.keep_lines)
        if dropped:
            # 先关掉句柄，再整体重写，最后重新打开追加
            self._fh.close()
            with open(path, 'w', encoding='utf-8') as f:
                f.writelines(lines[-self.keep_lines:])
            self._fh = open(path, 'a', encoding='utf-8')

        return dropped

    def close(self):
        """关掉文件。重复调用是安全的。"""
        if self._fh:
            self._fh.close()
            self._fh = None
