# RAGFlow v0.26.3 部署说明（本机脚本参考）

目录建议：`/opt/ragflow`（官方仓库 `docker/` 子目录）。

```bash
git clone -b v0.26.3 --depth 1 https://github.com/infiniflow/ragflow.git /opt/ragflow
cd /opt/ragflow/docker
```

`.env` 关键端口与镜像：

```bash
RAGFLOW_IMAGE=swr.cn-north-4.myhuaweicloud.com/infiniflow/ragflow:v0.26.3
# 或官方：infiniflow/ragflow:v0.26.3

SVR_WEB_HTTP_PORT=22331   # 前端 Web
SVR_HTTP_PORT=22334       # 后端 API
```

启动：

```bash
docker compose -f docker-compose.yml up -d
```

浏览器打开 `http://<host>:22331`，注册管理员后在用户设置生成 API Key，
写回 easy-agent `.env.dev` 的 `RAGFLOW_API_KEY`，并将 `KNOWLEDGE_ENABLED=true` 后重启。

模型（DashScope OpenAI 兼容）：

- Chat：`qwen-plus`，base `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Embedding：按需在 RAGFlow 注册；若 `Qwen3-embedding-4B` 不可用，可用 `text-embedding-v4`
