# 知识库 P0 运行手册

## 进程与数据责任

- Web：`python -m easy_agent.app`，只执行鉴权、元数据和 NAS 原文落盘，不在请求中等待 RAGFlow 长任务。
- Worker：`python -m easy_agent.knowledge.worker`，处理上传、重试、删除、心跳、超时接管、对账和告警。
- MySQL 是用户、权限、元数据、任务和审计的记录系统；NAS 是原文权威副本；RAGFlow 索引可从 NAS 重建。

Web 与 Worker 必须使用同一 release tag、同一份配置和 Secret。生产必须设置 `operations.worker.mode: queue` 且 `database.fallback_to_sqlite: false`。

空库首次初始化前，由 Secret 管理平台临时注入
`EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD`（不少于 12 位的强随机值）。系统不再创建
`admin/admin` 或使用其他公开默认密码；生产启动发现历史 admin 仍为
`admin`/`123456` 时会强制使用该 Secret 轮换，其他已存在的有效管理员密码不受影响。

## 巡检入口

以 admin Bearer Token 访问：

- `GET /api/knowledge/v1/admin/health`：MySQL、NAS、RAGFlow、Worker 心跳和任务数；
- `GET /api/knowledge/v1/admin/metrics`：Prometheus 文本指标；
- `GET /api/knowledge/v1/admin/alerts`：任务积压、死信、RAGFlow/NAS 不可用；
- `GET /api/knowledge/v1/admin/reconciliation/issues`：MySQL/NAS/RAGFlow 三方差异；
- `GET /api/knowledge/v1/admin/audits`：人员、权限、原文、检索和问答审计。

上述运维接口不返回底座地址、密钥、原文内容或内部异常。

## 故障处理

### 解析卡住或任务积压

1. 用 request ID 检索 Web/Worker/RAGFlow 日志，查看任务 `attempt_count` 和 `last_error_code`。
2. 确认 Worker 心跳；无心跳时重启 Worker。超过 `stale_after_seconds` 的 running 任务会自动回到 retry。
3. 临时错误按退避策略重试；超限进入 `dead_letter`，保留 NAS 原文，管理员排除后再从界面重试。
4. 不直接改 RAGFlow 记录，不删除不能确认归属的远端文档。

### RAGFlow 不可用

Web 仍可访问 MySQL/NAS 中的元数据与原文；新建解析任务持久化后等待重试。先恢复底座健康，再查告警和对账清单。

### NAS 异常

禁止继续上传，检查挂载、容量、权限和延迟。不得绕过 NAS 直接把文档送入 RAGFlow。恢复后运行对账，核验大小与 SHA-256。

### 数据不一致

- `LOCAL_WITHOUT_REMOTE`：从 NAS 使用重试重建索引；
- `REMOTE_WITHOUT_LOCAL`：只报告，核对归属后人工处理；
- `NAS_MISSING`/`NAS_SHA256_MISMATCH`：阻断删除和重建，从已验证备份恢复；
- `STATUS_MISMATCH`：已知远端 ID 的状态由巡检回写，其他差异不自动删除。

## 删除与恢复

文档删除立即从列表、下载和检索中隐藏，并记录 `deleted_at/deleted_by/purge_after`。恢复期内可调用 `POST /documents/{id}/restore` 从 NAS 重建索引；过期后 Worker 销毁原文。

知识库删除同样先软删除并从所有列表和资源接口隐藏，恢复期内可调用 `POST /bases/{id}/restore`；只有超过恢复期后，Worker 才同步销毁 RAGFlow 数据集、NAS 原文和本地映射。

## 备份恢复

```bash
python scripts/knowledge_backup.py --config easy_agent/config/config.prod.yaml backup \
  --output /approved-backup/easyagent-YYYYMMDD --release-tag vX.Y.Z
python scripts/knowledge_backup.py verify --backup /approved-backup/easyagent-YYYYMMDD
```

恢复演练只能恢复到新建空库和空 NAS 目录，工具拒绝覆盖当前生产库。恢复后检查知识库数、文档数、原文 SHA-256、权限隔离、下载和问答引用。
恢复所需的建库账号由 `EASYAGENT_RESTORE_MYSQL_USER`/
`EASYAGENT_RESTORE_MYSQL_PASSWORD` 临时注入，不写入配置或命令行。
