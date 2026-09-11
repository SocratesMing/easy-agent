# RAGFlow v0.17.2 本地轻量联调

该环境只用于第一期开发验证：RAGFlow 使用官方 `v0.17.2-slim` 镜像，向量模型由 macOS 原生 Ollama 运行。行内地址、鉴权和定制路由 EasyAgent 独立 YAML + 环境变量承载，不需要改业务代码。

## 本地组件

- RAGFlow 源码：`../ragflow-v0.17.2`（官方 tag `v0.17.2`）
- Colima：`x86_64` / 4 CPU / 8 GiB，用于运行官方 amd64 镜像
- RAGFlow Web：`http://127.0.0.1/`
- RAGFlow HTTP API：`http://127.0.0.1:9380/api/v1`
- Ollama：仅监听 `127.0.0.1:11434`，模型 `bge-m3`

从官方 tag 重建时，先在 RAGFlow 仓库根目录应用可复现的本地补丁：

```bash
git apply ../easy-agent/deploy/ragflow-v0.17.2/apple-silicon-local.patch
```

`colima-resolv.conf` 是虚拟机 DNS 配置；`docker-daemon.json` 将 Docker 并发下载降为 1，用于减少大镜像拉取时的 EOF 中断。
本地 `docker-compose.yml` 还将 RAGFlow 日志改为命名卷，并等待 Elasticsearch 健康后再启动 API，用于兼容含中文的 macOS 路径和较慢的 x86 首次启动。

## 启停

在 EasyAgent 仓库根目录可直接执行无破坏性启动脚本。脚本会复用已有
Colima、Docker 数据卷和 Ollama 模型，不会执行 `down -v`：

```bash
./scripts/start.dev.ragflow.local.sh
./scripts/status.dev.ragflow.local.sh
```

等价的手工命令如下：

```bash
colima start --arch x86_64 --cpu 4 --memory 8 --disk 40
cd ../ragflow-v0.17.2/docker
docker-compose -f docker-compose.yml up -d

OLLAMA_HOST=127.0.0.1:11434 ollama serve
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull bge-m3
```

停止时保留数据：

```bash
cd ../ragflow-v0.17.2/docker
docker-compose -f docker-compose.yml stop
colima stop
```

不要在联调环境使用 `down -v`，它会删除本地知识库数据。

## EasyAgent 接入

1. 在 RAGFlow 界面配置 Ollama 嵌入模型，并将其设为默认 embedding model。
2. 生成 RAGFlow System API token，存入 macOS Keychain：

   ```bash
   security add-generic-password -a "$USER" -s easy-agent-ragflow-api-key -w -U
   ```

3. 启动 EasyAgent 知识工程模式：

   ```bash
   ./scripts/start.dev.knowledge.keychain.sh
   ```

EasyAgent 从 `easy_agent/config/config.dev.yaml` 的 `knowledge` 段读取知识库配置，并从同文件的 `small_models` 段读取 embedding、reranker 等小模型信息。主客户端固定遵循行内 API 契约；开发态默认启用 `adapter.local_v017_bridge`，将请求重定向到本机 RAGFlow v0.17，并把行内布尔解析参数转换为本地所需的 `Plain Text`。

行内部署必须将 `RAGFLOW_LOCAL_COMPAT_ENABLED` 设为 `false`，同时配置正式的 `RAGFLOW_SYS_CODE`、`RAGFLOW_AAAS_AUTH_TOKEN`、管理端地址和 AaaS 检索地址。转换代码独立位于 `easy_agent/knowledge/ragflow/local_v017_bridge.py`，不会进入行内请求路径。

## 健康检查

```bash
curl -fsS http://127.0.0.1:9380/api/v1/datasets \
  -H "Authorization: Bearer $(security find-generic-password -a "$USER" -s easy-agent-ragflow-api-key -w)"
curl -fsS http://127.0.0.1:8000/api/knowledge/v1/capabilities
```

第一个命令会在 shell 进程内读取 Keychain，不会把 token 写入文件。
