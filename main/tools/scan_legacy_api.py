# -*- coding: utf-8 -*-
"""
静态扫描：找出源码里在当前环境（Python 3.13 / PyTorch 2.6 / NumPy 2 / Pillow 11 /
matplotlib 3.10）已被【删除或改名】的 API。

只读，不修改任何文件。

用法：
    python tools/scan_legacy_api.py <要扫描的目录>
"""
import os
import re
import sys

# (正则, 说明, 建议替换)   —— 顺序即输出顺序
RULES = [
    # ---- PyTorch 2.6 ----
    (r"torch\.load\(",
     "torch.load 默认 weights_only 由 False 改成 True（torch 2.6）",
     "加 weights_only=False，或改用 torch.serialization.add_safe_globals"),
    (r"torch\.cuda\.amp\.GradScaler",
     "torch.cuda.amp.GradScaler 已废弃（2.4+ 警告，未来移除）",
     "torch.amp.GradScaler('cuda')"),
    (r"torch\.cuda\.amp\.autocast",
     "torch.cuda.amp.autocast 已废弃（2.4+ 警告，未来移除）",
     "torch.amp.autocast('cuda')"),
    (r"torch\.cuda\.amp\.custom_fwd|torch\.cuda\.amp\.custom_bwd",
     "torch.cuda.amp.custom_fwd/bwd 已废弃",
     "torch.amp.custom_fwd(device_type='cuda')"),

    # ---- NumPy 2.0 删除的别名 ----
    (r"np\.float\b(?!\d|_)", "np.float 已从 NumPy 2.0 删除", "np.float64"),
    (r"np\.int\b(?!\d|_|e)", "np.int 已从 NumPy 2.0 删除", "np.int64"),
    (r"np\.bool\b(?!_)", "np.bool 已从 NumPy 2.0 删除", "bool"),
    (r"np\.object\b", "np.object 已从 NumPy 2.0 删除", "object"),
    (r"np\.str\b(?!_)", "np.str 已从 NumPy 2.0 删除", "str"),
    (r"np\.long\b", "np.long 已从 NumPy 2.0 删除", "int"),
    (r"np\.unicode\b", "np.unicode 已从 NumPy 2.0 删除", "str"),
    (r"np\.fromstring\(", "np.fromstring 已删除（且原语义是二进制解析）", "np.frombuffer / np.fromstring(sep=' ')"),
    (r"np\.NaN\b", "np.NaN 已删除", "np.nan"),
    (r"np\.Inf\b|np\.infty\b", "np.Inf/infty 已删除", "np.inf"),
    (r"np\.round_\(", "np.round_ 已删除", "np.round"),
    (r"np\.product\(", "np.product 已删除", "np.prod"),
    (r"np\.cumproduct\(", "np.cumproduct 已删除", "np.cumprod"),
    (r"np\.alltrue\(", "np.alltrue 已删除", "np.all"),
    (r"np\.sometrue\(", "np.sometrue 已删除", "np.any"),
    (r"np\.msort\(", "np.msort 已删除", "np.sort"),
    (r"np\.trapz\(", "np.trapz 已改名为 np.trapezoid（2.0 起 deprecated）", "np.trapezoid"),
    (r"np\.issubsctype\(", "np.issubsctype 已删除", "np.issubdtype"),
    (r"np\.set_string_function\(", "np.set_string_function 已删除", "-"),

    # ---- Pillow 10/11 ----
    (r"Image\.ANTIALIAS", "Image.ANTIALIAS 已在 Pillow 10 删除", "Image.Resampling.LANCZOS"),
    (r"Image\.(NEAREST|BOX|BILINEAR|HAMMING|BICUBIC|LANCZOS)\b(?!\.)",
     "Image.<filter> 已在 Pillow 10 删除", "Image.Resampling.<filter>"),
    (r"Image\.(ROTATE_90|ROTATE_180|ROTATE_270|FLIP_LEFT_RIGHT|FLIP_TOP_BOTTOM|TRANSPOSE|TRANSVERSE)\b",
     "Image.<transpose> 已在 Pillow 10 删除", "Image.Transpose.<...>"),
    (r"Image\.LINEAR\b", "Image.LINEAR 已在 Pillow 10 删除", "Image.Resampling.BILINEAR"),
    (r"Image\.CUBIC\b", "Image.CUBIC 已在 Pillow 10 删除", "Image.Resampling.BICUBIC"),
    (r"Image\.ADAPTIVE\b", "Image.ADAPTIVE 已移动", "Image.Palette.ADAPTIVE"),
    (r"Image\.(WEB|IMAGE_OPEN)\b", "Image.WEB / Image.IMAGE_OPEN 已删除", "-"),
    (r"ImageDraw\.textsize\(", "ImageDraw.textsize 已在 Pillow 10 删除", "draw.textbbox / textlength"),
    (r"ImageFont\.getsize\(", "ImageFont.getsize 已在 Pillow 10 删除", "font.getbbox / getlength"),
    (r"textsize\(", "textsize 已在 Pillow 10 删除", "textbbox"),

    # ---- matplotlib 3.9/3.10 ----
    (r"(?:plt|matplotlib\.cm|cm)\.get_cmap\(", "cm.get_cmap 已在 matplotlib 3.9 删除",
     "matplotlib.colormaps['名称']"),

    # ---- Python 3.12/3.13 ----
    (r"from distutils|import distutils", "distutils 已在 Python 3.12 移除", "setuptools / packaging"),
    (r"^import imp\b|^from imp import", "imp 已在 Python 3.12 移除", "importlib"),
    (r"import imghdr|from imghdr", "imghdr 已在 Python 3.13 移除", "-"),
    (r"import cgi\b|from cgi import", "cgi 已在 Python 3.13 移除", "-"),
    (r"import telnetlib|import pipes|import crypt|import nntplib|import spwd",
     "多个 stdlib 模块已在 Python 3.13 移除", "-"),
    (r"locale\.getdefaultlocale\(", "locale.getdefaultlocale 已在 Python 3.13 移除",
     "locale.getlocale"),
    (r"datetime\.utcnow\(", "datetime.utcnow 已废弃（3.12+）", "datetime.now(timezone.utc)"),

    # ---- 其它 ----
    (r"pkg_resources", "pkg_resources 属 setuptools，新版会告警", "importlib.metadata"),
    (r"np\.float32\(.*\)\.ptp\(|\.ptp\(", "ndarray.ptp 已在 NumPy 2.0 移除", "np.ptp(a)"),
    (r"np\.(array|asarray)\([^)]*copy=False", "np.array(copy=False) 在 NumPy 2.0 会报错",
     "np.asarray"),

    # ================================================================
    # 第二批：NumPy 2.0 / pandas 2.0 / torch 2.x 的其它移除项
    # ================================================================
    (r"\bnp\.asfarray\(", "np.asfarray 已在 NumPy 2.0 移除", "np.asarray(a, dtype=float)"),
    (r"\bnp\.row_stack\(", "np.row_stack 已在 NumPy 2.0 移除", "np.vstack"),
    (r"\bnp\.in1d\(", "np.in1d 已废弃（2.0）", "np.isin"),
    (r"\bnp\.disp\(|\bnp\.who\(|\bnp\.source\(|\bnp\.lookfor\(",
     "np.disp/who/source/lookfor 已在 NumPy 2.0 移除", "-"),
    (r"\bnp\.safe_eval\(|\bnp\.deprecate\(|\bnp\.deprecate_with_doc\(",
     "np.safe_eval/deprecate* 已在 NumPy 2.0 移除", "-"),
    (r"\bnp\.recfromcsv\(|\bnp\.recfromtxt\(",
     "np.recfromcsv/recfromtxt 已在 NumPy 2.0 移除", "-"),
    (r"\bnp\.cast\[|\bnp\.nbytes\[|\bnp\.issctype\(|\bnp\.maximum_sctype\(",
     "np.cast/nbytes/issctype/maximum_sctype 已在 NumPy 2.0 移除", "-"),
    (r"\bnp\.float_\(|\bnp\.complex_\(|\bnp\.unicode_\b",
     "np.float_/complex_/unicode_ 已在 NumPy 2.0 移除", "np.float64 / np.complex128 / np.str_"),
    (r"\bnp\.random\.random_integers\(|\bnp\.random\.ranf\(",
     "np.random.random_integers/ranf 已移除", "np.random.randint / np.random.random_sample"),
    (r"\bnp\.int0\b|\bnp\.uint0\b|\bnp\.void0\b|\bnp\.object0\b|\bnp\.str0\b",
     "np.int0/uint0/void0/object0/str0 已在 NumPy 2.0 移除", "-"),
    (r"\bnp\.bool8\b", "np.bool8 已在 NumPy 2.0 移除", "bool"),

    (r"\.iteritems\(", ".iteritems() 已在 pandas 2.0 移除", ".items()"),
    (r"\.iterkv\(", ".iterkv() 已在 pandas 2.0 移除", ".items()"),
    # 注意：不要写成 `\.append\(` —— 绝大多数 .append() 是普通 list 的，
    # 例如 utils/torch_utils.py:194 的 results.append(...)。误报会让整份报告失去可信度。

    (r"\btorch\._six\b", "torch._six 已在 PyTorch 2.0 移除", "直接用内置类型"),
    (r"\btorch\.solve\(|\btorch\.symeig\(|\btorch\.lstsq\(|\btorch\.chain_matmul\(",
     "torch.solve/symeig/lstsq/chain_matmul 已在 PyTorch 2.x 移除",
     "torch.linalg.solve / torch.linalg.eigh / torch.linalg.lstsq"),
    # torch.meshgrid 不列入：models/yolo.py:86 有 torch_1_10 版本判断，会走正确分支
    (r"\btorch\.autograd\.Variable\(", "Variable 已废弃（返回 Tensor 本身）", "直接用 Tensor"),
    (r"\btorch\.nn\.functional\.upsample\(", "F.upsample 已废弃", "F.interpolate"),
    (r"\btorch\.range\([^)]*\d+\.\d", "torch.range 已废弃（且含浮点 step 会报错）", "torch.arange"),
    (r"\btorch\.onnx\._export\(", "torch.onnx._export 已移除", "torch.onnx.export"),
    # torch.hub.download_url_to_file 不列入：torch 2.6 仍然存在，未被移除
]

SKIP_DIRS = {"__pycache__", ".git", ".github", "runs"}


def scan(root):
    hits = {}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))
    files.sort()

    for path in files:
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat, desc, fix in RULES:
                if re.search(pat, line):
                    hits.setdefault((pat, desc, fix), []).append((rel, i, stripped))

    return hits, len(files)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f"目录不存在: {root}")
        return 1

    hits, nfiles = scan(root)
    print("=" * 78)
    print(f"扫描目录 : {root}")
    print(f"Python 文件: {nfiles}")
    print(f"命中规则 : {len(hits)} 类")
    print("=" * 78)

    if not hits:
        print("\n✅ 没有发现已知的过时 / 已删除 API。")
        return 0

    total = 0
    for (pat, desc, fix), locs in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        total += len(locs)
        print(f"\n### {desc}")
        print(f"    规则   : {pat}")
        print(f"    建议   : {fix}")
        print(f"    命中   : {len(locs)} 处")
        for rel, i, line in locs[:12]:
            print(f"      {rel}:{i}  {line[:120]}")
        if len(locs) > 12:
            print(f"      ... 还有 {len(locs) - 12} 处")

    print("\n" + "=" * 78)
    print(f"合计 {total} 处需要处理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
