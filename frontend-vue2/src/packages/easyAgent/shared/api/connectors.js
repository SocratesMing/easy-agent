// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/api/connectors.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * 连接器（MCP）API 工厂（双端共享契约，由宿主注入 request 传输适配器）。
 */
function ensureJson(data, url, fallback) {
  if (typeof data === 'string' || data === null) {
    throw new Error(`${fallback}：后端未返回 JSON（${url}，通常是接口不存在或后端未重启）`)
  }
  return data
}

export function createConnectorsApi(request) {
  async function send(config, fallback) {
    const data = await request(config, fallback)
    return ensureJson(data, config.url, fallback)
  }

  return {
    async getConnectors() {
      return send({ url: '/agent/connectors', method: 'get' }, '获取连接器失败')
    },

    async installConnector(id, values = {}, validate = true) {
      return send(
        { url: '/agent/connectors/install', method: 'post', data: { id, values, validate } },
        '安装连接器失败'
      )
    },

    async uninstallConnector(id) {
      return send(
        { url: '/agent/connectors/uninstall', method: 'post', data: { id } },
        '卸载连接器失败'
      )
    },

    async toggleConnector(id, enabled) {
      return send(
        { url: '/agent/connectors/toggle', method: 'post', data: { id, enabled } },
        '切换连接器开关失败'
      )
    },

    async getConnectorTools(name) {
      return send(
        { url: `/agent/connectors/tools?name=${encodeURIComponent(name)}`, method: 'get' },
        '获取工具列表失败'
      )
    },

    async getConnectorsStatus() {
      return send({ url: '/agent/connectors/status', method: 'get' }, '获取连接状态失败')
    },

    async updateConnectorConfig(id, values) {
      return send(
        { url: '/agent/connectors/config', method: 'put', data: { id, values } },
        '保存连接器配置失败'
      )
    },

    async deleteMcpServer(name) {
      return send(
        { url: `/agent/connectors/mcp/${encodeURIComponent(name)}`, method: 'delete' },
        '删除 MCP 服务失败'
      )
    },

    async getMcpRaw() {
      return send({ url: '/agent/connectors/mcp/raw', method: 'get' }, '读取 mcp.json 失败')
    },

    async saveMcpRaw(content) {
      return send(
        { url: '/agent/connectors/mcp/raw', method: 'put', data: { content } },
        '保存 mcp.json 失败'
      )
    },
  }
}
