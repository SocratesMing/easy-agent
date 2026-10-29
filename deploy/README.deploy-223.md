# 服务器部署说明（183.196.130.56）

目录：`/mnt/data/easyagent`

## 端口

| 服务 | 对外端口 |
|------|----------|
| easy-agent 前端 | 22333 |
| easy-agent 后端 | 22332 |
| RAGFlow Web | 22331 |
| RAGFlow API | 22334 |

## 网络

共享 Docker 网络：`easyagent-net`

```bash
docker network create easyagent-net
# RAGFlow 启动后接入（含 DNS 别名）
docker network connect --alias ragflow --alias ragflow-cpu \
  easyagent-net docker-ragflow-cpu-1
```

easy-agent 内访问 RAGFlow：`http://ragflow:9380`（见 `.env.dev` 的 `RAGFLOW_BASE_URL`）

## 启动 easy-agent

```bash
cd /mnt/data/easyagent
# 需已有 frontend-vue2/dist
docker compose --env-file .env.dev up -d --build
```

## 启动 RAGFlow v0.26.3

```bash
cd /mnt/data/easyagent/ragflow/docker
# .env 中：SVR_WEB_HTTP_PORT=22331 SVR_HTTP_PORT=22334
docker compose -f docker-compose.yml up -d
docker network connect --alias ragflow --alias ragflow-cpu \
  easyagent-net docker-ragflow-cpu-1
```

## 启用知识库

1. 浏览器打开 `http://183.196.130.56:22331` 注册/登录
2. 在用户设置生成 API Key
3. 写入 `/mnt/data/easyagent/.env.dev`：
   - `RAGFLOW_API_KEY=...`
   - `KNOWLEDGE_ENABLED=true`
4. `cd /mnt/data/easyagent && docker compose --env-file .env.dev up -d backend`

在 RAGFlow 中配置模型（OpenAI-API-Compatible）：

- Chat：`deepseek-v4-pro-0813`
- Embedding：`qwen3.7-text-embedding-flash`
- base_url：`https://llm-tyyt06c5mq54ngq7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
- api_key：与 `.env.dev` 中 `DEEPSEEK_API_KEY` 相同

## 默认管理员（easy-agent 首次空库）

由 `EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD` 注入（见 `.env.dev`）。
