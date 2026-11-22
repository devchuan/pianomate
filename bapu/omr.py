from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Tuple

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".pdf"}


def _oemer_command() -> str:
    configured = os.environ.get("OEMER_CMD", "").strip()
    if configured:
        return configured
    found = shutil.which("oemer")
    if found:
        return found
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    candidate = scripts / ("oemer.exe" if os.name == "nt" else "oemer")
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("未找到 Oemer，请设置 OEMER_CMD")


def _audiveris_command() -> list[str]:
    configured = os.environ.get("AUDIVERIS_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    jar = os.environ.get("AUDIVERIS_JAR", "").strip()
    if jar and Path(jar).is_file():
        return ["java", "-jar", jar]
    found = shutil.which("audiveris")
    if found:
        return [found]
    raise RuntimeError(
        "未找到 Audiveris，请设置 AUDIVERIS_JAR（jar 路径）或 AUDIVERIS_CMD（启动命令）")


def _find_score_file(root: Path) -> Path | None:
    for pattern in ("*.musicxml", "*.mxl", "*.xml"):
        matches = sorted(root.rglob(pattern))
        if matches:
            return matches[0]
    return None


def _read_score_file(path: Path) -> str:
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".musicxml")]
            if not names:
                raise RuntimeError("MXL 文件中没有 MusicXML")
            return archive.read(names[0]).decode("utf-8", errors="replace")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if "<score-partwise" not in text and "<score-timewise" not in text:
        raise RuntimeError("OMR 输出不是有效的 MusicXML")
    return text


def _run_oemer(source: Path, workdir: Path) -> str:
    command = _oemer_command()
    if not command.lower().endswith("oemer.exe") and not command.lower().endswith("oemer"):
        parts = shlex.split(command)
    else:
        parts = [command]
    output = workdir / "oemer"
    output.mkdir(exist_ok=True)
    completed = subprocess.run(
        parts + [str(source), "-o", str(output)],
        cwd=workdir, capture_output=True, text=True, timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise RuntimeError(f"Oemer 运行失败：{detail}")
    result = _find_score_file(output) or _find_score_file(workdir)
    if not result:
        raise RuntimeError("Oemer 没有生成乐谱文件")
    return _read_score_file(result)


def _run_audiveris(source: Path, workdir: Path) -> str:
    output = workdir / "audiveris"
    output.mkdir(exist_ok=True)
    completed = subprocess.run(
        _audiveris_command() + ["-batch", "-output", str(output), str(source)],
        cwd=workdir, capture_output=True, text=True, timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise RuntimeError(f"Audiveris 运行失败：{detail}")
    result = _find_score_file(output) or _find_score_file(workdir)
    if not result:
        raise RuntimeError("Audiveris 没有生成乐谱文件")
    return _read_score_file(result)


def convert_image(content: bytes, filename: str) -> Tuple[str, str]:
    """图片/PDF 转 MusicXML，优先 Oemer，失败后降级到 Audiveris。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise RuntimeError("仅支持 png / jpg / jpeg / bmp / tif / tiff / pdf 图片或 PDF")
    errors = []
    with tempfile.TemporaryDirectory(prefix="bapu-omr-") as folder:
        workdir = Path(folder)
        source = workdir / f"score{suffix}"
        source.write_bytes(content)
        for engine, runner in (("oemer", _run_oemer), ("audiveris", _run_audiveris)):
            try:
                return runner(source, workdir), engine
            except Exception as exc:
                errors.append(f"{engine}: {exc}")
    raise RuntimeError("图片转谱失败，" + "；".join(errors))
