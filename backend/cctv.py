#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cctv-dl 封装层
封装对官方 cctv-dl CLI 的调用：list（列节目）、download（下载+流式进度）。
本层不含任何 Web 逻辑，可独立命令行测试。
"""
import os
import json
import subprocess
from typing import Iterator, Optional

# cctv-dl 可执行文件路径，可通过环境变量覆盖（Docker 内为固定路径）
CCTV_DL_BIN = os.environ.get(
    "CCTV_DL_BIN",
    os.path.expanduser("~/project/cctv-dl-docker/.local_test/cctv-dl/bin/cctv-dl"),
)

# 画质映射（value -> 中文标签），与原作者一致
QUALITY_MAP = {
    "0": "最高画质",
    "5": "蓝光(4000k)",
    "1": "超清(2000k)",
    "2": "高清(1200k)",
    "3": "标清(850k)",
    "4": "流畅(450k)",
}


class CctvDlError(Exception):
    """cctv-dl 调用出错"""
    pass


def _seconds_to_hms(seconds) -> str:
    """秒数转 HH:MM:SS / MM:SS"""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def list_programs(url: str, from_month: Optional[str] = None,
                  to_month: Optional[str] = None,
                  include_highlights: bool = False,
                  timeout: int = 120) -> list[dict]:
    """
    调用 cctv-dl list --json，返回节目列表。
    每项：{index, guid, title, time, length(秒), length_text, channel, highlight, listType}
    """
    cmd = [CCTV_DL_BIN, "list", url, "--json"]
    if from_month:
        cmd += ["--from", from_month]
    if to_month:
        cmd += ["--to", to_month]
    if include_highlights:
        cmd.append("--include-highlights")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CctvDlError("列出节目超时")

    if proc.returncode != 0 and not proc.stdout.strip():
        raise CctvDlError(f"列出节目失败: {proc.stderr.strip() or 未知错误}")

    items = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") != "video":
            continue
        length = ev.get("length")
        items.append({
            "index": ev.get("index"),
            "guid": ev.get("guid", ""),
            "title": ev.get("title", ""),
            "time": ev.get("time", ""),
            "length": length,
            "length_text": _seconds_to_hms(length),
            "channel": ev.get("channel", ""),
            "highlight": ev.get("highlight", False),
            "listType": ev.get("listType", ""),
            "image": ev.get("image", ""),
            "brief": ev.get("brief", ""),
        })
    return items



def download_stream(url: Optional[str] = None,
                    guid: Optional[str] = None,
                    title: Optional[str] = None,
                    select: Optional[str] = None,
                    quality: str = "0",
                    threads: int = 8,
                    output: str = "/downloads",
                    mp4: bool = True) -> Iterator[dict]:
    """
    调用 cctv-dl download --json，逐行 yield 进度事件（JSON Lines 解析后的 dict）。
    两种模式：
      1. URL + select：download(url=..., select="3")
      2. GUID 直接下载：download(guid=..., title=...)
    output 建议传绝对路径。
    yield 的事件形如：
      {"event":"job","state":"downloading","progress":50,"title":"..."}
      {"event":"job_finished","state":"completed"/"failed",...}
      {"event":"download_complete","completed":1,"failed":0,...}
    """
    # 输出目录必须存在
    os.makedirs(output, exist_ok=True)
    # 统一转绝对路径（规范做法）
    output = os.path.abspath(output)

    cmd = [CCTV_DL_BIN, "download"]
    if guid:
        cmd += ["--guid", guid]
        if title:
            cmd += ["--title", title]
    elif url:
        cmd.append(url)
        if select:
            cmd += ["--select", select]
    else:
        raise CctvDlError("download 需要 url 或 guid")

    cmd += ["--quality", quality, "--threads", str(threads), "--output", output]
    cmd.append("--mp4" if mp4 else "--no-mp4")
    cmd.append("--json")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield ev
    finally:
        proc.wait()
        # 若进程异常退出且无事件，补一个错误事件
        if proc.returncode not in (0, None):
            stderr = proc.stderr.read() if proc.stderr else ""
            yield {"event": "process_error", "returncode": proc.returncode,
                   "stderr": stderr.strip()[:500]}


# ============ 命令行自测 ============
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 cctv.py list <URL>")
        print("  python3 cctv.py download <URL> <select> <output>")
        print("  python3 cctv.py download-guid <GUID> <title> <output>")
        sys.exit(1)

    action = sys.argv[1]
    if action == "list":
        items = list_programs(sys.argv[2])
        print(f"共 {len(items)} 个节目:")
        for it in items[:10]:
            print("  [%s] %s | %s | %s" % (it["index"], it["title"], it["length_text"], it["guid"]))
        if len(items) > 10:
            print(f"  ... 还有 {len(items)-10} 个")
    elif action == "download":
        url, select, output = sys.argv[2], sys.argv[3], sys.argv[4]
        for ev in download_stream(url=url, select=select, quality="4", output=output):
            print(ev)
    elif action == "download-guid":
        guid, title, output = sys.argv[2], sys.argv[3], sys.argv[4]
        for ev in download_stream(guid=guid, title=title, quality="4", output=output):
            print(ev)
