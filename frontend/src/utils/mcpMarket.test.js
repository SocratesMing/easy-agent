import assert from 'node:assert/strict'
import test from 'node:test'

import { syncMarketAddedState } from './mcpMarket.js'

test('updates market added state from persisted server names', () => {
  const marketServers = [
    { name: 'kept-server', added: true },
    { name: 'removed-server', added: true },
    { name: 'unavailable-server', added: false },
  ]

  const result = syncMarketAddedState(marketServers, ['kept-server'])

  assert.deepEqual(result, [
    { name: 'kept-server', added: true },
    { name: 'removed-server', added: false },
    { name: 'unavailable-server', added: false },
  ])
  assert.notEqual(result[0], marketServers[0])
})
