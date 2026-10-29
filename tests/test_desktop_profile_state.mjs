import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const pluginSource = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')

function createHarness({ apiImpl, initialProfile = 'default', quietErrors = false } = {}) {
  const stateValues = new Map()
  let hookIndex = 0

  let currentApi = apiImpl

  const context = {
    console: { ...console, warn: () => {}, ...(quietErrors ? { error: () => {} } : {}) },
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
    MEMORY_PAGE_SIZE: 100,
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

test('profile switch never keeps the previous profile memory after list failure', async () => {
  let failProfileBList = false
  const harness = createHarness({
    initialProfile: 'profileA',
    quietErrors: true,
    apiImpl: async (url) => {
      const profile = new URLSearchParams(url.split('?')[1] || '').get('profile')
      if (url.startsWith('/stats')) {
        return { total_memories: 1, db_path: `/${profile}.db`, plugin_version: '0.1.0' }
      }
      if (url.startsWith('/memories')) {
        if (profile === 'profileB' && failProfileBList) {
          throw new Error('profileB list unavailable')
        }
        return {
          items: [{
            id: 1,
            category: 'preference',
            content: `private-${profile}`,
            created_at: '2026-10-29 12:00:00',
            updated_at: '2026-10-29 12:00:00',
            source: 'manual'
          }]
        }
      }
      return {}
    }
  })

  let state = harness.run()
  await state.reloadAll()
  state = harness.run()
  assert.equal(state.activeMemory.content, 'private-profileA')

  state.handleOpenModal(state.activeMemory)
  state = harness.run()
  assert.equal(state.showAddModal, true)

  failProfileBList = true
  state.handleSelectProfile('profileB')
  state = harness.run()
  assert.equal(state.activeMemory, null)
  assert.equal(state.showAddModal, false)
  assert.equal(state.editId, null)

  await state.reloadAll()
  state = harness.run()
  assert.equal(state.items.length, 0)
  assert.equal(state.activeMemory, null)
  assert.equal(state.memoryLoadError, true)
  assert.equal(state.selectedProfile, 'profileB')
})

test('memory list pagination requests successive offsets and stops at the filtered total', async () => {
  const memoryRequests = []
  let backendTotal = 205
  const harness = createHarness({
    initialProfile: 'profileA',
    apiImpl: async (url) => {
      if (url.startsWith('/stats')) {
        return { total_memories: 205, db_path: '/profileA.db', plugin_version: '0.1.0' }
      }
      if (url.startsWith('/memories')) {
        memoryRequests.push(url)
        const params = new URLSearchParams(url.split('?')[1] || '')
        const offset = Number(params.get('offset'))
        return {
          total: backendTotal,
          offset,
          items: offset < backendTotal
            ? [{ id: offset + 1, category: 'preference', content: `memory-${offset}` }]
            : []
        }
      }
      return {}
    }
  })

  let state = harness.run()
  await state.reloadAll()
  state = harness.run()
  assert.equal(state.listTotal, 205)
  assert.equal(state.offset, 0)

  await state.goToNextPage()
  state = harness.run()
  assert.equal(state.offset, 100)
  assert.match(memoryRequests.at(-1), /offset=100/)

  await state.goToNextPage()
  state = harness.run()
  assert.equal(state.offset, 200)
  assert.match(memoryRequests.at(-1), /offset=200/)

  await state.goToNextPage()
  assert.equal(memoryRequests.length, 3, 'does not request beyond the last partial page')

  await state.goToPreviousPage()
  state = harness.run()
  assert.equal(state.offset, 100)
  assert.match(memoryRequests.at(-1), /offset=100/)

  backendTotal = 50
  await state.goToNextPage()
  state = harness.run()
  assert.equal(state.listTotal, 50)
  assert.equal(state.offset, 0)
  assert.equal(state.items[0].id, 1)
  assert.deepEqual(
    memoryRequests.slice(-2).map((url) => new URLSearchParams(url.split('?')[1]).get('offset')),
    ['200', '0'],
    'when the result set shrinks, it reloads the last valid page'
  )
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

function renderMemoryTopHeader(props) {
  const start = pluginSource.indexOf('function MemoryTopHeader({')
  const end = pluginSource.indexOf('\n}\n\n/**\n * サブ情報バー', start)
  assert.notEqual(start, -1, 'memory top header exists')
  assert.notEqual(end, -1, 'memory top header ends before the profile bar')
  const context = vm.createContext({
    jsx: (type, values) => ({ type, props: values }),
    jsxs: (type, values) => ({ type, props: values }),
    S: { flexBetween: {}, flexCol: {}, flexRow: {}, btnPrimary: {} },
    Codicon: 'Codicon',
    t: {
      searchPlaceholder: 'Search memories',
      searchHint: 'Separate terms with spaces; all terms must match',
      addMemory: 'Add memory',
      category: 'Category'
    }
  })
  const component = vm.runInContext(
    `${pluginSource.slice(start, end + 2)}\nMemoryTopHeader`,
    context
  )
  return component(props)
}

function renderMemoryListColumn(props) {
  const start = pluginSource.indexOf('function MemoryListColumn({')
  const end = pluginSource.indexOf('\n}\n\n/**\n * 右カラム', start)
  assert.notEqual(start, -1, 'memory list component exists')
  assert.notEqual(end, -1, 'memory list component ends before the detail view')
  const context = vm.createContext({
    jsx: (type, values) => ({ type, props: values }),
    jsxs: (type, values) => ({ type, props: values }),
    S: { btnAction: {}, ellipsis: {} },
    MemoryListItem: 'MemoryListItem',
    t: {
      loading: 'Loading',
      listLoadError: 'Could not load memories',
      retry: 'Retry',
      noMatches: 'No matches',
      noMemories: 'No memories',
      tryOtherKeyword: 'Try another keyword',
      emptyHint: 'Add a memory',
      previousPage: 'Previous',
      nextPage: 'Next',
      pageStatus: (page, pages, total) => `Page ${page} of ${pages} (${total} items)`
    }
  })
  const component = vm.runInContext(
    `${pluginSource.slice(start, end + 2)}\nMemoryListColumn`,
    context
  )
  return component(props)
}

function collectElements(node, type, result = []) {
  if (Array.isArray(node)) {
    for (const child of node) collectElements(child, type, result)
  } else if (node && typeof node === 'object') {
    if (node.type === type) result.push(node)
    collectElements(node.props?.children, type, result)
  }
  return result
}

test('memory list shows page controls and an explicit retry state', () => {
  const baseProps = {
    loading: false,
    loadError: false,
    items: [{ id: 101, category: 'preference', content: 'page two' }],
    query: '',
    selectedId: 101,
    onSelectItem() {},
    isCompact: false,
    offset: 100,
    pageSize: 100,
    total: 205,
    onRetry() {},
    onPreviousPage() {},
    onNextPage() {}
  }
  const paged = renderMemoryListColumn(baseProps)
  const pageButtons = collectElements(paged, 'button')

  assert.match(collectText(paged), /Page 2 of 3 \(205 items\)/)
  assert.equal(pageButtons.length, 2)
  assert.equal(pageButtons[0].props.disabled, false)
  assert.equal(pageButtons[1].props.disabled, false)

  const lastPage = renderMemoryListColumn({ ...baseProps, offset: 200 })
  const lastPageButtons = collectElements(lastPage, 'button')
  assert.equal(lastPageButtons[1].props.disabled, true)

  const failed = renderMemoryListColumn({ ...baseProps, loadError: true, items: [] })
  assert.match(collectText(failed), /Could not load memoriesRetry/)
  assert.equal(collectElements(failed, 'button').length, 1)
})

test('memory search explains space-separated AND matching', () => {
  const header = renderMemoryTopHeader({
    query: '',
    setQuery() {},
    categoryPills: [],
    selectedCategory: 'all',
    onSelectCategory() {},
    onOpenAddModal() {}
  })

  assert.match(collectText(header), /Separate terms with spaces; all terms must match/)
})

test('an unconfirmed storage path is not shown as the default database', () => {
  const text = collectText(renderSubBar('', 'profileB'))

  assert.equal(text.includes('~/.hermes/memory.db'), false)
  assert.equal(text.includes('profileB'), true)
})

test('a confirmed storage path stays visible', () => {
  const text = collectText(renderSubBar('/path/to/profileA/memory.db', 'profileA'))

  assert.equal(text.includes('/path/to/profileA/memory.db'), true)
})
