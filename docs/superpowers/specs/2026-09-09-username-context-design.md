# 当前用户名注入上下文设计（2026-09-09）

## 背景

技能文档（如 `easy_agent/skills/strategy_fx/SKILL.md`）中的脚本命令带用户标识参数：

```bash
python run_backtest.py --user-id {userId}
```

模型执行时需要知道当前登录用户是谁（例如 `szm`）。但当前 `EasyAgent` 只在**服务端**用
`safe_username` 定位目录（`workspace/{safe_username}/...`、`memories/{safe_username}/AGENTS.md`），
系统提示词中只注入了机构 ID 与虚拟路径 `/workspace/`，**没有用户名**，模型无从得知。

现有系统提示词拼装（`easy_agent/agent.py:507-516`）：

```
主系统提示词
## 当前用户机构（organization_id，若有）
## Workspace: /workspace/
## User Skills: /user-skills/（若有）
## Memory: /workspace/memory.md
## OS: Linux
```

## 目标

1. 每次会话让模型知道当前登录用户名
2. 技能脚本能可靠拿到用户标识（不依赖模型记忆）
3. 用户名仍只在本会话内可见，不泄露其他用户信息

## 方案对比

| 方案 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A 提示词注入 | 系统提示词加 `## 当前用户 用户名: szm` | 改动一行，模型立刻可用 | 依赖模型每次正确替换占位符 |
| B 环境变量注入 | `LocalShellBackend(env={...})` | 脚本层可靠，模型无需记忆 | 现有 skill 的 `{userId}` 写法与之不匹配，单用打折 |
| **C 组合（选定）** | A + B 同时做 | 双保险：模型知情 + 脚本可直接取 | 改动点增至 3 处 |

`LocalShellBackend` 已确认支持 `env: dict[str, str]` 与 `inherit_env` 参数，方案 B 可行。

## 设计

### 1. 数据获取（services/agent_manager.py）

`get_or_create_agent_for_session()` 中创建 `EasyAgent` 前，按用户名查库取 `user_id`：

```python
user_id = ""
try:
    user = get_database().get_user_by_username(username)
    if user:
        user_id = user.user_id or ""
except Exception:
    user_id = ""   # 查不到不阻断会话
```

`user_id` 作为可选参数传给 `EasyAgent`。查不到时留空（不注入、不报错）。

### 2. EasyAgent 接收（agent.py）

`__init__` 新增可选参数 `user_id: str = ""`，保存为 `self.user_id`。

### 3. 系统提示词注入（agent.py）

在 `org_info` 之后追加：

```
## 当前用户
用户名: `szm`
用户ID: `xxx`（无则省略该行）
- 技能文档与脚本中的 `{userId}` / `{username}` 均指当前登录用户名
```

位置放在 `## Workspace` 之前，与机构信息相邻，语义集中。

### 4. 执行环境注入（agent.py）

创建 `LocalShellBackend` 的三处（workspace、user-skills、其他 routes）统一传入：

```python
env={
    "EASY_USERNAME": self.safe_username,
    "EASY_USER_ID": self.user_id or "",
    "EASY_SESSION_ID": self.session_id or "",
}
```

脚本可直接 `os.environ["EASY_USERNAME"]`，模型也可写 `$EASY_USERNAME`。

## 兼容性

- `user_id` 为空时：提示词省略「用户ID」行、env 中该值为空串，不影响既有行为
- 未登录/`default` 用户：注入 `default`，与现有目录逻辑一致
- 不改变工作区虚拟路径策略，真实用户名仍不对模型暴露路径细节

## 测试

`tests/services/test_agent_username_context.py`（新增）：

1. 系统提示词包含 `## 当前用户` 与用户名
2. `user_id` 存在时注入「用户ID」行；不存在时省略
3. `LocalShellBackend` 收到包含 `EASY_USERNAME` 的 env
4. 查库异常时不影响 Agent 构造（降级为空）

## 风险

- 用户名进入模型上下文：属用户本人会话，可接受
- 环境变量注入到 shell 后端：仅本人会话，不跨用户
