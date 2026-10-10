export function syncMarketAddedState(marketServers, persistedServerNames = []) {
  const addedNames = new Set(persistedServerNames)

  return marketServers.map(server => ({
    ...server,
    added: addedNames.has(server.name),
  }))
}
