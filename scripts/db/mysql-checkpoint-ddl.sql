-- Easy Agent HITL checkpoint 表结构（审计基线）
--
-- ⚠️ 本文件是 easy_agent/persistence/mysql_saver.py 里 DDL_STATEMENTS 的**逐字副本**，
--    只用于 DBA 审计 / 手工建库 / 变更评审。**不要单独改这里**：改 mysql_saver.py 的
--    常量，再重新生成本文件；单测 tests/persistence/test_mysql_saver_unit.py 做
--    字符串比对，两边不一致会直接红。
--
-- 运行时并不读这个文件：应用启动时 AsyncMySQLSaver.asetup() 会
--   1) 用 GET_LOCK('<db>.cp_ddl') 串行化多 Pod 并发 DDL；
--   2) 按 mysql_saver.MIGRATIONS 逐版本补齐（只加不减，禁止 DROP COLUMN / 缩小列宽）；
--   3) 把 schema_version=1 与 ns_hash_algo=sha256-16 写进
--      cp_checkpoint_meta，之后每次启动比对 —— ns_hash 算法变了就是历史 checkpoint
--      全部失联，所以宁可启动失败。
--
-- 表名前缀默认 cp_（config.yaml 的 checkpoint.mysql.table_prefix）。多环境共库时靠它隔离，
-- 前缀不能为空，否则 cp_* 表会写进业务表名空间。
--
-- 前置条件：MySQL >= 8.0.19（JSON 列 + 递归 CTE），库/会话字符集 utf8mb4
-- （中文表注释依赖它），账号需要 CREATE / ALTER / INDEX 权限。

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS cp_checkpoint_meta (
  meta_key   VARCHAR(64)  NOT NULL COMMENT '元数据键：schema_version / ns_hash_algo / ddl_applied_at / last_gc_at',
  meta_value VARCHAR(255) NOT NULL COMMENT '元数据值；schema_version 为递增整数串，ns_hash_algo 形如 sha256-16',
  updated_at DATETIME(6)  NOT NULL COMMENT '最后更新时间（UTC，应用显式写入；不用 DEFAULT CURRENT_TIMESTAMP）',
  PRIMARY KEY (meta_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='checkpoint 存储自身的元数据（KV）：DDL 版本号、checkpoint_ns 哈希算法标识。业务代码不读它，仅供 asetup 与运维排查';

CREATE TABLE IF NOT EXISTS cp_checkpoints (
  thread_id            VARCHAR(128) NOT NULL COMMENT '线程 id：<session_id>（会话级）或 <session_id>-<message_id>（单条消息级），定时任务以 sched- 开头',
  session_id           VARCHAR(64)  NOT NULL COMMENT '会话 id 冗余列，写入时由 session_id_of_thread() 解析；供 GC/TTL 直接 JOIN，避免全表拉回应用层解析',
  checkpoint_ns_hash   BINARY(16)   NOT NULL COMMENT 'checkpoint_ns 的 sha256 前 16 字节，仅用于索引定位（ns 由运行时决定、长度不可控，原值无法进主键）',
  checkpoint_ns        MEDIUMTEXT   NOT NULL COMMENT '子图命名空间原值，形如 tools:<uuid>|model:<uuid>|...；与 hash 同时作为查询过滤条件，保证 hash 碰撞时也不读错',
  checkpoint_id        VARCHAR(64)  NOT NULL COMMENT 'checkpoint 版本号，UUIDv6（36 字符），字典序≈写入时序，可直接倒序取最新',
  parent_checkpoint_id VARCHAR(64)  NULL     COMMENT '父 checkpoint id；首条为 NULL。delta 回溯与 alist 链式读取沿它上溯，不要用 checkpoint_id 排序代替',
  step                 INT          NULL     COMMENT '该 checkpoint 对应的图执行步号，用于排查；取自 metadata.step',
  channel_versions     JSON         NOT NULL COMMENT '各 channel 的版本号快照（内联自 checkpoint JSON，避免每次读都解析大 JSON）',
  type                 VARCHAR(64)  NULL     COMMENT 'checkpoint 序列化类型标识；本项目固定写 json',
  checkpoint           JSON         NOT NULL COMMENT 'checkpoint 主体（channel_values 仅保留 JSON 标量，大值在 checkpoint_blobs 表）',
  metadata             JSON         NOT NULL COMMENT '来源、步号、写入方等元数据（含 langgraph 的 source/steps 等字段）',
  created_at           DATETIME(6)  NOT NULL COMMENT '写入时间（UTC，应用显式写入）。不设 DEFAULT CURRENT_TIMESTAMP：该函数取服务器时区，跨时区 Pod 会混入两个时区的时间',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id),
  KEY idx_thread_ns_created (thread_id, checkpoint_ns_hash, created_at),
  KEY idx_thread_created (thread_id, created_at),
  KEY idx_session (session_id),
  KEY idx_parent (thread_id, checkpoint_ns_hash, parent_checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='LangGraph checkpoint 主体：一行 = 某 thread 在某命名空间下的一个版本（即"快照头"）。大对象在 checkpoint_blobs，任务级中间结果在 checkpoint_writes';

CREATE TABLE IF NOT EXISTS cp_checkpoint_blobs (
  thread_id          VARCHAR(128) NOT NULL COMMENT '线程 id，与 checkpoints.thread_id 同源',
  checkpoint_ns_hash BINARY(16)   NOT NULL COMMENT '命名空间哈希，与 checkpoints.checkpoint_ns_hash 一致',
  channel            VARCHAR(128) NOT NULL COMMENT 'channel 名，如 messages / context_compactions / 各节点输出',
  version            VARCHAR(64)  NOT NULL COMMENT '该 channel 的版本号，取自 checkpoints.channel_versions 对应值',
  type               VARCHAR(32)  NOT NULL COMMENT '值的序列化类型：msgpack（正常值）/ empty（该版本下无值）',
  payload            LONGBLOB     NULL     COMMENT '序列化后的 channel 值；type=empty 时为 NULL。列名不能叫 blob（MySQL 保留字）',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, channel, version),
  KEY idx_thread_ns (thread_id, checkpoint_ns_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='channel 值（大对象）：按 (thread, ns, channel, version) 去重，多个 checkpoint 共享同一份，避免"每步重存整个 messages 数组"的写放大';

CREATE TABLE IF NOT EXISTS cp_checkpoint_writes (
  thread_id          VARCHAR(128) NOT NULL COMMENT '线程 id，与 checkpoints.thread_id 同源',
  checkpoint_ns_hash BINARY(16)   NOT NULL COMMENT '命名空间哈希，与 checkpoints.checkpoint_ns_hash 一致',
  checkpoint_id      VARCHAR(64)  NOT NULL COMMENT '产生该次写入的 checkpoint id；interrupt 时可能先于该 checkpoint 的 aput 落库',
  task_id            VARCHAR(64)  NOT NULL COMMENT '产生写入的任务（节点）id',
  idx                INT          NOT NULL COMMENT '同一 (checkpoint, task) 内多个 channel 写入的序号；特殊通道（__interrupt__ 等）用固定负值，见 WRITES_IDX_MAP',
  channel            VARCHAR(128) NOT NULL COMMENT 'channel 名；HITL 审批时值为 __interrupt__',
  task_path          TEXT         NOT NULL COMMENT '任务层级路径，与 checkpoint_ns 同源、长度不可控故用 TEXT；TEXT 无字面量默认值，INSERT 必须显式传（空串也要传）',
  type               VARCHAR(32)  NOT NULL COMMENT '值的序列化类型',
  payload            LONGBLOB     NOT NULL COMMENT '写入值。与 blobs 的区别：这是任务级中间结果，HITL 会长期挂在这里等人审批，不会被后续 aput 覆盖',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id, task_id, idx),
  KEY idx_thread_ns_cp (thread_id, checkpoint_ns_hash, checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='任务级中间写入：interrupt / error / 节点输出。HITL 审批挂起与恢复强依赖本表，禁止单独清理（整 thread 一起删）';
