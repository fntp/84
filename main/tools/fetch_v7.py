# -*- coding: utf-8 -*-
"""
拉取 ultralytics/yolov5 v7.0 完整源码到本地，用于【只读静态扫描】。

为什么要绕这一圈：
  raw.githubusercontent.com  -> 被墙 (HTTP 000)
  codeload.github.com        -> 被墙 (HTTP 000)
  api.github.com             -> 能通，但匿名限额 60 次/小时，拉 55 个文件会 403
  cdn.jsdelivr.net           -> 可直连，且无严格限额   <== 用这个
  data.jsdelivr.com          -> 一次返回整棵树（flat 结构）

用途：把 v7.0 全部 .py + requirements.txt 拉到本地，然后静态扫描出
      在 2026 年的 Colab（Python 3.13 / torch 2.6 / numpy 2 / Pillow 11）
      上会炸的老 API，一次性把兼容补丁写全，避免"修一个蹦一个"。
"""
import json
import os
import sys
import time
import urllib.request

TAG = "v7.0"
REPO = "ultralytics/yolov5"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@{TAG}"
LIST_API = f"https://data.jsdelivr.com/v1/packages/gh/{REPO}@{TAG}?structure=flat"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "_ref_aimbot", "v7full"))

WANT_EXT = (".py",)
WANT_EXACT = {"requirements.txt"}


def fetch(url, retry=3, binary=True):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for i in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return data if binary else data.decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"GET 失败 {url}: {last}")


def main():
    os.makedirs(OUT, exist_ok=True)

    list_path = os.path.join(OUT, "_files.json")
    if os.path.exists(list_path):
        with open(list_path, "r", encoding="utf-8") as f:
            listing = json.load(f)
        print(f"[cache] 复用文件清单 {list_path}")
    else:
        print("[cdn ] 拉取文件清单 ...")
        listing = json.loads(fetch(LIST_API, binary=False))
        with open(list_path, "w", encoding="utf-8") as f:
            json.dump(listing, f, ensure_ascii=False, indent=1)

    allfiles = [n["name"] for n in listing["files"]]
    want = [p for p in allfiles
            if p.endswith(WANT_EXT) or os.path.basename(p) in WANT_EXACT]

    print(f"[info] 仓库共 {len(allfiles)} 个文件，需下载 {len(want)} 个")
    print(f"[out ] {OUT}")

    ok = skip = fail = 0
    for i, p in enumerate(sorted(want), 1):
        rel = p.lstrip("/")
        dst = os.path.join(OUT, rel.replace("/", os.sep))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            skip += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            raw = fetch(f"{CDN}{p}")
            with open(dst, "wb") as f:
                f.write(raw)
            ok += 1
            print(f"  [{i:>2}/{len(want)}] {rel}  ({len(raw)} B)")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i:>2}/{len(want)}] !! {rel}  {e}")
        time.sleep(0.15)

    print(f"\n[done] 新下载 {ok} / 已存在 {skip} / 失败 {fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
