# 技能多源与启用/停用设计（FW-06）

| 项 | 内容 |
| --- | --- |
| 状态 | 设计草案（待评审，不在本轮实施） |
| 依据 | 审计 FW-06、T1 §4.2；官方 DeepAgents Skills 文档（progressive disclosure、sources last-wins） |
| 背景 | 当前“启用技能 = 把公共技能复制到 `workspace/{user}/skills/`”，停用 = 改名到 `skills-disabled/`；导致公共技能更新不同步、未启用会话完全看不到技能元数据 |

## 1. 目标

- 公共技能以**只读源**挂载（`/skills/`），用户技能为可写源（`/user-skills/`），传给
  `create_deep_agent(skills=["/skills/", "/user-skills/"])`（后者 last-wins）。
- “启用/停用”改为**用户偏好过滤**，不再复制文件；公共技能更新对所有用户即时生效。
- 保留技能中心现有 UI/API 语义（安装/卸载/分类），底层改为偏好记录。

## 2. 数据模型

```text
user_skill_prefs(user_id, skill_key, source, enabled, installed_at, updated_at)
  skill_key = 公共技能 id（目录名）或用户技能目录名
  source    = "public" | "user"
```

- 默认：公共技能 **未启用**（与当前“用户需要主动安装”一致），但可配置
  `skills.auto_enable_public=true` 时默认全部启用。
- 用户新建技能：仍写 `/user-skills/<name>/SKILL.md`（已有写入权限策略）。

## 3. 运行时装配

1. `EasyAgent._build_backend` 增加只读 `/skills/` 路由（`LocalShellBackend(root_dir=<skills_root>, virtual_mode=True)`，权限规则 deny write）。
2. `_resolve_skills_paths()` 返回 `["/skills/"]` + `["/user-skills/"]`（若存在用户技能）。
3. 过滤器：在 `create_deep_agent` 之前按 `user_skill_prefs` 生成临时“技能视图”：
   - 方案 A（推荐）：不改官方 sources，新增轻量 `SkillFilterMiddleware` 或利用 SkillsMiddleware 的 source 列表构造“已启用公共技能目录的聚合视图”（临时目录/符号链接）；
   - 方案 B：按用户偏好把启用的公共技能软链到 `workspace/{user}/skills/`（仍是文件系统视图，但由偏好驱动、可重建）。
   建议先 B（改动小、可灰度），再演进 A。
4. 停用不再改名目录，只改 `enabled=false`，并驱逐用户 Agent 缓存。

## 4. 迁移

- 脚本扫描现有 `workspace/{user}/skills/`：若与公共技能同名且内容哈希一致 → 记为 public 偏好 + 删除副本；否则视为用户自建技能保留。
- 保留 `skills-disabled/` 读取兼容一个版本，之后删除。

## 5. 验收

- 公共技能新增/更新后，无需用户操作即可在已启用会话生效（下一次 Agent 重建）。
- 未启用技能不出现在系统提示词；启用后出现且可读 SKILL.md。
- 技能中心安装/卸载/分类功能与现有 E2E 通过。
- 公共技能目录只读（agent 无法改写）。

## 2026-10-17 进展（Increment 24）

- 已落地**启动期 frontmatter 校验**：`skills.parse_skill_frontmatter()` +
  `skills.validate_skills()`，`app.lifespan` 发现技能后逐条告警（坏技能不阻断启动）。
- 官方多源 `skills=["/skills/", "/user-skills/"]` 与“启用/停用”改偏好过滤仍未实施，
  需产品确认后再做（会改变 agent 可见技能集合）。
