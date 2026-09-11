# 团队空间创建与管理权限

团队空间采用“admin 全局控制 + 部门边界”的双重校验：

- 只有 `admin` 能从知识库模块右上角的“人员与权限”进入“人员与部门”页面，再通过人员列表的“团队空间权限”开关授予或取消权限。
- 获授权账号可以创建团队空间，并管理其当前所属部门的全部团队知识库。
- 同部门普通成员默认是查看者，可以查看、检索、问答和下载，不能上传、修改、授权或删除。
- 获授权账号不能管理其他部门团队空间；跨部门显式授权也不能绕过该限制。
- `admin` 天然具有全局团队空间创建与管理权，不需要将自己加入授权名单。
- `admin` 创建团队空间时必须选择一个由在职人员信息支撑的目标部门；不会创建无部门归属的团队库。
- 停用账号的授权立即失效；取消授权后，既有登录态的下一次请求也会立即降级为查看者。
- 全局团队空间授权与单库成员角色分离：前者决定谁可创建并统管本部门团队库，后者只在指定知识库生效。
- `admin` 可在单个团队知识库内向用户或部门授予“查看者”“维护者”“管理员”。单库维护者/管理员不因此获得其他团队库的访问或创建权。
- 非 admin 管理者可继续管理查看者，但不能新增、变更或删除 admin 配置的维护者/管理员角色。

## 管理接口

- `GET /api/knowledge/v1/admin/team-space-managers`：列出当前授权。
- `PUT /api/knowledge/v1/admin/team-space-managers/{user_id}`：请求体为 `{"enabled": true|false}`。
- `GET /api/knowledge/v1/bases` 返回 `team_space_management`；对 admin 同时返回可选在职部门，前端据此控制创建入口和部门选择。
- `POST /api/knowledge/v1/bases` 创建团队库时，admin 传入 `department_id`；非 admin 账号的部门始终以服务端登录信息为准，不允许跨部门指定。

所有管理接口都使用 JWT、服务端 admin 身份校验和元数据审计，不接受前端传入的部门作为授权依据。

## 数据库发布

该能力由不可变迁移 `0003_team_space_managers.sql` 建表。生产发布前显式运行：

```bash
python scripts/migrate_knowledge_schema.py \
  --config easy_agent/config/config.prod.yaml \
  --apply --confirm-target "$MYSQL_DATABASE"
```

生产 Web/Worker 只验证迁移版本和 checksum，不会在启动时隐式修改数据库。
