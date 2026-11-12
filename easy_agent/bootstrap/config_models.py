"""Pydantic 配置模型（2026-10-18 批次 1A 从 config.py 拆出）。

配置字段名与默认值的唯一来源：``Config.from_yaml`` 把 YAML 各段直接交给这里的模型校验，
已知键列表（``config_validation``）也由模型字段生成。本模块只依赖 pydantic，**不 import
config.py**；``config.py`` re-export 本模块全部模型，``from easy_agent.bootstrap.config
import LLMConfig`` 等既有导入路径不变。
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

# 应用默认欢迎标题（Config.app_welcome_title 的兜底值）。
DEFAULT_APP_WELCOME_TITLE = "Easy Agent，让工作更简单"

# 工具进度中间件阈值的默认值（唯一来源）。
# AgentConfig 字段默认值、build_agent_middleware 的 getattr 兜底、图缓存指纹三处
# 都引用这里的常量——同一批数字只写一份，改默认值不会三处不同步。
DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT = 3
DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT = 8
DEFAULT_TOOL_PROGRESS_STOP_LIMIT = 12
# 硬顶：同一工具连续空结果到这个次数，无论本轮是否取到过数据都升级为「停止」。
# 默认 = empty_limit * 3，覆盖「先取到 A 的数据、再对 B 反复空查」时
# 「按工具计数 + has_data_once」判不出来的场景。
DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT = DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT * 3

# 联网搜索默认地址（config.yaml 未显式配置 api_url 时按 provider 取用）：
#   tavily   —— Tavily Search API（https://docs.tavily.com）
#   internal —— 内网 WebSearch 接口文档中的测试环境地址，生产地址需自行覆盖
DEFAULT_WEB_SEARCH_API_URLS = {
    "tavily": "https://api.tavily.com/search",
    "internal": "http://28.221.28.7:10089/api/v1/webSearch",
}


def blank_as_false(value: Any) -> Any:
    """布尔开关写成 null 或空串（例如 ``${VAR:-}`` 展开为空）时按 false 处理。

    其余取值交给 Pydantic 解析：字符串 ``"false"`` / ``"0"`` / ``"no"`` 是 false。
    """
    return False if value is None or value == "" else value


class RetryConfig(BaseModel):
    enabled: bool = True
    max_retries: int = 3


class PromptEnhancementConfig(BaseModel):
    """独立草稿增强：引用模型配置键，不跟随当前聊天模型。"""

    model: str = Field(default="deepseek", min_length=1)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_input_chars: int = Field(default=10000, gt=0, le=100000)
    max_output_chars: int = Field(default=20000, gt=0, le=200000)
    max_output_tokens: int = Field(default=8192, gt=0, le=65536)


class ProviderConfig(BaseModel):
    """Individual provider/model configuration"""

    provider: str = ""
    api_key: str = ""
    model: str = ""
    api_base: str = ""
    # 未显式配置时的默认上下文窗口（1M）；实际值以各 provider 段配置为准。
    context_length: int = 1_000_000
    protocol: str = "openai"  # 目前只支持 "openai"（OpenAI 兼容接口）
    # 额外的请求头（透传给 LLM 客户端）。值里可用 ``{session_id}`` 占位符，
    # 调用时会替换为当前会话标识——部分网关（如 OpenCode Go）强制要求稳定的
    # 会话标识（x-opencode-session）与自定义 User-Agent，缺失会直接 400。
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers", mode="before")
    @classmethod
    def _headers_default_empty(cls, value):
        return dict(value or {})


class LLMConfig(BaseModel):
    """LLM configuration - resolved from the active model selection"""

    api_key: str
    api_base: str | None = None
    model: str = "claude-sonnet-4-6"
    provider: str = "minimax"
    context_length: int = 1_000_000  # Model context window size
    protocol: str = "openai"  # 目前只支持 "openai"（OpenAI 兼容接口）
    headers: dict[str, str] = Field(default_factory=dict)
    retry: RetryConfig = Field(default_factory=RetryConfig)

    @field_validator("api_base", "model", "context_length", "protocol", mode="before")
    @classmethod
    def _empty_means_default(cls, value, info):
        """当前模型段里留空（空串 / 0 / null）的字段按默认值处理。"""
        return value or cls.model_fields[info.field_name].default


class ToolsConfig(BaseModel):
    skills_dir: str = "./skills"
    prompts_dir: str = "./prompts"
    result_log_truncate: int = 200
    """工具结果写入日志时的截断字符数（默认 200）。

    仅影响日志展示长度，不改变工具返回给模型的内容；读取方见
    工具错误日志（``agent/tool_errors.py``）与流式日志。
    """
    experts_dir: str = "./experts"
    """公共（预置）专家目录：只读扫描，见 services/experts/public.py。

    可写绝对路径（推荐生产环境，如 ``/qts/experts``）或相对路径；相对路径按
    「进程 CWD → 项目根」顺序解析，因此改这里的目录后无需调整启动方式。
    留空时退化为 ``<项目根>/experts``。环境变量 ``EASY_EXPERTS_DIR`` 可在
    配置缺失时兜底（优先级低于本项）。
    """

class SQLiteConfig(BaseModel):
    """SQLite configuration"""

    path: str = "./data/easy_agent.db"


class MySQLPoolConfig(BaseModel):
    """MySQL connection pool configuration"""

    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 3600


class MySQLConfig(BaseModel):
    """MySQL configuration"""

    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "easy_agent"
    charset: str = "utf8mb4"
    pool: MySQLPoolConfig = Field(default_factory=MySQLPoolConfig)
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30


class DatabaseConfig(BaseModel):
    """Database configuration - supports SQLite and MySQL"""

    type: str = "sqlite"
    sqlite: SQLiteConfig = Field(default_factory=SQLiteConfig)
    mysql: MySQLConfig = Field(default_factory=MySQLConfig)


class AgentConfig(BaseModel):
    """Agent configuration"""

    max_steps: int = 50
    tool_progress_enabled: bool = True
    """工具进度提示（空结果终止信号 + 原地打转提醒）。

    ``ls``/``glob``/``grep`` 查不到东西时框架返回的是正常结果，模型拿不到「该停」
    的信号，容易在错误路径上反复重试直到把 ``max_steps`` 烧完。开启后给这类空结果
    补一句终止提示，并对「同工具 + 同参数连续重复」点名次数。只改工具结果文本，
    不改工具行为；出问题可置 false 一键回到旧行为。"""
    tool_progress_empty_limit: int = DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT
    """同一 MCP 工具连续空结果到几次注入「停止查询」指令（默认 3）。

    只对「本轮从未取到过任何数据」生效：一旦取到过数据，空结果只提示
    「该区间无数据」——分页到底、按月份分段遍历遇到空区间都是正常情况。
    取到过数据后仍连续空查到 ``tool_progress_empty_hard_limit`` 次，照样会止损。"""
    tool_progress_converge_limit: int = DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT
    """同一 MCP 工具连续「无进展」到几次注入收敛提示（默认 8）。

    无进展 = 空结果，或结果与之前某次**逐字节相同**（同一批数据被反复取回）。
    分页取数每页内容不同，永不触发。"""
    tool_progress_stop_limit: int = DEFAULT_TOOL_PROGRESS_STOP_LIMIT
    """同一 MCP 工具连续无进展到几次要求停止并输出数据质量报告（默认 12）。

    不得小于 ``tool_progress_converge_limit``，小了会被自动抬到该值。"""
    tool_progress_empty_hard_limit: int = DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT
    """同一 MCP 工具连续空结果到这个次数，无条件止损（默认 9 = 3 × 3）。

    兜住「按工具名计数」判不出来的场景：先查 A 拿到了数据，再对 B 反复空查时
    ``has_data_once`` 已经是 true，普通阈值不会升级。不得小于
    ``tool_progress_empty_limit``，小了会被自动抬到该值。"""
    workspace_dir: str = "./workspace"
    memories_dir: str = "./memories"
    memory_model: str = ""
    """整理记忆（会话记忆、长期记忆）用的模型：填 ``models`` 段里的键；留空 = 用会话当前的对话模型。

    记忆整理在后台跑、输出很短，用不上推理能力：单独配一个便宜、快的模型能省成本，也不跟
    主对话抢同一个模型的限流额度。键不在 ``models`` 里或建模型失败时告警并回落到对话模型。"""
    sessions_dir: str = "./sessions"
    prompt_path: str = "prompts"
    """提示词目录（相对配置目录），内含 system.md、fragments/ 与 memory_*.md。

    目录不存在时依次回落：包内 ``easy_agent/config/prompts`` → 内置默认提示词。
    也可用环境变量 EASY_PROMPTS_DIR 指向外部目录覆盖。
    """
    idle_logout_minutes: int = 0
    """登录态空闲超时（分钟）：后端以最近一次接口调用为起点滑动续期，
    前端超过该时长无操作时自动退出到登录页。0 表示永不过期（默认，后台不启用登录超时机制）。"""
    denied_dirs: list[str | dict[str, Any]] = Field(default_factory=list)
    # 对「能跨会话影响后续行为」的写入走人工审批（D7）：/memories/** 与 /user-skills/**
    # 需要 checkpointer；默认关闭，打开后由 FilesystemPermission(mode="interrupt") 生效
    interrupt_sensitive_writes: bool = False
    """禁止智能体读写的虚拟路径目录列表。

    支持两种格式（可混用）：
    - 字符串：``"/user-skills"`` → 默认禁止 read+write
    - 字典：``{path: "/user-skills", operations: ["write"]}`` → 只禁止指定操作

    operations 可选值：``read``、``write``，默认两者都禁止。
    通过 FilesystemPermission(mode="deny") 传入 create_deep_agent。

    注意：``/user-skills`` 的 write 权限由 ``skill_write`` 接管（默认 new-only 会
    忽略这里的 write 禁令并改用更细的规则），需要整体禁写时请设 skill_write=deny。
    """

    skill_write: str = "new-only"
    """技能库（虚拟路径 /user-skills/）的写权限策略：

    - ``new-only``（默认）：允许创建新技能目录并写入文件，禁止改动/删除已有技能，
      点开头的路径（隐藏文件/目录）一律禁写；
    - ``deny``：整个技能库禁止写入（旧行为）；
    - ``allow``：不做额外限制。

    通过 FilesystemPermission 传入 create_deep_agent，规则生成见
    easy_agent.agent.EasyAgent._build_permissions。
    """

    external_dirs: dict[str, str] = Field(default_factory=dict)
    """外部目录映射：虚拟路径前缀 → 宿主机实际路径。

    将 skill 需要访问的宿主机目录挂载为虚拟路径路由，使文件工具
    (ls/read_file/write_file 等) 能通过虚拟路径访问。
    例: {"/strategy-workspace/": "/home/sututu/code/finance-skills/fast_backtest/workspace"}
    """
    sandbox_enabled: bool = True
    """Shell 命令沙箱：Linux 优先使用 bwrap，无法使用 bwrap 时降级 Landlock。

    沙箱仅允许读写工作区、技能和外部目录，并只读访问系统命令库与当前 Python
    环境。Windows 暂无等效沙箱，默认拒绝 execute；仅在可信单用户环境才可设为
    False 直接执行宿主机命令。
    """
    sandbox_network: str = "host"
    """沙箱内命令的网络（DA-07）：``host``（默认，可联网）或 ``none``（bwrap 断网，只剩回环）。

    Landlock 降级模式受内核限制无法断网，设为 ``none`` 时只记告警；LangSmith 远端沙箱不受影响。
    """
    execute_max_timeout: int = 600
    """模型调用 execute 时能指定的最长超时（秒）；超过的按该值执行（DA-07）。"""
    session_thread: bool = True
    """一个会话一个 LangGraph thread（DA-03 P1）：``thread_id = session_id``，每轮只输入本轮消息，
    历史与摘要状态由 checkpointer 保存。默认开启，与 DeepAgents/LangGraph 的 thread 语义一致；
    显式设为 ``false`` 可退回每条消息一个 thread、每轮从 DB 重建历史的兼容模式。
    见 ``docs/archive/design/2026-10-19-da03-session-thread.md``。
    """

    @field_validator("prompt_path", "skill_write", mode="before")
    @classmethod
    def _empty_means_default(cls, value, info):
        """null、空串等空值按默认值处理，和 YAML 里不写这个键一样。"""
        return value or cls.model_fields[info.field_name].default

    @field_validator("sandbox_network", mode="before")
    @classmethod
    def _sandbox_network_text(cls, value):
        return str(value or cls.model_fields["sandbox_network"].default)

    @field_validator("interrupt_sensitive_writes", mode="before")
    @classmethod
    def _blank_switch_is_off(cls, value):
        return blank_as_false(value)


class MCPToolConfig(BaseModel):
    """Configuration for a single MCP server"""

    name: str
    transport: str = "stdio"  # stdio | sse | http
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class SummarizationConfig(BaseModel):
    """Conversation summarization/compression configuration"""

    enabled: bool = True
    compression_threshold: float = (
        0.8  # Trigger compression at this fraction of context window
    )
    compression_target: float = (
        0.1  # Keep this fraction of context window after compression
    )


class WebSearchConfig(BaseModel):
    """联网搜索配置（输入框地球按钮）。

    与 models 段一致，各字段支持 config.yaml 中的 ``${VAR:-默认值}`` 占位符，
    实际取值来自 ``.env.{AGENT_ENV}``（复用同一套配置加载逻辑）。
    api_key 为空视为未配置：前端联网搜索按钮置灰、后端不注入 search 工具。
    """

    enabled: bool = True
    provider: str = "tavily"
    api_url: str = ""
    """留空时按 provider 取 DEFAULT_WEB_SEARCH_API_URLS 中的默认地址。"""
    api_key: str = ""
    search_depth: str = "basic"
    """仅 tavily：basic / advanced / fast / ultra-fast。"""
    topic: str = "general"
    """仅 tavily：general / news / finance；带时间范围的检索会自动改用 news。"""
    timeout_seconds: float = 30.0
    default_time_range: str = "NoLimit"
    max_results: int = 10
    main_text_max_chars: int = 500
    """单条结果的摘要展示上限（Tavily content / 内网 summary、mainText 回填均适用；
    0 表示不返回正文节选与摘要截断）。"""


class PresetQuestionGroup(BaseModel):
    """预设问题分组，可在配置文件中按分类组织。"""

    category: str = "预设问题"
    icon: str = ""
    questions: list[str] = Field(default_factory=list)


class ExpertTag(BaseModel):
    """专家 Agent 的分类标签（配置文件 ``expert_tags``）。

    用于专家创建时的自动归类：把候选列表交给大模型，让它为每个专家挑一个
    最匹配的 ``tag_id``；前端「专家」Tab 的筛选 chips 用 ``tag_name`` 渲染、
    按 ``tag_id`` 过滤。
    """

    tag_id: str = ""
    tag_name: str = ""


class DistributedLockConfig(BaseModel):
    """多实例（多 pod / 多 worker）部署下的分布式锁配置。

    锁表建在数据库里（MySQL 场景依赖 InnoDB 行锁保证互斥），用于避免同一任务
    被多个实例重复处理，典型场景是定时任务：每个 pod 都会注册同一批 cron，
    到点后只有抢到锁的实例真正执行。

    默认关闭：单实例部署保持引入分布式锁之前的行为，且不产生任何额外查询。
    """

    enabled: bool = False
    key_prefix: str = "easy_agent"
    """锁 key 前缀：多套系统共用同一个库时避免键冲突。"""
    ttl_seconds: float = 300.0
    """锁的存活时间（秒）：持有者崩溃后，其它实例最多等待该时长即可接管。"""
    renew_interval_seconds: float = 100.0
    """长任务的续约间隔（秒），实际取 min(本值, ttl/3)，须小于 ttl_seconds。"""
    retry_interval_seconds: float = 1.0
    """``acquire(wait_seconds>0)`` 等待抢锁时的轮询间隔（秒）。"""


class MySQLCheckpointSectionConfig(BaseModel):
    """``config.yaml`` 的 ``checkpoint.mysql`` 段（自研 ``AsyncMySQLSaver``）。

    **连接参数留空即复用 ``database.mysql.*``**（同实例同库不同表），这是生产默认形态：
    只要把后端切成 ``mysql_saver`` 就能跑，不需要额外填一套连接串。表名前缀
    ``table_prefix`` 是同库隔离的唯一手段，**不允许为空**（否则 cp_ 表会写进业务表名空间）。

    ``record_ttl_days`` 默认 0（不按时间过期）：checkpoint 是"用户点开会话就能续聊"的
    唯一依据，贸然按天数清理会出现"列表里还在、点进去是空的"。要开 TTL 必须同时满足
    ``ttl_only_archived=true``（只清已归档会话）与业务侧会话保留策略已经上线。
    """

    enabled: bool = True
    """业务库是 MySQL 时是否允许 ``auto`` 选中本后端。"""

    host: str = ""
    """留空 = 复用 ``database.mysql.host``。"""

    port: int = 0
    """0 = 复用业务库端口。"""

    user: str = ""
    """留空 = 复用业务库账号。"""

    password: str = ""
    """留空 = 复用业务库密码。"""

    database: str = ""
    """留空 = 复用业务库库名。"""

    charset: str = "utf8mb4"
    """连接字符集。**必须 utf8mb4**：它同时决定建表时中文表/列注释能否正确入库。"""

    table_prefix: str = Field(default="cp_", min_length=1)
    """表名前缀，多环境共库时靠它隔离；为空直接启动失败。"""

    pool_min_size: int = Field(default=2, ge=1)
    pool_max_size: int = Field(default=10, ge=1)
    """单 Pod 连接数上限；多副本时总连接数 = Pod 数 × 该值，需 ≤ MySQL ``max_connections`` 的 30%。"""

    connect_timeout: int = Field(default=5, ge=1)
    """建连超时（秒）。"""

    read_timeout: int = Field(default=10, ge=1)
    """单次查询超时（秒），落到服务端 ``max_execution_time``（只对 SELECT 生效）。"""

    auto_setup: bool = True
    """启动时幂等建表 + 执行未应用的迁移。多 Pod 并发启动用 ``GET_LOCK`` 串行化。"""

    auto_setup_lock_wait: int = Field(default=60, ge=0)
    """抢建表串行锁的等待秒数；超时则**启动失败**，不静默跳过建表。"""

    gc_interval_seconds: int = Field(default=600, ge=0)
    """巡检间隔；0 = 完全关闭（已删会话对账 + TTL 一起关）。"""

    record_ttl_days: int = Field(default=0, ge=0)
    """0 = 不按时间过期（推荐）；>0 时按"整 thread"删除。"""

    ttl_basis: str = "session_updated_at"
    """过期判定依据：``session_updated_at``（推荐，语义是用户真的没再动过）或
    ``checkpoint_created_at``（只跟 checkpoint 表自身耦合）。"""

    ttl_only_archived: bool = True
    """只清理 ``archived=1`` 的会话，避免"列表里还在、点进去空了"。"""

    ttl_batch_size: int = Field(default=200, ge=1)
    """单轮最多删多少个 thread，分批提交，避免 LONGBLOB 的 DELETE 撑爆 redo。"""

    ttl_protect_active_stream: bool = True
    """删除前跳过有活跃流锁的 thread，防误删正在跑的回合。"""

    @field_validator("table_prefix")
    @classmethod
    def _strip_prefix(cls, value: str) -> str:
        prefix = (value or "").strip()
        if not prefix:
            raise ValueError("checkpoint.mysql.table_prefix 不能为空")
        return prefix

    @field_validator("ttl_basis")
    @classmethod
    def _check_ttl_basis(cls, value: str) -> str:
        basis = (value or "session_updated_at").strip() or "session_updated_at"
        if basis not in {"session_updated_at", "checkpoint_created_at"}:
            raise ValueError(
                "checkpoint.mysql.ttl_basis 只能是 session_updated_at 或 checkpoint_created_at"
            )
        return basis


class CheckpointConfig(BaseModel):
    """HITL 人工审批的 checkpoint（config.yaml 的 ``checkpoint`` 段）。

    ``sqlite_path`` 供官方 ``AsyncSqliteSaver``（dev/test 与生产回滚）；
    ``mysql`` 子段供自研 ``AsyncMySQLSaver``（生产多副本共享）。
    """

    sqlite_path: str = ""
    """SQLite 文件路径，``{pod}`` 启动时换成实例名（``EASY_POD_NAME`` → ``POD_NAME`` →
    ``HOSTNAME``），每个 pod 一个文件；相对路径按项目根解析。留空时用旧环境变量
    ``EASY_CHECKPOINT_SQLITE_PATH``，也没有时默认 ``/qts/checkpoint/{pod}.db``
    （见 ``persistence/checkpoints.py``）。"""

    mysql: MySQLCheckpointSectionConfig = Field(default_factory=MySQLCheckpointSectionConfig)
    """MySQL 共享后端配置（``checkpoint.mysql``），不配置时全部走默认值。"""


class HitlConfig(BaseModel):
    """人工审批（config.yaml 的 ``hitl`` 段）。"""

    enabled: bool = True
    """关闭后对话里也不审批，和定时任务一样：工具审批、``interrupt_sensitive_writes`` 的写入审批
    都不生效，也不提供 ``create_scheduled_task``（建定时任务必须有人确认）。"""
    tools: list[str] | None = None
    """执行前要人工审批的工具名；不配时用内置名单 ``agent.hitl.DEFAULT_APPROVAL_TOOLS``。
    ``execute`` 只拦删除类命令、``access_knowledge_original`` 只拦 materialize；MCP 工具写名字即可
    （带不带服务前缀都行），没列出的 MCP 工具仍按服务端标注 / 名字自动判断（见 ``agent/hitl.py``）。"""


class PersonnelConfig(BaseModel):
    """Personnel provisioning and account-source policy."""

    # Fail closed unless a controlled deployment explicitly opts into the
    # legacy public registration flow.
    self_registration_enabled: bool = False
    """自助注册开关：默认关闭，登录页不显示注册入口。"""

    passwordless_login_enabled: bool = False
    """门户免密登录开关：默认关闭；启用后外部系统可通过用户名+用户ID 直登。"""


class UploadLimitsConfig(BaseModel):
    """对话附件上传限额（config.yaml 的 ``upload_limits`` 段）。

    约束对象是**单个会话单次对话**的附件（输入框里待发送的文件列表），发送消息后
    输入框清空、额度自动重置；不是"会话历史累计"。

    单位统一为 MB（1MB = 1024*1024 字节），与前端 formatSize 一致。
    校验点：前端选择文件时即时拦截（体验），后端 upload 接口对单文件兜底（权威）。

    注意：若把 ``max_total_size_mb`` 调到大于 nginx 的 ``client_max_body_size``
    （见 docker/nginx-frontend.conf，现为 100m），会被 nginx 以 413 提前拦截。
    """

    enabled: bool = True
    """总开关：false 时前后端都不做限额校验（应急开关）。"""
    max_file_size_mb: int = Field(default=10, gt=0)
    """单个文件大小上限（MB）。"""
    max_files_per_message: int = Field(default=5, gt=0)
    """单次对话的附件数量上限（输入框内附件总数）。"""
    max_total_size_mb: int = Field(default=20, gt=0)
    """单次对话的附件总大小上限（MB）。"""


class FileContextConfig(BaseModel):
    """会话上传文件的解析落盘与记忆注入（config.yaml 的 ``file_context`` 段）。

    解决的问题：改造前上传文件的解析全文只塞进"本轮带文件"的提示词，第二轮起
    请求不再带文件，模型就看不到内容了（且首轮内容挂在内存中的 Agent 缓存上，
    缓存一驱逐即丢失）。

    机制（详见 ``services/sessions/uploads.py``）：
    1. 上传即解析：解析文本落盘到会话工作区 ``uploads/{文件名}.txt``，一次解析、
       全程复用；
    2. 上传即记清单：文件名/大小/时间/解析状态写入 ``uploads/index.md``，该文件由
       ``agent.py`` 作为**记忆文件**注入 system prompt，模型每轮都知道用户传过哪些
       文件、正文在哪；
    3. 正文不注入：模型按需用内置的 grep/read_file 工具在 ``uploads/`` 里检索，
       避免每轮把全文塞进上下文撑爆窗口；
    4. 本轮刚上传的文件额外注入开头预览（``preview_max_chars``），减少首问时的一次
       工具往返。

    ``enabled=False`` 时整体回退到改造前行为（上传不解析、清单不注入，聊天接口把
    解析全文塞进提示词），作为应急回滚开关。
    """

    enabled: bool = True
    """总开关：false 时回退旧的全量注入行为。"""

    parse_max_file_size_mb: int = Field(default=20, gt=0)
    """超过该大小的文件不做解析（仍记录清单条目），避免上传请求被解析阻塞。"""

    max_stored_chars: int = Field(default=200000, gt=0)
    """单个文件落盘的解析文本上限（字符）；超出截断并在清单中标注。"""

    manifest_max_chars: int = Field(default=2000, gt=0)
    """上传清单（``uploads/index.md``）字符上限；超出只保留最新的文件条目。"""

    preview_max_chars: int = Field(default=2000, ge=0)
    """本轮上传文件注入模型的开头预览字符数（多文件合计）；0 = 不注入预览。"""


class StreamingConfig(BaseModel):
    """SSE 流式响应配置（config.yaml 的 ``streaming`` 段）。

    心跳帧用于穿过反向代理的空闲超时：模型长思考 / 长工具调用期间流上可能几十秒
    没有任何事件，nginx 默认 ``proxy_read_timeout 60s`` 会掐断连接，浏览器报
    ``ERR_INCOMPLETE_CHUNKED_ENCODING 200``。

    心跳是 SSE 注释行（``: keep-alive``），**不是事件**：不进 hub 历史、不落
    ``stream_events`` 表，前端只解析 ``data: `` 行所以会自动忽略。
    """

    sse_heartbeat_seconds: float = 15.0
    """空闲多久补一帧心跳（秒）；<= 0 关闭心跳。须小于反向代理的读超时（nginx 默认 60s）。"""
