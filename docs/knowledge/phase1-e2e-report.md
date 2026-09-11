# 知识工程一期 MVP 联调报告

日期：2026-08-14

## 2026-08-24 本地环境复验

- RAGFlow、MySQL、Elasticsearch、MinIO、Valkey 五个容器及数据卷均完整保留。
- RAGFlow Web/API 健康检查均返回 HTTP 200。
- 原有知识库保留 3 篇文档、91 个 Chunk，三篇文档均为 `ready / 100%`。
- Ollama `bge-m3:latest` 保留，宿主机和 RAGFlow 容器内均验证可生成 1024 维向量。
- EasyAgent 状态为 `ready`；人民币国际化问题返回 3 条证据，最高相似度仍为
  `0.7495989923161519`，无 warning。
- 知识工程测试复验结果为 `56 passed`；浏览器复验 RAGFlow 知识库页面、
  EasyAgent 团队空间、部门查看者权限和文档列表均正常。
- 新增 `scripts/start.dev.ragflow.local.sh` 和
  `scripts/status.dev.ragflow.local.sh`，分别用于无破坏启动和状态检查。

## 验收环境

- EasyAgent：本地开发服务，知识工程配置独立挂载。
- RAGFlow：官方 `v0.17.2-slim`，本地 Docker/Colima 运行。
- 向量模型：macOS 原生 Ollama `bge-m3`，RAGFlow 容器通过
  `host.docker.internal` 调用。
- 本地轻量解析：`Plain Text`。独立 YAML 默认仍为 `DeepDOC`，供行内环境使用。
- 凭据：RAGFlow API token 与本地测试密码存储在 macOS Keychain，未写入仓库。

## 自动化回归

| 检查项 | 结果 |
|---|---|
| Python 测试 | `121 passed, 26 warnings` |
| 前端生产构建 | 成功，Vite 完成 705 个模块转换 |
| Git 空白检查 | 通过 |

现存告警来自项目已有的 Starlette/httpx、`datetime.utcnow()`、fork 及前端大包提示，
不阻塞一期功能。

## 真实研报联调

知识库 `market_reports_2026_e2e` 上传并解析以下三篇 PDF，最终状态均为
`ready / 100%`：

1. `CICC_全球外汇周报：人民币国际化的政策主线.pdf`
2. `CICC_大宗商品：图说大宗：宏观冲击或仍有反复.pdf`
3. `CICC_简评：融资需求持续收缩，利率下降仍有空间 ——6月金融数据点评.pdf`

原始研报文件未修改。首次 DeepDOC 慢任务产生的远端副本已通过 EasyAgent API
删除，随后按本地轻量模式重新上传；RAGFlow、Elasticsearch、MinIO、MySQL、
Valkey 和 Ollama 的真实链路均参与了本次验证。

| 问题 | 证据数 | 命中来源 | 最高相似度 |
|---|---:|---|---:|
| 研报如何概括人民币国际化的政策主线？ | 5 | 人民币国际化研报 | 0.7496 |
| 融资需求持续收缩时，研报对利率走势的判断是什么？ | 5 | 6 月金融数据点评 | 0.5150 |

两次检索均无 warning，EasyAgent 只向 RAGFlow 发送服务端重新校验后的已授权、
已就绪文档 ID，并把返回片段映射回 EasyAgent 本地文档 ID。

## 权限与前端验收

- 所有者可管理知识库、文档和权限。
- 同部门用户通过团队空间自动获得查看者权限，可查看、预览、下载、选择知识库和提问。
- 非同部门用户默认不可见；授予部门权限后可见，替换为显式用户维护者权限后可上传和维护文档，但不能管理权限。
- 浏览器实测知识工程入口、个人/团队/共享空间、文档状态与筛选、任务中心、知识问答区均正常呈现。
- 浏览器创建空会话后，成功选择并保存一个团队知识库；按钮状态由“选择知识库”更新为“知识库 1”。

## 数据边界说明

真实研报向量化与检索完全在本机 RAGFlow/Ollama 闭环完成。未将研报证据发送给
外部 DeepSeek；`/ask` 的证据拼装、引用和无证据降级已由自动化测试覆盖，DeepSeek
基础连接也已单独验证。若要进行携带真实研报证据的外部模型实答验收，需要数据
所有者明确授权；行内部署时应改接行内模型服务。

## 本地地址

- EasyAgent：`http://127.0.0.1:8000`
- RAGFlow Web：`http://127.0.0.1/`
- RAGFlow API：`http://127.0.0.1:9380/api/v1`
- Ollama：`http://127.0.0.1:11434`（仅本机监听）

部署和重建步骤见 `deploy/ragflow-v0.17.2/README.md`。
