// Vue3 standalone entry; Vue2 reads links through its host navigation adapter.
export function readStandaloneSessionLink() {
  const url = new URL(window.location.href)
  const linked = url.searchParams.get('session')
  if (linked) {
    url.searchParams.delete('session')
    window.history.replaceState({}, '', url.pathname + url.search + url.hash)
  }
  return linked
}
