import { api } from '../../api'

export function listWebSearchProviders() {
  return api.get('/web-search/providers').then(r => r.data)
}

export function updateWebSearchProvider(providerId, payload) {
  return api.put(`/web-search/providers/${encodeURIComponent(providerId)}`, payload).then(r => r.data)
}

export function testWebSearchProvider(providerId, query = 'nanobot') {
  return api.post(`/web-search/providers/${encodeURIComponent(providerId)}/test`, { query }).then(r => r.data)
}

