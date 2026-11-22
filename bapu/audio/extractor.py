from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def get_ffmpeg() -> str:
    """优先使用 imageio-ffmpeg 自带的二进制，其次使用系统 ffmpeg。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        binary = shutil.which("ffmpeg")
        if not binary:
            raise RuntimeError("未找到 ffmpeg，请先安装 imageio-ffmpeg 或把 ffmpeg 加入 PATH")
        return binary


def extract_wav(source: str | Path, target: str | Path | None = None,
                sample_rate: int = 22050) -> Path:
    """把任意音视频文件解码为单声道 WAV（视频会自动丢弃画面）。"""
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"输入文件不存在：{source_path}")
    if target is None:
        target_path = Path(tempfile.mkdtemp(prefix="bapu_")) / f"{source_path.stem}.wav"
    else:
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source_path), "-vn", "-map", "0:a:0",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        str(target_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"音频提取失败：{result.stderr.strip() or '文件可能不含音频流'}")
    return target_path
