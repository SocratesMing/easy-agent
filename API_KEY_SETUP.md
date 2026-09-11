# DeepSeek API Key 安全配置

这个项目通过环境变量 `DEEPSEEK_API_KEY` 读取模型密钥。不要把真实 API key 写进仓库、README、聊天记录、截图或普通配置文件。

## 推荐方式：macOS Keychain

把密钥保存到系统钥匙串。下面命令会弹出/使用系统钥匙串保存能力，密钥不会写入项目文件。

```bash
security add-generic-password \
  -a "$USER" \
  -s easy-agent-deepseek-api-key \
  -w "在这里粘贴你的 DeepSeek API Key" \
  -U
```

保存后，用这个脚本启动 Easy Agent：

```bash
cd /Users/brain/Desktop/金市Agent-知识工程设计/Agent开发/easy-agent
./scripts/start.dev.keychain.sh
```

脚本会从 Keychain 读取密钥并只注入到当前后端进程的环境变量中，不会打印密钥。

## 隐私边界

- 我不会要求你把真实 API key 发到聊天里。
- 我不会读取、打印或记录 Keychain 中的密钥。
- 如果把密钥写入工作区普通文件，我在技术上可能读取到，所以不要这样做。
- Keychain 方案能避免密钥出现在项目文件和 Git diff 里；后端进程仍会在运行时使用该密钥调用 DeepSeek。

## 更新密钥

重新执行保存命令即可覆盖旧密钥：

```bash
security add-generic-password \
  -a "$USER" \
  -s easy-agent-deepseek-api-key \
  -w "新的 DeepSeek API Key" \
  -U
```

然后重启 Easy Agent。

## 删除密钥

```bash
security delete-generic-password \
  -a "$USER" \
  -s easy-agent-deepseek-api-key
```
