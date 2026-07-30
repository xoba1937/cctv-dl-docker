#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鉴权模块
- 账号固定 admin，密码 bcrypt 哈希存储
- 首次启动自动生成随机密码，写入配置文件，打印到日志
- JWT token（1天过期）
- 登录限流（同IP 1分钟5次失败 → 拒15分钟）
"""
import os
import json
import time
import secrets
import string
import threading
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/downloads")
AUTH_FILE = os.path.join(DOWNLOAD_DIR, ".cctvdl_auth.json")
USERNAME = "admin"
TOKEN_EXPIRE_HOURS = 24

# 限流：{ip: {"fails": n, "block_until": ts}}
_rate_limit = {}
_rate_lock = threading.Lock()


def _gen_random_pw(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _gen_jwt_secret():
    return secrets.token_hex(32)


def load_config():
    """加载鉴权配置，不存在则首次生成"""
    if not os.path.isfile(AUTH_FILE):
        return _init_config()
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _init_config()


def _init_config():
    """首次生成：随机密码 + jwt密钥，写入文件，返回配置"""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    plain = _gen_random_pw()
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    cfg = {
        "pw_hash": hashed,
        "jwt_secret": _gen_jwt_secret(),
    }
    try:
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.chmod(AUTH_FILE, 0o600)
    except Exception as e:
        print(f"[auth] 写配置文件失败: {e}")
    print("=" * 60)
    print("[auth] 首次启动，已生成随机登录密码：")
    print(f"[auth] 账号: {USERNAME}")
    print(f"[auth] 密码: {plain}")
    print("[auth] 请妥善保存。修改密码：docker exec -it cctv-dl-docker python3 /app/backend/cli.py password")
    print("=" * 60)
    return cfg


def set_password(new_plain):
    """设置新密码（明文），返回新密码。用于 CLI 改密码"""
    cfg = load_config()
    hashed = bcrypt.hashpw(new_plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    cfg["pw_hash"] = hashed
    cfg["jwt_secret"] = _gen_jwt_secret()  # 换密钥，旧token失效
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(AUTH_FILE, 0o600)
    return new_plain


def verify_password(plain):
    cfg = load_config()
    hashed = cfg.get("pw_hash", "").encode("utf-8")
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed)
    except Exception:
        return False


def make_token():
    cfg = load_config()
    payload = {
        "sub": USERNAME,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, cfg["jwt_secret"], algorithm="HS256")


def verify_token(token):
    cfg = load_config()
    try:
        jwt.decode(token, cfg["jwt_secret"], algorithms=["HS256"])
        return True
    except Exception:
        return False


# ---------- 登录限流 ----------
def client_ip(request):
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(ip):
    """返回 True=允许尝试，False=已被拒"""
    now = time.time()
    with _rate_lock:
        rec = _rate_limit.get(ip)
        if rec and rec.get("block_until", 0) > now:
            return False
        return True


def record_login_fail(ip):
    now = time.time()
    with _rate_lock:
        rec = _rate_limit.get(ip, {"fails": 0, "block_until": 0})
        rec["fails"] = rec.get("fails", 0) + 1
        if rec["fails"] >= 5:
            rec["block_until"] = now + 900
            rec["fails"] = 0
        _rate_limit[ip] = rec


def record_login_success(ip):
    with _rate_lock:
        _rate_limit.pop(ip, None)
