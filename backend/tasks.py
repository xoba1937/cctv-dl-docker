#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载任务管理层
- 串行队列：一次下载一个视频（合并阶段可与下一个下载重叠，由 cctv-dl 内部流程决定）
- 状态跟踪：每个任务的进度、阶段、结果
- 持久化：任务记录写入 DOWNLOAD_DIR/.cctvdl_tasks.json，容器重启可恢复
"""
import os
import json
import time
import uuid
import threading
import queue
from dataclasses import dataclass, field, asdict
from typing import Optional

import cctv

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/downloads")
TASKS_FILE = os.path.join(DOWNLOAD_DIR, ".cctvdl_tasks.json")


@dataclass
class DownloadTask:
    id: str
    title: str
    quality: str
    quality_label: str = ""
    # 下载来源：url+select 或 guid
    url: str = ""
    select: str = ""
    guid: str = ""
    threads: int = 8
    mp4: bool = True
    # 运行时状态
    status: str = "queued"   # queued/running/completed/failed/cancelled
    state: str = ""          # cctv-dl 的细分阶段：resolving/downloading/concatenating/decrypting
    progress: float = 0.0
    error: str = ""
    output_file: str = ""    # 完成后的文件名
    created_at: float = 0.0
    finished_at: float = 0.0


class TaskManager:
    """串行下载任务管理器"""

    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue = queue.Queue()
        self._cancel_flags: dict[str, bool] = {}
        self._load()
        # 启动串行工作线程
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    # ---------- 持久化 ----------
    def _load(self):
        if not os.path.isfile(TASKS_FILE):
            return
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data:
                t = DownloadTask(**d)
                # 重启后，之前 running/queued 的任务标记为中断
                if t.status in ("running", "queued"):
                    t.status = "failed"
                    t.error = "服务重启，任务中断"
                self.tasks[t.id] = t
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump([asdict(t) for t in self.tasks.values()], f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 任务操作 ----------
    def create(self, title, quality, url="", select="", guid="",
               threads=8, mp4=True) -> DownloadTask:
        tid = uuid.uuid4().hex[:12]
        t = DownloadTask(
            id=tid, title=title, quality=quality,
            quality_label=cctv.QUALITY_MAP.get(quality, quality),
            url=url, select=select, guid=guid, threads=threads, mp4=mp4,
            status="queued", created_at=time.time(),
        )
        with self.lock:
            self.tasks[tid] = t
            self._save()
        self.queue.put(tid)
        return t

    def get(self, tid) -> Optional[DownloadTask]:
        return self.tasks.get(tid)

    def list_all(self):
        return sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)

    def file_exists(self, t) -> bool:
        """已完成任务的输出文件是否还在挂载目录"""
        if not t.output_file:
            return False
        return os.path.isfile(os.path.join(DOWNLOAD_DIR, t.output_file))

    def clear_invalid(self):
        """清理失败和已失效（文件不存在）的记录"""
        removed = 0
        with self.lock:
            for tid in list(self.tasks.keys()):
                t = self.tasks[tid]
                is_invalid = (t.status == "failed") or \
                             (t.status == "completed" and not self._file_ok(t))
                if is_invalid:
                    del self.tasks[tid]
                    removed += 1
            self._save()
        return removed

    def _file_ok(self, t) -> bool:
        if not t.output_file:
            return False
        return os.path.isfile(os.path.join(DOWNLOAD_DIR, t.output_file))

    def cancel(self, tid):
        """标记取消（运行中的任务由 worker 检测标志）"""
        with self.lock:
            t = self.tasks.get(tid)
            if not t:
                return False
            if t.status == "queued":
                t.status = "cancelled"
                t.finished_at = time.time()
                self._save()
                return True
            if t.status == "running":
                self._cancel_flags[tid] = True
                return True
        return False

    def delete(self, tid):
        """删除任务记录（不删已下载文件，文件删除单独接口）"""
        with self.lock:
            t = self.tasks.pop(tid, None)
            self._save()
            return t is not None

    def _update(self, tid, **kw):
        with self.lock:
            t = self.tasks.get(tid)
            if t:
                for k, v in kw.items():
                    setattr(t, k, v)
                self._save()

    # ---------- 串行执行 ----------
    def _worker_loop(self):
        while True:
            tid = self.queue.get()
            t = self.tasks.get(tid)
            if not t or t.status != "queued":
                continue
            self._run_one(t)

    def _run_one(self, t: DownloadTask):
        self._update(t.id, status="running")
        try:
            for ev in cctv.download_stream(
                url=t.url or None, guid=t.guid or None, title=t.title,
                select=t.select or None, quality=t.quality,
                threads=t.threads, output=DOWNLOAD_DIR, mp4=t.mp4,
            ):
                # 检查取消
                if self._cancel_flags.get(t.id):
                    self._update(t.id, status="cancelled", finished_at=time.time())
                    self._cancel_flags.pop(t.id, None)
                    return
                event = ev.get("event")
                if event == "job":
                    self._update(t.id, state=ev.get("state", ""),
                                 progress=float(ev.get("progress", 0)))
                elif event == "job_finished":
                    if ev.get("state") == "completed":
                        self._update(t.id, state="completed", progress=100.0)
                    else:
                        self._update(t.id, error=ev.get("error", "下载失败"))
                elif event == "download_complete":
                    if ev.get("completed", 0) >= 1:
                        fname = self._guess_output_file(t.title, t.mp4)
                        self._update(t.id, status="completed", progress=100.0,
                                     output_file=fname, finished_at=time.time())
                    else:
                        self._update(t.id, status="failed",
                                     error=t.error or "下载失败",
                                     finished_at=time.time())
                elif event == "process_error":
                    self._update(t.id, status="failed",
                                 error="进程错误: " + ev.get("stderr", ""),
                                 finished_at=time.time())
            # 循环正常结束但未标记完成 → 兜底判断
            cur = self.tasks.get(t.id)
            if cur and cur.status == "running":
                fname = self._guess_output_file(t.title, t.mp4)
                if fname:
                    self._update(t.id, status="completed", progress=100.0,
                                 output_file=fname, finished_at=time.time())
                else:
                    self._update(t.id, status="failed",
                                 error="未知原因，未生成文件",
                                 finished_at=time.time())
        except Exception as e:
            self._update(t.id, status="failed", error=str(e),
                         finished_at=time.time())

    def _guess_output_file(self, title, mp4):
        """根据标题推断输出文件名（cctv-dl 用 title.mp4 命名）"""
        ext = ".mp4" if mp4 else ".ts"
        # cctv-dl 对文件名的非法字符处理：这里做同样清理
        safe = title
        for ch in '\\/:*?"<>|':
            safe = safe.replace(ch, "_")
        candidate = safe + ext
        if os.path.isfile(os.path.join(DOWNLOAD_DIR, candidate)):
            return candidate
        # 回退：找最近生成的同扩展名文件
        return candidate


task_manager = TaskManager()
