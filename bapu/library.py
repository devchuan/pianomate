from __future__ import annotations

import html
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from .music.converter import midi_to_score, musicxml_to_score
from .music import score_to_musicxml
from .music.model import Score

SCORES_DIR = Path(__file__).resolve().parent / "scores"
USER_DIR = SCORES_DIR / "user"
INDEX_FILE = SCORES_DIR / "index.json"
USER_INDEX_FILE = USER_DIR / "index.json"

_last_imslp_request = 0.0
_imslp_lock = threading.Lock()

DEFAULT_PROVIDERS = [
    "local", "openscore", "mutopia", "imslp", "musopen", "pianomidi", "custom",
]
PROVIDER_LABELS = {
    "local": "本地曲谱库",
    "openscore": "OpenScore",
    "mutopia": "Mutopia",
    "imslp": "IMSLP",
    "musopen": "Musopen",
    "pianomidi": "Piano-MIDI",
    "custom": "自定义聚合",
}
_cache: Dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, loader) -> Any:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.monotonic() - entry[0] < ttl:
            return entry[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _fetch(url: str, timeout: float = 7, as_json: bool = False) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "bapu-score-search/2.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        return json.loads(text) if as_json else text
    except Exception:
        return {} if as_json else ""


def _read_json(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def all_scores() -> List[Dict[str, Any]]:
    entries = _read_json(INDEX_FILE) + _read_json(USER_INDEX_FILE)
    return [entry for entry in entries if entry.get("key") and entry.get("file")]


def list_scores(query: str = "", tag: str = "", source: str = "") -> List[Dict[str, Any]]:
    needle = query.strip().lower()
    wanted_tag = tag.strip().lower()
    wanted_source = source.strip().lower()
    results = []
    for entry in all_scores():
        if wanted_source and str(entry.get("source", "")).lower() != wanted_source:
            continue
        if wanted_tag and wanted_tag not in [str(item).lower() for item in entry.get("tags", [])]:
            continue
        if needle:
            haystack = " ".join(str(entry.get(field, "")) for field in ("title", "composer", "tags"))
            if needle not in haystack.lower():
                continue
        results.append(dict(entry))
    return results


def load_score(key: str) -> Score:
    for entry in all_scores():
        if entry.get("key") == key:
            path = SCORES_DIR / entry["file"]
            if not path.is_file():
                break
            if path.suffix.lower() in (".mid", ".midi"):
                score = midi_to_score(path.read_bytes(), entry.get("title", ""))
            else:
                text = path.read_text(encoding="utf-8-sig")
                score = musicxml_to_score(text)
                score.source_xml = text
            score.title = entry.get("title") or score.title
            return score
    raise KeyError(f"曲谱不存在：{key}")


def _safe_key(filename: str, title: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", Path(title or filename).stem).strip("-")
    return (base or "score")[:60]


def import_score(filename: str, content: bytes) -> Dict[str, Any]:
    lower = filename.lower()
    if lower.endswith((".mid", ".midi")):
        score = midi_to_score(content, Path(filename).stem)
        key = _safe_key(filename, score.title)
        existing = {entry.get("key") for entry in all_scores()}
        if key in existing:
            key = f"{key}-{int(time.time())}"
        relative = f"user/{key}.mid"
        (USER_DIR / f"{key}.mid").write_bytes(content)
        meta = {
            "key": key,
            "title": score.title or Path(filename).stem,
            "composer": "本地导入",
            "tonic": score.tonic,
            "mode": score.mode,
            "meter": f"{score.beats_per_measure}/{score.beat_unit}",
            "tempo": score.tempo,
            "difficulty": "自定义",
            "tags": ["导入", "钢琴", "MIDI"],
            "source": "user",
            "format": "midi",
            "file": relative,
            "note": "用户导入的 MIDI 演奏，保存在本地",
        }
    else:
        if lower.endswith(".mxl") or content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".musicxml")]
                if not names:
                    raise ValueError("MXL 压缩包中没有 MusicXML 文件")
                text = archive.read(names[0]).decode("utf-8-sig", errors="replace")
        else:
            text = content.decode("utf-8-sig")
        score = musicxml_to_score(text)
        key = _safe_key(filename, score.title)
        existing = {entry.get("key") for entry in all_scores()}
        if key in existing:
            key = f"{key}-{int(time.time())}"
        relative = f"user/{key}.musicxml"
        (USER_DIR / f"{key}.musicxml").write_text(text, encoding="utf-8")
        meta = {
            "key": key,
            "title": score.title or Path(filename).stem,
            "composer": "本地导入",
            "tonic": score.tonic,
            "mode": score.mode,
            "meter": f"{score.beats_per_measure}/{score.beat_unit}",
            "tempo": score.tempo,
            "difficulty": "自定义",
            "tags": ["导入", "钢琴"],
            "source": "user",
            "format": "musicxml",
            "file": relative,
            "note": "用户导入的乐谱，保存在本地",
        }
    user_index = _read_json(USER_INDEX_FILE)
    user_index.append(meta)
    USER_INDEX_FILE.write_text(json.dumps(user_index, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def save_score(key: str, score: Score) -> Dict[str, Any]:
    """保存编辑后的乐谱；内置曲谱会另存为用户曲谱，不覆盖原文件。"""
    user_index = _read_json(USER_INDEX_FILE)
    existing = next((item for item in user_index if item.get("key") == key), None)
    if existing:
        filename = f"{key}.musicxml"
        relative = f"user/{filename}"
    else:
        base = _safe_key("", score.title or key or "score")
        key = base
        suffix = 1
        taken = {item.get("key") for item in all_scores()}
        while key in taken:
            suffix += 1
            key = f"{base}-{suffix}"
        filename = f"{key}.musicxml"
        relative = f"user/{filename}"
    path = USER_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(score_to_musicxml(score), encoding="utf-8")
    meta = {
        "key": key,
        "title": score.title or key,
        "composer": "本地编辑",
        "tonic": score.tonic,
        "mode": score.mode,
        "meter": f"{score.beats_per_measure}/{score.beat_unit}",
        "tempo": score.tempo,
        "difficulty": "自定义",
        "tags": ["编辑", "钢琴"],
        "source": "user",
        "file": relative,
        "note": "网页编辑器保存的乐谱",
    }
    if existing:
        user_index = [meta if item.get("key") == key else item for item in user_index]
    else:
        user_index.append(meta)
    USER_INDEX_FILE.write_text(json.dumps(user_index, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _imslp_search(query: str, limit: int) -> List[Dict[str, Any]]:
    global _last_imslp_request
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": max(1, min(20, limit)), "format": "json",
    })
    url = f"https://imslp.org/api.php?{params}"
    with _imslp_lock:
        elapsed = time.monotonic() - _last_imslp_request
        if elapsed < 2.0:
            time.sleep(2.0 - elapsed)
        request = urllib.request.Request(url, headers={"User-Agent": "bapu-local-score-search/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []
        finally:
            _last_imslp_request = time.monotonic()
    results = []
    for item in payload.get("query", {}).get("search", []):
        title = str(item.get("title", ""))
        if not title:
            continue
        results.append({
            "key": "", "title": title, "composer": "IMSLP",
            "source": "imslp", "kind": "link",
            "url": "https://imslp.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            "note": "IMSLP 页面链接，版权状态请自行核对；遵守站点限速",
        })
    return results


def _custom_search(query: str, limit: int) -> List[Dict[str, Any]]:
    endpoint = os.environ.get("BAPU_AGGREGATOR_URL", "").strip()
    if not endpoint:
        return []
    separator = "&" if "?" in endpoint else "?"
    url = f"{endpoint}{separator}q={urllib.parse.quote(query)}&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    rows = payload.get("results", []) if isinstance(payload, dict) else payload
    results = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        results.append({
            "key": "", "title": str(item.get("title", "")),
            "composer": str(item.get("composer", "")),
            "source": "custom", "kind": "link" if item.get("url") else "score",
            "url": str(item.get("url", "")),
            "note": str(item.get("note", "自定义聚合接口")),
        })
    return results


def _openscore_search(query: str, limit: int) -> List[Dict[str, Any]]:
    needle = query.strip().lower()
    if not needle or limit <= 0:
        return []

    def load() -> List[Dict[str, Any]]:
        composers = _fetch(
            "https://api.github.com/repos/OpenScore/Lieder/contents/scores?ref=main",
            as_json=True,
        )
        if not isinstance(composers, list):
            return []
        matched = [item for item in composers
                   if item.get("type") == "dir" and needle in item.get("name", "").lower()]
        if not matched:
            return []
        found: List[Dict[str, Any]] = []
        queue = [(item.get("url", ""), item.get("name", "").replace("_", " "))
                 for item in matched[:4] if item.get("url")]
        calls = 0
        while queue and calls < 12 and len(found) < limit:
            url, composer = queue.pop(0)
            rows = _fetch(url, as_json=True)
            calls += 1
            if not isinstance(rows, list):
                continue
            for row in rows:
                name = row.get("name", "")
                if row.get("type") == "dir" and row.get("url"):
                    queue.append((row["url"], composer))
                elif name.lower().endswith((".musicxml", ".mxl", ".mscx")):
                    found.append({
                        "key": "",
                        "title": re.sub(r"[_-]+", " ", Path(name).stem).strip(),
                        "composer": composer,
                        "source": "openscore",
                        "kind": "link",
                        "url": row.get("html_url") or row.get("download_url") or "",
                        "download_url": row.get("download_url", ""),
                        "note": "CC0 MusicXML，可导入演奏",
                    })
                    if len(found) >= limit:
                        break
        return found[:limit]

    return _cached(f"openscore:{needle}:{limit}", 600, load)


def _mutopia_search(query: str, limit: int) -> List[Dict[str, Any]]:
    needle = query.strip().lower()

    def load() -> List[Dict[str, Any]]:
        text = _fetch("https://www.mutopiaproject.org/latestadditions.rss")
        if not text:
            text = _fetch("http://www.mutopiaproject.org/latestadditions.rss")
        if not text:
            return []
        rows = []
        for block in re.findall(r"<item>(.*?)</item>", text, flags=re.S | re.I):
            title_match = re.search(r"<title>(.*?)</title>", block, flags=re.S | re.I)
            link_match = re.search(r"<link>(.*?)</link>", block, flags=re.S | re.I)
            if not title_match or not link_match:
                continue
            title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
            if needle and needle not in title.lower():
                continue
            rows.append({
                "key": "", "title": title, "composer": "Mutopia",
                "source": "mutopia", "kind": "link",
                "url": html.unescape(link_match.group(1)).strip(),
                "note": "公有领域或 CC，站点提供 MIDI、LilyPond 与 PDF",
            })
            if len(rows) >= limit:
                break
        return rows

    return _cached(f"mutopia:{needle}:{limit}", 600, load)


def _musopen_search(query: str, limit: int) -> List[Dict[str, Any]]:
    needle = query.strip()
    if not needle or limit <= 0:
        return []
    return [{
        "key": "", "title": f"在 Musopen 搜索：{needle}", "composer": "Musopen",
        "source": "musopen", "kind": "link",
        "url": "https://musopen.org/?s=" + urllib.parse.quote(needle),
        "note": "公有领域乐谱以 PDF 为主，版权与下载请自行核对",
    }]


_PIANOMIDI_COMPOSERS = [
    "albeniz", "bach", "beethoven", "brahms", "chopin", "debussy", "grieg",
    "handel", "haydn", "liszt", "mozart", "rachmaninoff", "ravel", "schubert",
    "schumann", "scriabin", "tchaikovsky", "vivaldi",
]


def _pianomidi_search(query: str, limit: int) -> List[Dict[str, Any]]:
    needle = query.strip().lower()
    if limit <= 0:
        return []
    if needle:
        for composer in _PIANOMIDI_COMPOSERS:
            if needle in composer or composer in needle:
                return [{
                    "key": "", "title": f"在 Piano-MIDI 浏览：{composer.title()}",
                    "composer": "Piano-MIDI", "source": "pianomidi", "kind": "link",
                    "url": f"http://piano-midi.de/{composer}.htm",
                    "note": "MIDI 编配为 CC BY-SA，使用时需署名",
                }]
    return [{
        "key": "", "title": "在 Piano-MIDI 浏览钢琴 MIDI", "composer": "Piano-MIDI",
        "source": "pianomidi", "kind": "link", "url": "http://piano-midi.de/",
        "note": "MIDI 编配为 CC BY-SA，使用时需署名",
    }]


PROVIDER_FUNCS = {
    "openscore": _openscore_search,
    "mutopia": _mutopia_search,
    "imslp": _imslp_search,
    "musopen": _musopen_search,
    "pianomidi": _pianomidi_search,
    "custom": _custom_search,
}


def search_all(query: str, providers: List[str] | None = None, limit: int = 12) -> List[Dict[str, Any]]:
    """按 provider 顺序逐级降级：当前来源没有结果就尝试下一个。"""
    selected = [item for item in (providers or DEFAULT_PROVIDERS) if item in DEFAULT_PROVIDERS]
    results: List[Dict[str, Any]] = []
    seen = set()
    for provider in selected:
        if provider == "local":
            found: List[Dict[str, Any]] = [
                {**entry, "kind": "score"} for entry in list_scores(query)[:limit]
            ]
        else:
            search_fn = globals().get(f"_{provider}_search")
            found = search_fn(query, max(1, limit - len(results))) if search_fn else []
        for item in found:
            identity = (item.get("title", ""), item.get("url", ""))
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
            if len(results) >= limit:
                break
        # 只要当前来源有结果就不再降级，保证结果按来源优先级排列
        if results:
            break
    return results
