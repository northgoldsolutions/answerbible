const API_URL = import.meta.env.VITE_API_URL || '/api'

async function request(path, options = {}) {
  const url = `${API_URL}${path}`
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  health: () => request('/health'),
  listProductions: (stage) => request(`/productions${stage ? `?stage=${stage}` : ''}`),
  getProduction: (id) => request(`/productions/${id}`),
  createProduction: (data) => request('/productions', { method: 'POST', body: JSON.stringify(data) }),
  submitResearch: (id, data) => request(`/productions/${id}/research`, { method: 'POST', body: JSON.stringify(data) }),
  submitScript: (id, data) => request(`/productions/${id}/script`, { method: 'POST', body: JSON.stringify(data) }),
  runEvidenceGate: (id) => request(`/productions/${id}/evidence`, { method: 'POST' }),
  humanReview: (id, data) => request(`/productions/${id}/review`, { method: 'POST', body: JSON.stringify(data) }),
  produce: (id) => request(`/productions/${id}/produce`, { method: 'POST' }),
  qualityGate: (id, data) => request(`/productions/${id}/quality`, { method: 'POST', body: JSON.stringify(data) }),
  submitPackaging: (id, data) => request(`/productions/${id}/packaging`, { method: 'POST', body: JSON.stringify(data) }),
  finalApproval: (id, data) => request(`/productions/${id}/approve`, { method: 'POST', body: JSON.stringify(data) }),
}
