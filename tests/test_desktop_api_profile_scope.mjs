import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const pluginSource = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')

function loadApi(bridge) {
  const start = pluginSource.indexOf('async function api(path, options = {}) {')
  const end = pluginSource.indexOf('\n\nconst t = {', start)
  assert.notEqual(start, -1, 'plugin API helper exists')
  assert.notEqual(end, -1, 'plugin API helper ends before translations')

  const context = vm.createContext({
    URLSearchParams,
    console,
    window: { hermesDesktop: { api: bridge } }
  })
  return vm.runInContext(`${pluginSource.slice(start, end)}\napi`, context)
}

test('forwards the selected profile to the Electron API backend route', async () => {
  let request
  const api = loadApi(async value => {
    request = value
    return { total_memories: 2 }
  })

  await api('/stats?profile=coding')

  assert.equal(request.path, '/api/plugins/sqlite-memory/stats?profile=coding')
  assert.equal(request.profile, 'coding')
})

test('does not call an unmounted compatibility API path', async () => {
  const requests = []
  const api = loadApi(async request => {
    requests.push(request)
    throw new Error('404')
  })

  await assert.rejects(() => api('/stats?profile=coding'), /404/)

  assert.deepEqual(requests.map(request => request.path), ['/api/plugins/sqlite-memory/stats?profile=coding'])
  assert.equal(requests[0].profile, 'coding')
})
