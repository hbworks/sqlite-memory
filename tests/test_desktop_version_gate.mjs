import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const pluginSource = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const pluginManifest = readFileSync(new URL('../plugin.yaml', import.meta.url), 'utf8')
const apiSource = readFileSync(new URL('../dashboard/plugin_api.py', import.meta.url), 'utf8')
const dashboardManifest = JSON.parse(readFileSync(new URL('../dashboard/manifest.json', import.meta.url), 'utf8'))
const pageStart = pluginSource.indexOf('function MemoryManagementPage() {')
const pageEnd = pluginSource.indexOf('\n}\n\nexport default', pageStart)
assert.notEqual(pageStart, -1, 'memory management page exists')
assert.notEqual(pageEnd, -1, 'memory management page ends before plugin registration')
const pageSource = pluginSource.slice(pageStart, pageEnd + 2)

function getPackageVersion() {
  const match = pluginManifest.match(/^version:\s*([^\s#]+)/m)
  assert.ok(match, 'plugin manifest declares a version')
  return match[1]
}

function getDesktopApiContractVersion() {
  const match = pluginSource.match(/^const DESKTOP_API_CONTRACT_VERSION = (\d+)$/m)
  assert.ok(match, 'desktop plugin declares its API contract version')
  return Number(match[1])
}

function getBackendApiContractVersion() {
  const match = apiSource.match(/^DESKTOP_API_CONTRACT_VERSION = (\d+)$/m)
  assert.ok(match, 'backend declares its Desktop API contract version')
  return Number(match[1])
}

function loadApiCompatibility() {
  const start = pluginSource.indexOf('function isDesktopApiIncompatible(profileContractVersion) {')
  const end = pluginSource.indexOf('\n}', start)
  assert.notEqual(start, -1, 'Desktop API compatibility check exists')
  assert.notEqual(end, -1, 'Desktop API compatibility check ends')
  const context = vm.createContext({ DESKTOP_API_CONTRACT_VERSION: getDesktopApiContractVersion() })
  return vm.runInContext(`${pluginSource.slice(start, end + 2)}\nisDesktopApiIncompatible`, context)
}

function loadTranslations() {
  const localeStart = pluginSource.indexOf('const getLocale = () => {')
  const localeEnd = pluginSource.indexOf('\n}\n\nfunction isDesktopApiIncompatible', localeStart)
  const tStart = pluginSource.indexOf('\nconst t = {')
  const tEnd = pluginSource.indexOf('\n}\n\n/**\n * カテゴリ別スタイル設定', tStart)
  assert.notEqual(localeStart, -1, 'locale helper exists')
  assert.notEqual(localeEnd, -1, 'locale helper ends')
  assert.notEqual(tStart, -1, 'translation table exists')
  assert.notEqual(tEnd, -1, 'translation table ends')
  const context = vm.createContext({
    document: { documentElement: { lang: 'ja' } },
    navigator: { language: 'ja', languages: ['ja'] }
  })
  return vm.runInContext(
    `${pluginSource.slice(localeStart, localeEnd + 2)}\n${pluginSource.slice(tStart + 1, tEnd + 2)}\nt`,
    context
  )
}

function renderPage(versionStatus, apiContractVersion, pluginVersion) {
  const components = [
    'MemoryTopHeader',
    'MemorySubBar',
    'MemoryListColumn',
    'MemoryDetailColumn',
    'MemoryEditModal'
  ]
  const state = {
    versionStatus,
    pluginVersion,
    apiContractVersion,
    containerRef: null,
    query: '',
    setQuery() {},
    categoryPills: [],
    selectedCategory: 'all',
    setSelectedCategory() {},
    handleOpenModal() {},
    selectedProfile: 'default',
    handleSelectProfile() {},
    availableProfiles: ['default'],
    dbPath: '',
    total: 0,
    reloadAll() {},
    isCompact: false,
    mobileDetailOpen: false,
    items: [],
    loading: false,
    selectedId: null,
    handleSelectItem() {},
    activeMemory: null,
    handleDelete() {},
    setMobileDetailOpen() {},
    showAddModal: false,
    editId: null,
    formCategory: 'preference',
    setFormCategory() {},
    formContent: '',
    setFormContent() {},
    saving: false,
    handleSave() {},
    handleCloseModal() {}
  }
  const context = vm.createContext({
    DESKTOP_API_CONTRACT_VERSION: getDesktopApiContractVersion(),
    useMemoryState: () => state,
    jsx: (type, props) => ({ type, props }),
    jsxs: (type, props) => ({ type, props }),
    S: { page: {} },
    t: loadTranslations(),
    ...Object.fromEntries(components.map((component) => [component, component]))
  })
  const MemoryManagementPage = vm.runInContext(`${pageSource}\nMemoryManagementPage`, context)
  return MemoryManagementPage()
}

function collectTree(node, result = { types: [], text: [] }) {
  if (Array.isArray(node)) {
    for (const child of node) collectTree(child, result)
  } else if (typeof node === 'string' || typeof node === 'number') {
    result.text.push(String(node))
  } else if (node && typeof node === 'object') {
    if (typeof node.type === 'string') result.types.push(node.type)
    collectTree(node.props?.children, result)
  }
  return result
}

test('desktop UI version matches the package and dashboard manifests', () => {
  const desktopVersion = pluginSource.match(/^const DESKTOP_PLUGIN_VERSION = ['"]([^'"]+)['"]/m)?.[1]
  assert.ok(desktopVersion, 'desktop plugin declares its package version')
  assert.equal(desktopVersion, getPackageVersion())
  assert.equal(desktopVersion, dashboardManifest.version)
})

test('Desktop and backend API contract versions stay aligned', () => {
  assert.equal(getDesktopApiContractVersion(), getBackendApiContractVersion())
})

test('product name is shared and navigation stays short', () => {
  const productName = pluginManifest.match(/^label:\s*(.+)$/m)?.[1]
  assert.equal(productName, 'Persistent Memory (SQLite)')
  assert.match(pluginSource, /name:\s*'Persistent Memory \(SQLite\)'/)
  assert.match(pluginSource, /label:\s*'Memory'/)
  assert.equal(dashboardManifest.label, 'Memory')
})

test('only an explicitly different Desktop API contract is incompatible', () => {
  const isIncompatible = loadApiCompatibility()
  const contractVersion = getDesktopApiContractVersion()

  assert.equal(isIncompatible(contractVersion), false)
  assert.equal(isIncompatible(contractVersion + 1), true)
  assert.equal(isIncompatible(undefined), false, 'legacy backends without a contract version are allowed to try')
  assert.equal(isIncompatible('unknown'), false, 'malformed metadata is not reported as a known incompatibility')
})

test('shows a version warning instead of memory UI for an incompatible API contract', () => {
  const incompatibleVersion = getDesktopApiContractVersion() + 1
  const rendered = collectTree(renderPage('mismatch', incompatibleVersion))
  const text = rendered.text.join('')

  assert.ok(text.includes('バージョンが違います'))
  assert.ok(text.includes(`v${incompatibleVersion}`))
  assert.ok(text.includes('API'), 'mismatch hint explains the incompatible API contract')
  assert.ok(text.includes('再試行'), 'retry uses the real label, not the Reload fallback')
  assert.ok(!rendered.types.includes('MemoryTopHeader'))
  assert.ok(rendered.types.includes('MemorySubBar'), 'profile selector remains available during a version mismatch')
})

test('explains an unavailable backend without showing the memory editor', () => {
  const rendered = collectTree(renderPage('unavailable', null))
  const text = rendered.text.join('')

  assert.ok(text.includes('バックエンド API に接続できませんでした'))
  assert.ok(text.includes('再試行'))
  assert.ok(!rendered.types.includes('MemoryTopHeader'))
  assert.ok(rendered.types.includes('MemorySubBar'))
})

test('renders memory UI when the API contract is compatible despite a package version difference', () => {
  const rendered = collectTree(renderPage('compatible', getDesktopApiContractVersion(), '9.8.7'))

  assert.ok(rendered.types.includes('MemoryTopHeader'))
  assert.ok(!rendered.text.join('').includes('バージョンが違います'))
})
