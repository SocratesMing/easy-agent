"""Pydantic 配置模型（2026-10-18 批次 1A 从 config.py 拆出）。

本模块只依赖 pydantic，**不 import config.py**；YAML 加载/解析仍由
``config.py`` 的 ``Config`` 负责，并由其 re-export 本模块全部模型，保证
``from easy_agent.bootstrap.config import LLMConfig`` 等既有导入路径不变。
"""

from typing import Any

from pydantic import BaseModel, Field

# 应用默认欢迎标题（Config.app_welcome_title 的兜底值）。
DEFAULT_APP_WELCOME_TITLE = "Easy Agent，让工作更简单"

# 联网搜索默认地址（config.yaml 未显式配置 api_url 时按 provider 取用）：
#   tavily   —— Tavily Search API（https://docs.tavily.com）
#   internal —— 内网 WebSearch 接口文档中的测试环境地址，生产地址需自行覆盖
DEFAULT_WEB_SEARCH_API_URLS = {
    "tavily": "https://api.tavily.com/search",
    "internal": "http://28.221.28.7:10089/api/v1/webSearch",
}


class RetryConfig(BaseModel):
    enabled: bool = True
    max_retries: int = 3


class ProviderConfig(BaseModel):
    """Individual provider/model configuration"""

    provider: str = ""
    api_key: str = ""
    model: str = ""
    api_base: str = ""
    # 未显式配置时的默认上下文窗口（1M）；实际值以各 provider 段配置为准。
    context_length: int = 1_000_000
    protocol: str = "openai"  # "openai" or "anthropic"
    # 额外的请求头（透传给 LLM 客户端）。值里可用 ``{session_id}`` 占位符，
    # 调用时会替换为当前会话标识——部分网关（如 OpenCode Go）强制要求稳定的
    # 会话标识（x-opencode-session）与自定义 User-Agent，缺失会直接 400。
    headers: dict[str, str] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    """LLM configuration - resolved from the active model selection"""

    api_key: str
    api_base: str | None = None
    model: str = "claude-sonnet-4-6"
    provider: str = "minimax"
    context_length: int = 1_000_000  # Model context window size
    protocol: str = "openai"  # "openai" or "anthropic"
    headers: dict[str, str] = Field(default_factory=dict)
    retry: RetryConfig = Field(default_factory=RetryConfig)


class ToolsConfig(BaseModel):
    skills_dir: str = "./skills"
    prompts_dir: str = "./prompts"
    result_log_truncate: int = 200
    """工具结果写入日志时的截断字符数（默认 200）。

    仅影响日志展示长度，不改变工具返回给模型的内容；读取方见
    工具错误日志（``agent/tool_errors.py``）与流式日志。
    """
    experts_dir: str = "./experts"
    """公共（预置）专家目录：只读扫描，见 services/public_expert_service.py。

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
    pool_timeout: int = 30
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
    workspace_dir: str = "./workspace"
    memories_dir: str = "./memories"
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
    session_thread: bool = False
    """一个会话一个 LangGraph thread（DA-03 P1）：``thread_id = session_id``，每轮只输入本轮消息，
    历史与摘要状态由 checkpointer 保存。默认关闭（每条消息一个 thread、每轮从 DB 重建历史）。
    见 ``docs/design/2026-10-19-da03-session-thread.md``。
    """


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

    机制（详见 ``services/session_uploads.py``）：
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
