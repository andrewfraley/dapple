// Thin wrapper over /api. Every call surfaces the server's `detail` message,
// which is what the status line shows.

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const text = await response.text()
  const body = text ? JSON.parse(text) : null
  if (!response.ok) {
    const detail = body?.detail
    if (!detail) throw new Error(`HTTP ${response.status}`)
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return body
}

export const getDevices = () => request('/api/devices')
export const refreshDevices = () => request('/api/devices/refresh', { method: 'POST' })
export const getPresets = () => request('/api/presets')
export const getConfig = () => request('/api/config')

// ---- groups: what each one is showing, and changing it ---------------------

export const getGroups = () => request('/api/groups')
// Read from the strands, so it notices changes made outside Dapple; slower than getGroups.
export const getGroupLive = (id) => request(`/api/groups/${encodeURIComponent(id)}/live`)

export const applyToGroup = (id, pattern) =>
  request(`/api/groups/${encodeURIComponent(id)}/apply`, {
    method: 'POST',
    body: JSON.stringify(pattern),
  })

// The preset name goes in the body, not the path — names have spaces in them.
export const applyPresetToGroup = (id, name) =>
  request(`/api/groups/${encodeURIComponent(id)}/preset`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })

export const setGroupBrightness = (id, value) =>
  request(`/api/groups/${encodeURIComponent(id)}/brightness`, {
    method: 'POST',
    body: JSON.stringify({ value }),
  })

export const turnGroupOn = (id) =>
  request(`/api/groups/${encodeURIComponent(id)}/on`, { method: 'POST' })
export const turnGroupOff = (id) =>
  request(`/api/groups/${encodeURIComponent(id)}/off`, { method: 'POST' })

// ---- presets ---------------------------------------------------------------

export const savePreset = (name, pattern) =>
  request(`/api/presets/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(pattern),
  })

export const deletePreset = (name) =>
  request(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' })

// ---- configuration ---------------------------------------------------------

export const createGroup = (name) =>
  request('/api/config/groups', { method: 'POST', body: JSON.stringify({ name }) })

export const renameGroup = (id, name) =>
  request(`/api/config/groups/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({ name }),
  })

export const deleteGroup = (id) =>
  request(`/api/config/groups/${encodeURIComponent(id)}`, { method: 'DELETE' })

export const reorderGroups = (ids) =>
  request('/api/config/groups/order', { method: 'PUT', body: JSON.stringify({ ids }) })

export const reorderGroupStrands = (id, hosts) =>
  request(`/api/config/groups/${encodeURIComponent(id)}/strands/order`, {
    method: 'PUT',
    body: JSON.stringify({ hosts }),
  })

export const addStrand = (strand) =>
  request('/api/config/strands', { method: 'POST', body: JSON.stringify(strand) })

export const updateStrand = (host, strand) =>
  request(`/api/config/strands/${encodeURIComponent(host)}`, {
    method: 'PUT',
    body: JSON.stringify(strand),
  })

export const moveStrand = (host, group, index) =>
  request(`/api/config/strands/${encodeURIComponent(host)}/group`, {
    method: 'PUT',
    body: JSON.stringify(index === undefined ? { group } : { group, index }),
  })

export const deleteStrand = (host) =>
  request(`/api/config/strands/${encodeURIComponent(host)}`, { method: 'DELETE' })

// ---- home assistant --------------------------------------------------------

export const getMqtt = () => request('/api/config/mqtt')

// Leave `password` out to keep the saved one; the server never sends it back.
export const saveMqtt = (settings) =>
  request('/api/config/mqtt', { method: 'PUT', body: JSON.stringify(settings) })
