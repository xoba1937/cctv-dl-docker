#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
央视视频下载器 - FastAPI 后端
"""
import os
import json
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

import cctv
import auth
from tasks import task_manager, DOWNLOAD_DIR

app = FastAPI(title="央视视频下载器")
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
APP_VERSION = "v4.4.0"

# 应用启动时初始化鉴权配置（首次生成密码）
auth.load_config()


# ---------- 请求模型 ----------
class ListRequest(BaseModel):
    url: str
    from_month: str = ""
    to_month: str = ""
    include_highlights: bool = False


class DownloadRequest(BaseModel):
    title: str
    quality: str = "0"
    url: str = ""
    select: str = ""
    guid: str = ""
    threads: int = 8
    mp4: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str


# ---------- 鉴权 ----------
PUBLIC_PATHS = {"/", "/api/login", "/api/qualities"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # 静态首页、登录、画质列表、前端资源放行
    if path in PUBLIC_PATHS or path.startswith("/frontend") or path.startswith("/static"):
        return await call_next(request)
    # 仅保护 /api/* 业务接口
    if not path.startswith("/api/"):
        return await call_next(request)
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token or not auth.verify_token(token):
        return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期"})
    return await call_next(request)


@app.post("/api/login")
def api_login(req: LoginRequest, request: Request):
    ip = auth.client_ip(request)
    if not auth.check_rate_limit(ip):
        raise HTTPException(429, "登录失败次数过多，请15分钟后再试")
    if req.username != auth.USERNAME or not auth.verify_password(req.password):
        auth.record_login_fail(ip)
        raise HTTPException(401, "账号或密码错误")
    auth.record_login_success(ip)
    return {"token": auth.make_token(), "username": auth.USERNAME}


@app.get("/api/auth/check")
def api_auth_check():
    return {"ok": True, "username": auth.USERNAME}


# ---------- 节目解析 ----------
@app.post("/api/list")
def api_list(req: ListRequest):
    if not req.url.strip():
        raise HTTPException(400, "请输入节目链接")
    try:
        items = cctv.list_programs(
            req.url.strip(),
            from_month=req.from_month or None,
            to_month=req.to_month or None,
            include_highlights=req.include_highlights,
        )
    except cctv.CctvDlError as e:
        raise HTTPException(400, str(e))
    return {"count": len(items), "items": items}


@app.get("/api/qualities")
def api_qualities():
    return [{"value": k, "label": v} for k, v in cctv.QUALITY_MAP.items()]


@app.get("/api/config")
def api_config():
    return {"save_dir": DOWNLOAD_DIR, "version": APP_VERSION}


# ---------- 下载任务 ----------
@app.post("/api/download")
def api_download(req: DownloadRequest):
    if not req.guid and not req.url:
        raise HTTPException(400, "需要 guid 或 url")
    if req.quality not in cctv.QUALITY_MAP:
        raise HTTPException(400, "画质参数无效")
    t = task_manager.create(
        title=req.title, quality=req.quality, url=req.url,
        select=req.select, guid=req.guid, threads=req.threads, mp4=req.mp4,
    )
    return {"task_id": t.id, "status": t.status}


def _task_dict(t):
    status = t.status
    if status == "completed" and not task_manager.file_exists(t):
        status = "invalid"
    return {
        "id": t.id, "title": t.title, "quality": t.quality,
        "quality_label": t.quality_label, "status": status,
        "state": t.state, "progress": round(t.progress, 1),
        "error": t.error, "output_file": t.output_file,
        "created_at": t.created_at, "finished_at": t.finished_at,
    }


@app.get("/api/tasks")
def api_tasks():
    return [_task_dict(t) for t in task_manager.list_all()]


@app.get("/api/tasks/{tid}")
def api_task(tid: str):
    t = task_manager.get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return _task_dict(t)


@app.post("/api/tasks/{tid}/cancel")
def api_cancel(tid: str):
    if not task_manager.cancel(tid):
        raise HTTPException(400, "无法取消（任务不存在或已结束）")
    return {"ok": True}


@app.post("/api/tasks/clear-invalid")
def api_clear_invalid():
    removed = task_manager.clear_invalid()
    return {"ok": True, "removed": removed}


@app.get("/api/tasks/{tid}/stream")
async def api_task_stream(tid: str):
    t = task_manager.get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")

    async def gen():
        last = None
        while True:
            cur = task_manager.get(tid)
            if not cur:
                break
            snap = _task_dict(cur)
            key = (snap["status"], snap["state"], snap["progress"])
            if key != last:
                yield "data: " + json.dumps(snap, ensure_ascii=False) + "\n\n"
                last = key
            if cur.status in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 前端 ----------
@app.get("/", response_class=HTMLResponse)
def index():
    p = FRONTEND_DIR / "index.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "<h1>央视视频下载器</h1><p>前端未就绪</p>"


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "3322"))
    uvicorn.run(app, host="0.0.0.0", port=port)
