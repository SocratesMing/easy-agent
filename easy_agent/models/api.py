"""Pydantic Models for API Request/Response"""

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    system_prompt: str = Field(
        default="你是一个有帮助的 AI 助手.", description="系统提示词"
    )
    max_steps: int = Field(default=50, description="最大执行步数")
    workspace_dir: str = Field(default="./workspace", description="工作目录")


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="会话标题")
    username: Optional[str] = Field(default=None, description="用户名")


class CreateSessionResponse(BaseModel):
    session_id: str = Field(..., description="会话唯一标识符")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    message_count: int = Field(default=0, description="消息数量")


class SessionInfo(BaseModel):
    session_id: str = Field(..., description="会话唯一标识符")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    message_count: int = Field(default=0, description="消息数量")
    pinned: int = Field(default=0, description="是否置顶 0/1")


class SessionDetail(BaseModel):
    session_id: str = Field(..., description="会话唯一标识符")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    messages: List[dict[str, Any]] = Field(default_factory=list, description="消息列表")
    todos: List[dict[str, Any]] = Field(
        default_factory=list, description="任务计划列表"
    )
    usage: Optional[dict[str, Any]] = Field(
        default=None, description="会话累计 token 用量"
    )
    context_length: Optional[int] = Field(
        default=None, description="模型上下文窗口大小"
    )


class MessageModel(BaseModel):
    role: str = Field(..., description="消息角色: user, assistant, system")
    content: str = Field(..., description="消息内容")
    timestamp: Optional[str] = Field(default=None, description="时间戳")
    thinking: Optional[str] = Field(default=None, description="思考内容")
    tool_calls: Optional[List[dict]] = Field(default=None, description="工具调用列表")


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        description="用户消息内容（可为空：只挂技能/连接器、或只上传文件时没有正文）",
        min_length=0,
    )
    session_id: Optional[str] = Field(default=None, description="会话ID")
    message_id: Optional[str] = Field(default=None, description="消息ID")
    enable_deep_think: bool = Field(default=False, description="是否启用深度思考")
    enable_web_search: bool = Field(
        default=False, description="是否开启联网搜索（输入框地球按钮）"
    )
    files: Optional[List[dict]] = Field(default=None, description="上传的文件列表")
    model: Optional[str] = Field(
        default=None,
        description="模型名称（config.models 的 key，如 deepseek/glm）。为空时使用 active model",
    )
    page_context: Optional[str] = Field(
        default=None,
        max_length=8000,
        description="AI 侧边栏附带的页面/功能上下文（用户唤起 AI 时所在页面的描述），仅提供给模型参考",
    )
    skills: Optional[List[str]] = Field(
        default=None,
        description=(
            "本轮选中的技能目录名（输入框技能胶囊选中项），后端据此把 `/目录名` 指令拼进"
            "**模型输入**并落库到用户消息的 skills 字段（气泡按它渲染技能标签）。"
            "为空或未传时回退解析消息文本开头的 `/目录名`（兼容旧前端/手打指令）"
        ),
    )
    pending_expert_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "会话级「待修改专家」ID：用户在专家面板点「编辑」后由前端随**首条消息**"
            "以结构化字段上行（不进消息正文，用户不可见）。服务端做归属校验（只接受"
            "本人创建的可用专家）后绑定为 save_expert 的修改目标；不传/非法则忽略"
        ),
    )
    expert_agent_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "本轮使用的专家 ID（我的专家 ID 或公共专家目录名）；不传=通用 Agent。"
            "专家对话只有一个入口：本字段随 /agent/chat/stream 上行，后端据此加载"
            "专家提示词、补装依赖技能、做职责范围预检（命中即短路拒答，Agent 不创建）"
        ),
    )


class ResumeRequest(BaseModel):
    session_id: str = Field(..., description="会话ID")
    thread_id: str = Field(..., description="HITL 线程ID（与中断时一致）")
    decisions: List[dict[str, Any]] = Field(
        ...,
        description=(
            "审批决策列表，每个元素 {type: approve|reject, message?: str, "
            "interrupt_id?: str, action_index?: int}。带 interrupt_id 时只回答该中断"
            "（取自 approval_required.action_requests[].interrupt_id），其余中断保持挂起；"
            "不带时只回答第一个中断。与待审批中断对不上时返回 400"
        ),
    )
    message_id: Optional[str] = Field(
        default=None,
        description="被恢复（继续）的 assistant 消息ID，用于将本轮工具调用/思考增量关联到该消息",
    )


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    response: str = Field(..., description="AI 响应内容")
    thinking: Optional[str] = Field(default=None, description="思考过程")
    tool_calls: Optional[List[dict]] = Field(default=None, description="工具调用列表")
    usage: Optional[dict] = Field(default=None, description="Token 使用统计")


class ChatCompletionMessage(BaseModel):
    role: str = Field(..., description="消息角色: system / user / assistant")
    content: str = Field(..., description="消息内容")


class ChatCompletionRequest(BaseModel):
    messages: List[ChatCompletionMessage] = Field(
        ..., description="对话消息数组，例如 [{role: system, content: ...}, {role: user, content: ...}]"
    )
    model: Optional[str] = Field(
        default=None,
        description="模型名称（config.models 的 key，如 deepseek/glm）。为空时使用当前激活模型",
    )


class StreamChunk(BaseModel):
    type: str = Field(
        ...,
        description="数据类型: start, thinking_start, thinking, tool_call, tool_result, assistant_start, content, done, error",
    )
    content: str = Field(default="", description="数据内容")
    data: Optional[dict] = Field(default=None, description="附加数据")
    session_id: Optional[str] = Field(default=None, description="会话ID")


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="错误详情")


class HealthResponse(BaseModel):
    status: str = Field(default="healthy", description="服务状态")
    agent_initialized: bool = Field(default=False, description="Agent 是否已初始化")
    database_initialized: bool = Field(default=False, description="数据库是否已初始化")
    database_ready: bool = Field(default=False, description="数据库是否可连通")
    workspace_ready: bool = Field(default=False, description="工作区是否可写")
    detail: str = Field(default="", description="未就绪原因")


class UpdateTitleRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="新的会话标题")


class AddMessageRequest(BaseModel):
    role: str = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")


class AddMessageResponse(BaseModel):
    status: str = Field(default="success", description="状态")
    session_id: str = Field(..., description="会话ID")
    message_count: int = Field(..., description="当前消息数量")


class SessionCountResponse(BaseModel):
    total_sessions: int = Field(..., description="会话总数")


class DeleteSessionResponse(BaseModel):
    status: str = Field(default="deleted", description="状态")
    session_id: str = Field(..., description="被删除的会话ID")


class GetChatHistoryResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    title: str = Field(..., description="会话标题")
    messages: List[dict[str, Any]] = Field(default_factory=list, description="消息列表")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    usage: Optional[dict[str, Any]] = Field(
        default=None, description="会话累计 token 用量"
    )
    context_length: Optional[int] = Field(
        default=None, description="模型上下文窗口大小"
    )


class UserProfile(BaseModel):
    user_id: str = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    organization_id: str = Field(default="", description="机构ID")
    email: str = Field(default="", description="用户邮箱")
    bound_ip: str = Field(default="", description="绑定IP")
    employee_id: str = Field(default="", description="工号")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class UserAccount(BaseModel):
    username: str = Field(..., description="用户名")
    employee_id: str = Field(default="", description="工号")
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")


class UserListResponse(BaseModel):
    total: int = Field(..., description="用户总数")
    users: List[UserAccount] = Field(default_factory=list, description="用户列表")


class UpdateUserProfileRequest(BaseModel):
    username: Optional[str] = Field(default=None, description="用户名（注册后不可更改，此字段已废弃）")
    organization_id: Optional[str] = Field(default=None, description="机构ID（注册后不可更改，此字段已废弃）")
    email: Optional[str] = Field(default=None, description="用户邮箱")


class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名或工号")
    password: str = Field(..., description="密码", min_length=3, max_length=20)


class RegisterRequest(BaseModel):
    """注册项：用户名 + 密码 + 工号。

    机构ID 与邮箱不再是注册项——机构ID 仍可用于系统提示词注入（由管理员在库中维护），
    邮箱可在登录后通过「更新资料」接口补填。
    """

    username: str = Field(..., description="用户名", min_length=2, max_length=50)
    password: str = Field(..., description="密码", min_length=4, max_length=20)
    employee_id: str = Field(..., description="工号（必填，全局唯一，注册后不可更改）", min_length=1, max_length=64)


class PasswordlessLoginRequest(BaseModel):
    username: str = Field(..., description="用户名", min_length=2, max_length=50)
    user_id: str = Field(default="0", max_length=100, description="用户ID：已存在用户忽略此字段；新用户为 0/空时自动生成")


class ResetPasswordRequest(BaseModel):
    username: str = Field(..., description="用户名")
    new_password: str = Field(..., description="新密码", min_length=4, max_length=20)


class AuthResponse(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    username: str = Field(..., description="用户名")
    context_length: int = Field(default=1_000_000, description="模型上下文窗口大小")


class FileInfo(BaseModel):
    filename: str = Field(..., description="文件名")
    file_path: str = Field(..., description="文件路径")
    file_type: str = Field(..., description="文件类型")
    size: int = Field(..., description="文件大小")
    uploaded_at: str = Field(..., description="上传时间")


class FileListResponse(BaseModel):
    files: List[FileInfo] = Field(default_factory=list, description="文件列表")
    total: int = Field(..., description="文件总数")


class ScheduledTaskInfo(BaseModel):
    task_id: str = Field(..., description="任务ID")
    username: str = Field(..., description="用户名")
    session_id: str = Field(default="", description="创建任务的会话ID")
    name: str = Field(..., description="任务名称")
    description: str = Field(default="", description="任务描述")
    schedule_cron: str = Field(..., description="Cron 表达式（5字段）")
    task_prompt: str = Field(..., description="任务执行时发送给 Agent 的提示词")
    enabled: bool = Field(default=True, description="是否启用")
    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")
    last_run_at: str = Field(default="", description="上次执行时间")
    next_run_at: str = Field(default="", description="下次执行时间")


class ScheduledTaskRunInfo(BaseModel):
    run_id: str = Field(..., description="执行记录ID")
    task_id: str = Field(..., description="任务ID")
    session_id: str = Field(default="", description="执行时创建的会话ID")
    status: str = Field(..., description="执行状态: running/succeeded/failed")
    started_at: str = Field(..., description="开始时间")
    finished_at: str = Field(default="", description="完成时间")
    result_summary: str = Field(default="", description="执行结果摘要")
    error_message: str = Field(default="", description="错误信息")


class DeleteScheduledTaskResponse(BaseModel):
    status: str = Field(default="deleted", description="状态")
    task_id: str = Field(..., description="任务ID")


# ── Settings API 响应模型（ARCH-08：新增/变更路由强制 response_model）──
class MemoryContentResponse(BaseModel):
    content: str
    path: str


class MemoryUpdateResponse(BaseModel):
    status: str
    chars: int


class ContentResponse(BaseModel):
    content: str


class SkillInfo(BaseModel):
    name: str
    description: str
    description_file: str


class SkillsResponse(BaseModel):
    skills: list[SkillInfo]


class ModelInfo(BaseModel):
    name: str
    model: str
    provider: str
    protocol: str
    context_length: int | None = None
    is_active: bool


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    active_model: str


class WebSearchStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    reason: str
    provider: str
    default_time_range: str
    time_ranges: list[str]


class UploadLimitsResponse(BaseModel):
    enabled: bool
    max_file_size_mb: int
    max_files_per_message: int
    max_total_size_mb: int


class StatusResponse(BaseModel):
    status: str


class MessageResponse(BaseModel):
    status: str
    message: str


class PresetQuestionGroup(BaseModel):
    """首页预置问题：一个分类展开成一张或多张卡片。"""

    category: str = "预设问题"
    icon: str = ""
    questions: list[str] = []


class AuthConfigResponse(BaseModel):
    context_length: int
    preset_questions: list[PresetQuestionGroup]
    win: bool
    agent_env: str
    idle_logout_minutes: int
    app_welcome_title: str


class SessionTitleUpdateResponse(BaseModel):
    status: str
    session_id: str
    title: str


class SessionPinUpdateResponse(BaseModel):
    status: str
    session_id: str
    pinned: int


class SessionUploadResponse(BaseModel):
    id: str
    filename: str
    file_path: str
    file_type: str
    size: int
    uploaded_at: str
    parsed: bool
    parsed_path: str | None = None
    parse_status: str | None = None


class SessionGeneratedFileInfo(BaseModel):
    id: int
    filename: str
    file_path: str
    file_type: str
    size: int
    created_at: str


class UploadFileResponse(BaseModel):
    id: Optional[int] = None
    filename: str
    file_path: str
    file_type: str
    size: int
    uploaded_at: str


class DeleteFileResponse(BaseModel):
    status: str
    file_id: int


class ParseFileResponse(BaseModel):
    file_path: str
    content: str


class ConnectorServerStatus(BaseModel):
    name: str
    status: str
    tools_count: int
    error: str = ""


class ConnectorStatusResponse(BaseModel):
    statuses: list[ConnectorServerStatus]


class ConnectorInstallResponse(BaseModel):
    status: str
    installed: str
    servers: list[str]
    path: str
    agents_invalidated: int
    server_status: list[ConnectorServerStatus]


class ConnectorUninstallResponse(BaseModel):
    status: str
    uninstalled: str
    servers: list[str]
    path: str
    agents_invalidated: int


class ConnectorToggleResponse(BaseModel):
    status: str
    id: str
    enabled: bool
    agents_invalidated: int


class SessionConnectorSwitchesResponse(BaseModel):
    session_id: str
    connectors: list[str] | None


class SessionConnectorSetResponse(BaseModel):
    status: str
    session_id: str
    connectors: list[str]
    unknown: list[str]
    agents_invalidated: int


class ConnectorToolInfo(BaseModel):
    name: str
    description: str


class ConnectorToolsResponse(BaseModel):
    name: str
    tools: list[ConnectorToolInfo]
    error: str = ""


class ConnectorConfigUpdateResponse(BaseModel):
    status: str
    id: str
    applied: list[str]
    agents_invalidated: int
    server_status: list[ConnectorServerStatus]


class McpRawResponse(BaseModel):
    path: str
    content: str


class McpSaveResponse(BaseModel):
    status: str
    path: str
    servers: list[str]
    agents_invalidated: int
    server_status: list[ConnectorServerStatus]


class McpAddResponse(BaseModel):
    status: str
    added: list[str]
    servers: list[str]
    path: str
    agents_invalidated: int
    server_status: list[ConnectorServerStatus]


class McpDeleteResponse(BaseModel):
    status: str
    deleted: str
    servers: list[str]
    path: str
    agents_invalidated: int
