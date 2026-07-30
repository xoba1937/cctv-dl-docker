# ===== 阶段1：解压 cctv-dl（按目标架构选包） =====
FROM alpine:3.20 AS extract
ARG TARGETARCH
WORKDIR /work
COPY vendor/cctv-dl.v4.4.0.linux.x86_64.tar.gz vendor/cctv-dl.v4.4.0.linux.arm64.tar.gz /tmp/pkg/
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        cp /tmp/pkg/cctv-dl.v4.4.0.linux.arm64.tar.gz /work/cctv-dl.tar.gz; \
    else \
        cp /tmp/pkg/cctv-dl.v4.4.0.linux.x86_64.tar.gz /work/cctv-dl.tar.gz; \
    fi && tar xzf cctv-dl.tar.gz

# ===== 阶段2：运行环境 =====
FROM python:3.12-slim
LABEL maintainer="cctv-dl-docker"
LABEL description="央视视频下载器，基于 cctv-dl 的 Web 版"

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/; \
    pip config set global.trusted-host mirrors.aliyun.com; \
    true

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libegl1 libdbus-1-3 libglib2.0-0 ca-certificates \
    libgssapi-krb5-2 libkrb5-3 libcom-err2 \
    libbrotli1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=extract /work/cctv-dl /opt/cctv-dl
RUN chmod +x /opt/cctv-dl/bin/cctv-dl

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

ENV CCTV_DL_BIN=/opt/cctv-dl/bin/cctv-dl \
    DOWNLOAD_DIR=/downloads \
    PORT=3322 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /downloads
VOLUME /downloads

EXPOSE 3322

WORKDIR /app/backend
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3322"]
