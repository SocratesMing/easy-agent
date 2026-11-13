# 可视化框架页托管的第三方库

`/agent/viz/frame.html`（沙箱 iframe）按需加载这里的浏览器构建，不打进任何前端包。
内网部署无法访问 CDN，所以随仓库一起发布。

| 文件 | 来源 | 许可证 |
|---|---|---|
| `echarts.min.js` | npm `echarts@6.1.0` 的 `dist/echarts.min.js` | Apache-2.0（`LICENSE-echarts`） |
| `mermaid.min.js` | npm `mermaid@11.17.2` 的 `dist/mermaid.min.js` | MIT（`LICENSE-mermaid`） |

升级时在空目录里取包，只拷这两个文件（不要把整包放进来）：

```bash
npm pack echarts@<版本> && tar -xzf echarts-*.tgz package/dist/echarts.min.js package/LICENSE
npm pack mermaid@<版本> && tar -xzf mermaid-*.tgz package/dist/mermaid.min.js package/LICENSE
```

换了文件名要同步改 `easy_agent/api/viz.py` 的 `ASSETS` 白名单和 `frame.js` 的 `LIBS`。
