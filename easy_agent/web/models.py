"""Pydantic Models for API Request/Response"""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    system_prompt: str = Field(default="你是一个有帮助的 AI 助手.", description="系统提示词")
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


class SessionDetail(BaseModel):
    session_id: str = Field(..., description="会话唯一标识符")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    messages: List[dict[str, Any]] = Field(default_factory=list, description="消息列表")


class MessageModel(BaseModel):
    role: str = Field(..., description="消息角色: user, assistant, system")
    content: str = Field(..., description="消息内容")
    timestamp: Optional[str] = Field(default=None, description="时间戳")
    thinking: Optional[str] = Field(default=None, description="思考内容")
    tool_calls: Optional[List[dict]] = Field(default=None, description="工具调用列表")


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息内容", min_length=1)
    session_id: Optional[str] = Field(default=None, description="会话ID")
    message_id: Optional[str] = Field(default=None, description="消息ID")
    enable_deep_think: bool = Field(default=False, description="是否启用深度思考")
    files: Optional[List[dict]] = Field(default=None, description="上传的文件列表")
    use_knowledge_base: bool = Field(default=False, description="是否启用知识库检索")


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    response: str = Field(..., description="AI 响应内容")
    thinking: Optional[str] = Field(default=None, description="思考过程")
    tool_calls: Optional[List[dict]] = Field(default=None, description="工具调用列表")
    usage: Optional[dict] = Field(default=None, description="Token 使用统计")


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


class UserProfile(BaseModel):
    user_id: str = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    organization_id: str = Field(default="", description="机构ID")
    email: str = Field(default="", description="用户邮箱")
    bound_ip: str = Field(default="", description="绑定IP")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class UpdateUserProfileRequest(BaseModel):
    username: Optional[str] = Field(default=None, description="用户名")
    organization_id: Optional[str] = Field(default=None, description="机构ID")
    email: Optional[str] = Field(default=None, description="用户邮箱")


class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码", min_length=4, max_length=20)


class RegisterRequest(BaseModel):
    username: str = Field(..., description="用户名", min_length=2, max_length=50)
    password: str = Field(..., description="密码", min_length=4, max_length=20)
    email: Optional[str] = Field(default="", description="邮箱")


class ResetPasswordRequest(BaseModel):
    username: str = Field(..., description="用户名")
    new_password: str = Field(..., description="新密码", min_length=4, max_length=20)


class AuthResponse(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    username: str = Field(..., description="用户名")


class FileInfo(BaseModel):
    filename: str = Field(..., description="文件名")
    file_path: str = Field(..., description="文件路径")
    file_type: str = Field(..., description="文件类型")
    size: int = Field(..., description="文件大小")
    uploaded_at: str = Field(..., description="上传时间")


class FileListResponse(BaseModel):
    files: List[FileInfo] = Field(default_factory=list, description="文件列表")
    total: int = Field(..., description="文件总数")
