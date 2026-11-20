import request from '../utils/request.js'
import { createConnectorsApi } from '../shared/api/connectors.js'

export const {
  getConnectors,
  installConnector,
  uninstallConnector,
  toggleConnector,
  getConnectorTools,
  getConnectorsStatus,
  updateConnectorConfig,
  deleteMcpServer,
  getMcpRaw,
  saveMcpRaw,
} = createConnectorsApi(request)
