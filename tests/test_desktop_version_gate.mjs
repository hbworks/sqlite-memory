import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const pluginSource = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const pluginManifest = readFileSync(new URL('../plugin.yaml', import.meta.url), 'utf8')
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

function loadVersionCompatibility() {
  const start = pluginSource.indexOf('function isPluginVersionCompatible(profileVersion) {')
  const end = pluginSource.indexOf('\n}', start)
  assert.notEqual(start, -1, 'plugin version compatibility check exists')
  assert.notEqual(end, -1, 'plugin version compatibility check ends')
  const context = vm.createContext({ DESKTOP_PLUGIN_VERSION: getPackageVersion() })
  return vm.runInContext(`${pluginSource.slice(start, end + 2)}\nisPluginVersionCompatible`, context)
}

function loadTranslations() {
  const localeStart = pluginSource.indexOf('const getLocale = () => {')
  const localeEnd = pluginSource.indexOf('\n}\n\nfunction isPluginVersionCompatible', localeStart)
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

function renderPage(versionStatus, pluginVersion) {
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
    DESKTOP_PLUGIN_VERSION: getPackageVersion(),
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

test('only an exact profile plugin version is compatible', () => {
  const isCompatible = loadVersionCompatibility()

  assert.equal(isCompatible(getPackageVersion()), true)
  assert.equal(isCompatible('9.8.7'), false)
  assert.equal(isCompatible(undefined), false)
})

test('shows a version warning instead of memory UI for a mismatched profile', () => {
  const rendered = collectTree(renderPage('mismatch', '9.8.7'))
  const text = rendered.text.join('')

  assert.ok(text.includes('バージョンが違います'))
  assert.ok(text.includes('9.8.7'))
  assert.ok(text.includes('プロファイル配下に古いプラグイン'), 'mismatch hint explains the stale profile copy')
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

test('renders memory UI when the selected profile version matches', () => {
  const rendered = collectTree(renderPage('compatible', getPackageVersion()))

  assert.ok(rendered.types.includes('MemoryTopHeader'))
  assert.ok(!rendered.text.join('').includes('バージョンが違います'))
})
