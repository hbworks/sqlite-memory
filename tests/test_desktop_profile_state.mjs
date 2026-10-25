import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const pluginSource = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')

function createHarness({ apiImpl, initialProfile = 'default' } = {}) {
  const stateValues = new Map()
  let hookIndex = 0

  let currentApi = apiImpl

  const context = {
    console: { ...console, warn: () => {} },
    URLSearchParams,
    host: { state: {} },
    useValue: () => initialProfile,
    useState: (initial) => {
      const id = ++hookIndex
      if (!stateValues.has(id)) {
        stateValues.set(id, typeof initial === 'function' ? initial() : initial)
      }
      const setState = (updater) => {
        const current = stateValues.get(id)
        const next = typeof updater === 'function' ? updater(current) : updater
        stateValues.set(id, next)
      }
      return [stateValues.get(id), setState]
    },
    useRef: (initial) => {
      const id = ++hookIndex
      if (!stateValues.has(id)) {
        stateValues.set(id, { current: initial })
      }
      return stateValues.get(id)
    },
    useCallback: (fn) => fn,
    useMemo: (fn) => fn(),
    useEffect: () => {},
    api: (...args) => currentApi(...args),
    isPluginVersionCompatible: (v) => v === '0.1.0',
    DESKTOP_PLUGIN_VERSION: '0.1.0',
    t: {
      saveError: (e) => e,
      deleteConfirm: () => true,
      deleteError: (e) => e,
      all: 'All',
      preference: 'Preference',
      project: 'Project',
      rule: 'Rule',
      general: 'General',
      totalCount: (c) => `${c} items`
    },
    confirm: () => true
  }

  const start = pluginSource.indexOf('function useMemoryState() {')
  const end = pluginSource.indexOf('\n// =============================================================================\n// 3. UI部品層', start)
  const fnSource = `${pluginSource.slice(start, end)}\nuseMemoryState`
  const runFn = vm.runInContext(fnSource, vm.createContext(context))

  return {
    run: () => {
      hookIndex = 0
      return runFn()
    },
    setApi: (fn) => {
      currentApi = fn
    }
  }
}

test('loadCurrentProfile clears total and dbPath at start and keeps them empty on version check failure', async () => {
  let statsResult = {
    total_memories: 12,
    db_path: '/path/to/profileA/memory.db',
    plugin_version: '0.1.0'
  }

  const harness = createHarness({
    apiImpl: async (url) => {
      if (url.startsWith('/stats')) {
        if (!statsResult) throw new Error('connection failed')
        return statsResult
      }
      if (url.startsWith('/memories')) return { items: [] }
      return {}
    }
  })

  let state = harness.run()
  await state.reloadAll()

  state = harness.run()
  assert.equal(state.total, 12)
  assert.equal(state.dbPath, '/path/to/profileA/memory.db')
  assert.equal(state.versionStatus, 'compatible')

  // 次のプロファイルへの切り替え時に stats の取得が失敗（unavailable）した場合
  statsResult = null
  state.handleSelectProfile('profileB')
  state = harness.run()

  await state.reloadAll()
  state = harness.run()

  assert.equal(state.total, 0, 'total must be cleared to 0 on failure')
  assert.equal(state.dbPath, '', 'dbPath must be cleared to empty on failure')
  assert.equal(state.versionStatus, 'unavailable')
})

test('save does not borrow next profile request ID when profile changes during save', async () => {
  const calls = []
  let resolveSave
  const savePromise = new Promise((resolve) => { resolveSave = resolve })

  const harness = createHarness({
    apiImpl: async (url, options) => {
      calls.push({ url, options })
      if (options?.method === 'POST') {
        await savePromise
        return { ok: true }
      }
      if (url.startsWith('/stats')) {
        return { total_memories: 5, db_path: '/path/db', plugin_version: '0.1.0' }
      }
      if (url.startsWith('/memories')) {
        return { items: [] }
      }
      return {}
    }
  })

  let state = harness.run()
  state.setFormContent('test note')
  state = harness.run()

  // 保存を開始 (非同期で保留)
  const saveTask = state.handleSave({ preventDefault() {} })

  // 保存処理待機中にプロファイルが切り替わり、loadCurrentProfile が実行される
  state.handleSelectProfile('profileB')
  state = harness.run()
  await state.reloadAll()

  calls.length = 0 // ここまでの呼び出しをリセット

  // 保存APIを解決して完了させる
  resolveSave()
  await saveTask

  // 保存処理の完了時、古いプロファイルの stats / fetchMemories が呼ばれていないことを検証
  const oldProfileCalls = calls.filter((c) => c.url.includes('profile=default'))
  assert.equal(oldProfileCalls.length, 0, 'did not fetch stats or memories for old profile using new request ID')
})

test('delete does not borrow next profile request ID when profile changes during delete', async () => {
  const calls = []
  let resolveDelete
  const deletePromise = new Promise((resolve) => { resolveDelete = resolve })

  const harness = createHarness({
    apiImpl: async (url, options) => {
      calls.push({ url, options })
      if (options?.method === 'DELETE') {
        await deletePromise
        return { ok: true }
      }
      if (url.startsWith('/stats')) {
        return { total_memories: 5, db_path: '/path/db', plugin_version: '0.1.0' }
      }
      if (url.startsWith('/memories')) {
        return { items: [] }
      }
      return {}
    }
  })

  let state = harness.run()

  // 削除を開始 (非同期で保留)
  const deleteTask = state.handleDelete('mem_123')

  // 削除処理待機中にプロファイルが切り替わり、loadCurrentProfile が実行される
  state.handleSelectProfile('profileB')
  state = harness.run()
  await state.reloadAll()

  calls.length = 0 // ここまでの呼び出しをリセット

  // 削除APIを解決して完了させる
  resolveDelete()
  await deleteTask

  // 削除処理の完了時、古いプロファイルの stats / fetchMemories が呼ばれていないことを検証
  const oldProfileCalls = calls.filter((c) => c.url.includes('profile=default'))
  assert.equal(oldProfileCalls.length, 0, 'did not fetch stats or memories for old profile using new request ID')
})

function renderSubBar(dbPath, selectedProfile) {
  const start = pluginSource.indexOf('function MemorySubBar({')
  const end = pluginSource.indexOf('\nfunction MemoryListItem(', start)
  assert.notEqual(start, -1, 'profile subbar exists')
  assert.notEqual(end, -1, 'profile subbar ends before the list item')
  const context = vm.createContext({
    jsx: (type, props) => ({ type, props }),
    jsxs: (type, props) => ({ type, props }),
    S: { flexBetween: {}, flexRow: {}, ellipsis: {} },
    Codicon: 'Codicon',
    t: {
      profileLabel: 'プロファイル:',
      defaultProfileLabel: 'デフォルト (~/.hermes)',
      storageLabel: '保存先:',
      storageUnknown: '未確認',
      totalCount: (total) => `合計: ${total} 件`,
      refresh: '更新'
    }
  })
  const MemorySubBar = vm.runInContext(`${pluginSource.slice(start, end)}\nMemorySubBar`, context)
  return MemorySubBar({
    selectedProfile,
    onSelectProfile() {},
    availableProfiles: [selectedProfile],
    dbPath,
    total: 0,
    onReload() {}
  })
}

function collectText(node, result = []) {
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, result)
  } else if (typeof node === 'string' || typeof node === 'number') {
    result.push(String(node))
  } else if (node && typeof node === 'object') {
    collectText(node.props?.children, result)
  }
  return result.join('')
}

test('an unconfirmed storage path is not shown as the default database', () => {
  const text = collectText(renderSubBar('', 'profileB'))

  assert.equal(text.includes('~/.hermes/memory.db'), false)
  assert.equal(text.includes('profileB'), true)
})

test('a confirmed storage path stays visible', () => {
  const text = collectText(renderSubBar('/path/to/profileA/memory.db', 'profileA'))

  assert.equal(text.includes('/path/to/profileA/memory.db'), true)
})
