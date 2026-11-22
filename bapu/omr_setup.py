"""下载 Oemer 模型文件到当前 Python 环境的 oemer/checkpoints 目录。

GitHub API 未认证时有每小时请求限制；脚本使用 Range 分段下载并在限流后重试。
用法：python -m bapu.omr_setup
"""
from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

ASSETS = {
    50273314: ("unet_big/model.onnx", 70767752),
    50273330: ("unet_big/weights.h5", 70977288),
    50273348: ("seg_net/model.onnx", 38448467),
    50273365: ("seg_net/weights.h5", 38570576),
}
CHUNK = 4 * 1024 * 1024


def _fetch(url: str, start: int, end: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "bapu-omr-setup/1.0",
        "Range": f"bytes={start}-{end}",
    })
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def setup() -> None:
    try:
        import oemer
    except ImportError as exc:
        raise RuntimeError("请先安装 oemer：pip install oemer") from exc
    root = Path(oemer.__file__).resolve().parent / "checkpoints"
    for asset_id, (relative, expected) in ASSETS.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == expected:
            print("已存在", relative)
            continue
        url = f"https://api.github.com/repos/BreezeWhite/oemer/releases/assets/{asset_id}"
        print("下载", relative, expected)
        with target.open("wb") as handle:
            for start in range(0, expected, CHUNK):
                end = min(start + CHUNK - 1, expected - 1)
                for attempt in range(8):
                    try:
                        handle.write(_fetch(url, start, end))
                        handle.flush()
                        break
                    except Exception as exc:
                        print("  重试", attempt, repr(exc))
                        time.sleep(5)
                else:
                    raise RuntimeError(f"下载失败：{relative} {start}-{end}")
        print("完成", relative, target.stat().st_size)


if __name__ == "__main__":
    try:
        setup()
    except Exception as exc:
        print("OMR 模型安装失败：", exc, file=sys.stderr)
        raise
