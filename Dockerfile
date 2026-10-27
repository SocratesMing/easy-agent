# ============================================================
# Easy Agent - Multi-stage Dockerfile
# AGENT_ENV: prod (default) | test | dev
# ============================================================

# ---------- Stage 1: Frontend Build ----------
FROM node:20-slim AS frontend-builder

# 前端构建时需要 AGENT_ENV 来决定加载哪个 .env 文件
ARG AGENT_ENV=prod
ENV AGENT_ENV=${AGENT_ENV}

WORKDIR /build/frontend-vue2

# Install dependencies first (cache layer)
COPY frontend-vue2/package.json frontend-vue2/package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com

# Copy frontend source and build
COPY frontend-vue2/ ./
RUN npm run build

# ---------- Stage 2: bubblewrap Build (离线源码编译) ----------
# 麒麟 V10 等国产 ARM 发行版仓库里的 bwrap 仍是 0.3.x，缺 --ro-bind-try
# （bubblewrap 0.4.0 才引入），会让沙箱探测直接判定 bwrap 不可用、execute 走
# fail-closed；4.19 内核又没有 Landlock（需 >= 5.13）。所以镜像里不再依赖发行版
# 的 bubblewrap 包，统一用 docker/vendor/ 下的源码离线编译。
# 源码包与架构无关，arm64 / amd64 同包，无需交叉编译。
FROM python:3.12-slim AS bwrap-builder

# 升级版本时同时改这里和下面的 SHA256（sha256sum docker/vendor/bubblewrap-*.tar.xz）
COPY docker/vendor/bubblewrap-0.13.1.tar.xz /tmp/bubblewrap.tar.xz

# 刻意不传 -Dassume_kernel：保留对 4.19 等旧内核的运行时回退路径
RUN echo "0e85c8702248b8dd64144f594147553650cdd0e4c951690345faace79c217c4d  /tmp/bubblewrap.tar.xz" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        meson \
        ninja-build \
        libcap-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /src \
    && tar -xf /tmp/bubblewrap.tar.xz -C /src \
    && meson setup /src/bubblewrap-0.13.1/build /src/bubblewrap-0.13.1 \
        --prefix=/usr/local \
        --libdir=lib \
        -Dselinux=disabled \
        -Dman=disabled \
        -Dtests=false \
        -Dbash_completion=disabled \
        -Dzsh_completion=disabled \
    && ninja -C /src/bubblewrap-0.13.1/build \
    && DESTDIR=/out ninja -C /src/bubblewrap-0.13.1/build install

# ---------- Stage 3: Backend Runtime ----------
FROM python:3.12-slim

# 环境变量: AGENT_ENV 决定后端配置文件和前端构建环境
ARG AGENT_ENV=prod
ENV AGENT_ENV=${AGENT_ENV}

# 系统依赖：pty 支持 + shell 沙箱运行时。
# bubblewrap 来自上面的离线编译阶段（>= 0.4 才有 --ro-bind-try），/usr/local/bin
# 在 PATH 中优先于 /usr/bin，不会退回发行版旧版；运行期只需 libcap2。
# 沙箱把 agent 的 shell 命令限制在工作区/技能/外部目录内（系统目录只读），
# 其余宿主路径不可见。若容器内 userns 被禁，bwrap 起不来会降级 Landlock
# （需内核 >= 5.13），两者都不可用时 execute 直接拒绝（fail-closed）。
# 启用完整 bwrap 隔离（无需 SYS_ADMIN）：
#   --security-opt seccomp=unconfined --security-opt apparmor=unconfined
#   --security-opt systempaths=unconfined
# 若宿主禁用了非特权 user namespace（麒麟 kysec 常见），先在宿主执行
#   sysctl -w user.max_user_namespaces=15000
# 或以 --cap-add SYS_ADMIN 运行并给 /usr/local/bin/bwrap 加 setuid 位。
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        procps \
        libcap2 \
        gettext-base \
        && rm -rf /var/lib/apt/lists/*

# 离线编译产物 + 构建期校验（缺 --ro-bind-try 会直接让沙箱 fail-closed）
COPY --from=bwrap-builder /out/usr/local/bin/bwrap /usr/local/bin/bwrap
RUN bwrap --version && bwrap --help | grep -q -- '--ro-bind-try'


WORKDIR /app

# Install Python dependencies with uv（项目统一使用 uv 管理 Python 依赖）
COPY pyproject.toml uv.lock ./
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /uvx /bin/
# 先用锁文件安装全部依赖（含 docs 扩展），便于 Docker 层缓存；
# 项目本体待源码就位后再做 editable 安装。
RUN uv export --frozen --no-dev --extra docs -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Copy backend source
COPY easy_agent/ ./easy_agent/

# Copy runtime resources (skills / prompts / preset experts). Without these the
# image starts with zero discoverable skills and zero preset experts, because
# find_skills_root()/public_expert_service resolve relative to /app.
COPY skills/ ./skills/
COPY prompts/ ./prompts/
COPY experts/ ./experts/

# Editable 安装项目本体（脚本入口 easy-web / 包发现）
RUN uv pip install --system --no-cache --no-deps -e .

# Copy frontend build output
COPY --from=frontend-builder /build/frontend-vue2/dist ./frontend-vue2/dist/

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create data directories
RUN mkdir -p /app/data /app/workspace /app/memories /app/logs /app/skills /app/prompts

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/live')" || exit 1

# Entrypoint starts uvicorn (app.py 内部已通过 AGENT_ENV 选择配置文件)
ENTRYPOINT ["/entrypoint.sh"]
