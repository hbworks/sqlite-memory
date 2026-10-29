/**
 * Persistent Memory (SQLite) — Hermes Desktop UI
 *
 * Implements a clean, 2-column master-detail layout consistent with
 * Hermes official Settings, Toolsets, and Messaging pages.
 */

import {
  Codicon,
  host,
  useValue,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// =============================================================================
// 1. 基盤・定義層 (Foundations & Utilities)
// =============================================================================

let _rest = null
// Keep this package version aligned with plugin.yaml and dashboard/manifest.json.
const DESKTOP_PLUGIN_VERSION = '1.0.0'
const MEMORY_PAGE_SIZE = 100

const getLocale = () => {
  if (typeof document !== 'undefined') {
    const docLang = document.documentElement?.lang || document.documentElement?.getAttribute('lang')
    if (docLang && docLang.toLowerCase().startsWith('ja')) return 'ja'
  }
  if (typeof navigator !== 'undefined') {
    const langs = navigator.languages || [navigator.language || navigator.userLanguage || '']
    if (langs.some((l) => l && l.toLowerCase().startsWith('ja'))) return 'ja'
  }
  return 'en'
}

function isPluginVersionCompatible(profileVersion) {
  return typeof profileVersion === 'string' && profileVersion === DESKTOP_PLUGIN_VERSION
}

/**
 * バックエンド API 通信レイヤー
 * 1. Electron IPC (window.hermesDesktop.api)
 * 2. SDK ctx.rest
 * 3. Browser fetch
 */
async function api(path, options = {}) {
  const cleanPath = path.startsWith('/') ? path : '/' + path
  // The query selects the data profile; the IPC field selects its backend.
  // Keep them aligned instead of routing every profile through the primary.
  const profile = new URLSearchParams(cleanPath.split('?')[1] || '').get('profile')
  const profileScope = profile ? { profile } : {}

  // 1. Electron 環境の正規 IPC ブリッジ（window.hermesDesktop.api）を最優先
  if (typeof window !== 'undefined' && window.hermesDesktop?.api) {
    let body = options.body
    if (typeof body === 'string') {
      try { body = JSON.parse(body) } catch (_) {}
    }
    const method = options.method || 'GET'

    // The host mounts this plugin name only. sqlite_memory is a config key, not an API path.
    return await window.hermesDesktop.api({
      path: `/api/plugins/sqlite-memory${cleanPath}`,
      method,
      body,
      ...profileScope
    })
  }

  // 2. SDK の ctx.rest が利用可能な場合
  if (_rest) {
    try {
      const relPath = cleanPath.startsWith('/') ? cleanPath.slice(1) : cleanPath
      return await _rest(relPath, options)
    } catch (e) {
      console.warn('ctx.rest failed, falling back to fetch:', e)
    }
  }

  // 3. ブラウザ / Web UI 環境での fetch フォールバック
  const token = typeof window !== 'undefined' ? (window.__HERMES_SESSION_TOKEN__ || '') : ''
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...(options.headers || {})
  }
  const fullUrl = cleanPath.startsWith('/api') ? cleanPath : `/api/plugins/sqlite-memory${cleanPath}`
  const requestOptions = { ...options, headers }
  if (requestOptions.body && typeof requestOptions.body !== 'string') {
    try {
      requestOptions.body = JSON.stringify(requestOptions.body)
    } catch (_) {}
  }
  const res = await fetch(fullUrl, requestOptions)
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`)
  return await res.json()
}

const t = {
  get preference() { return getLocale() === 'ja' ? '設定・好み' : 'Preference' },
  get project() { return getLocale() === 'ja' ? 'プロジェクト' : 'Project' },
  get rule() { return getLocale() === 'ja' ? 'ルール' : 'Rule' },
  get general() { return getLocale() === 'ja' ? '一般' : 'General' },
  get all() { return getLocale() === 'ja' ? 'すべて' : 'All' },
  get searchPlaceholder() { return getLocale() === 'ja' ? '「Rust」などを検索' : 'Search "Rust", etc...' },
  get searchHint() { return getLocale() === 'ja' ? '半角・全角スペース区切りでAND検索' : 'Space-separated terms use AND matching' },
  get addMemory() { return getLocale() === 'ja' ? '記憶を追加' : 'Add Memory' },
  get category() { return getLocale() === 'ja' ? 'カテゴリ' : 'Category' },
  totalCount: (total) => getLocale() === 'ja' ? `合計: ${total} 件` : `Total: ${total}`,
  get refresh() { return getLocale() === 'ja' ? '更新' : 'Refresh' },
  get loading() { return getLocale() === 'ja' ? '読み込み中...' : 'Loading...' },
  get versionChecking() { return getLocale() === 'ja' ? 'バージョンを確認中...' : 'Checking plugin version...' },
  get versionUnavailable() { return getLocale() === 'ja' ? 'バージョンを確認できませんでした' : 'Could not verify plugin version' },
  get unknownVersion() { return getLocale() === 'ja' ? '不明' : 'unknown' },
  versionMismatch: (desktopVersion, profileVersion) => getLocale() === 'ja'
    ? `バージョンが違います（Desktop プラグイン: ${desktopVersion}、プロファイル: ${profileVersion}）`
    : `Plugin version mismatch (Desktop plugin: ${desktopVersion}, profile: ${profileVersion})`,
  get versionMismatchHint() {
    return getLocale() === 'ja'
      ? 'プロファイル配下に古いプラグインが存在するか、バックエンドが古い可能性があります。~/.hermes/profiles/<profile>/plugins/ のコピーを確認してください。'
      : 'The profile may have an outdated plugin copy or backend. Check ~/.hermes/profiles/<profile>/plugins/.'
  },
  get versionUnavailableHint() {
    return getLocale() === 'ja'
      ? 'バックエンド API に接続できませんでした。Hermes が起動しているか確認してください。'
      : 'Could not connect to the backend API. Please make sure Hermes is running.'
  },
  get retry() { return getLocale() === 'ja' ? '再試行' : 'Retry' },
  get noMatches() { return getLocale() === 'ja' ? '一致なし' : 'No matches found' },
  get noMemories() { return getLocale() === 'ja' ? '記憶がありません' : 'No memories found' },
  get tryOtherKeyword() { return getLocale() === 'ja' ? '別のキーワードをお試しください' : 'Try a different keyword' },
  get emptyHint() { return getLocale() === 'ja' ? '「+ 記憶を追加」から登録できます' : 'Add one using "+ Add Memory"' },
  get backToList() { return getLocale() === 'ja' ? '一覧に戻る' : 'Back to list' },
  get edit() { return getLocale() === 'ja' ? '編集' : 'Edit' },
  get delete() { return getLocale() === 'ja' ? '削除' : 'Delete' },
  get created() { return getLocale() === 'ja' ? '作成: ' : 'Created: ' },
  get updated() { return getLocale() === 'ja' ? '更新: ' : 'Updated: ' },
  get source() { return getLocale() === 'ja' ? '登録元: ' : 'Source: ' },
  get contentHeading() { return getLocale() === 'ja' ? '記憶の内容' : 'Memory Content' },
  get selectPrompt() { return getLocale() === 'ja' ? '左の一覧から記憶を選択してください' : 'Select a memory from the left list' },
  editModalTitle: (id) => getLocale() === 'ja' ? `Memory #${id} を編集` : `Edit Memory #${id}`,
  get addModalTitle() { return getLocale() === 'ja' ? '新しい記憶を追加' : 'Add New Memory' },
  get contentLabel() { return getLocale() === 'ja' ? '記憶内容' : 'Memory Content' },
  get placeholderExample() { return getLocale() === 'ja' ? '例: このプロジェクトでは strict モードを有効にする' : 'e.g., Always use strict mode for this project' },
  get cancel() { return getLocale() === 'ja' ? 'キャンセル' : 'Cancel' },
  get saving() { return getLocale() === 'ja' ? '保存中...' : 'Saving...' },
  get save() { return getLocale() === 'ja' ? '保存する' : 'Save' },
  deleteConfirm: (id) => getLocale() === 'ja' ? `Memory #${id} を削除しますか？` : `Delete Memory #${id}?`,
  saveError: (m) => getLocale() === 'ja' ? `保存エラー: ${m}` : `Save error: ${m}`,
  deleteError: (m) => getLocale() === 'ja' ? `削除エラー: ${m}` : `Delete error: ${m}`,
  get defaultProfileLabel() { return getLocale() === 'ja' ? 'デフォルト (~/.hermes)' : 'Default (~/.hermes)' },
  get profileLabel() { return getLocale() === 'ja' ? 'プロファイル:' : 'Profile:' },
  get storageLabel() { return getLocale() === 'ja' ? '保存先:' : 'Storage:' },
  get storageUnknown() { return getLocale() === 'ja' ? '未確認' : 'Unknown' },
  get listLoadError() { return getLocale() === 'ja' ? '記憶一覧を読み込めませんでした' : 'Could not load memories' },
  get retry() { return getLocale() === 'ja' ? '再試行' : 'Retry' },
  get previousPage() { return getLocale() === 'ja' ? '前へ' : 'Previous' },
  get nextPage() { return getLocale() === 'ja' ? '次へ' : 'Next' },
  pageStatus: (page, pages, total) => getLocale() === 'ja'
    ? `${page} / ${pages} ページ（${total} 件）`
    : `Page ${page} of ${pages} (${total} items)`
}

/**
 * カテゴリ別スタイル設定
 */
const getCategoryStyles = () => ({
  preference: { label: t.preference, dot: 'var(--ui-accent)', badgeBg: 'color-mix(in srgb, var(--ui-accent) 12%, var(--card, var(--dt-card)))', badgeColor: 'var(--foreground, var(--dt-foreground))' },
  project:    { label: t.project,    dot: 'var(--ui-green)', badgeBg: 'color-mix(in srgb, var(--ui-green) 12%, var(--card, var(--dt-card)))', badgeColor: 'var(--foreground, var(--dt-foreground))' },
  rule:       { label: t.rule,       dot: 'var(--ui-warm)', badgeBg: 'color-mix(in srgb, var(--ui-warm) 12%, var(--card, var(--dt-card)))', badgeColor: 'var(--foreground, var(--dt-foreground))' },
  general:    { label: t.general,    dot: 'var(--ui-purple)', badgeBg: 'color-mix(in srgb, var(--ui-purple) 12%, var(--card, var(--dt-card)))', badgeColor: 'var(--foreground, var(--dt-foreground))' }
})

/**
 * カテゴリバッジの描画ヘルパー
 */
const renderBadge = (catKey) => {
  const styles = getCategoryStyles()
  const cat = styles[catKey] || styles.general
  return jsx('span', {
    style: {
      fontSize: '11px',
      fontWeight: 600,
      padding: '2px 8px',
      borderRadius: '9999px',
      backgroundColor: cat.badgeBg,
      color: cat.badgeColor
    },
    children: cat.label
  })
}

/**
 * 日時文字列の安全なパース
 */
const parseUtcDate = (val) => {
  if (!val) return null
  if (val instanceof Date) return isNaN(val.getTime()) ? null : val
  if (typeof val === 'number') {
    const d = new Date(val > 1e11 ? val : val * 1000)
    return isNaN(d.getTime()) ? null : d
  }
  if (typeof val === 'string') {
    const clean = val.trim()
    if (!clean) return null
    const iso = clean.includes('T')
      ? (clean.endsWith('Z') || clean.includes('+') ? clean : clean + 'Z')
      : clean.replace(' ', 'T') + 'Z'
    const d = new Date(iso)
    if (!isNaN(d.getTime())) return d
    const fallback = new Date(clean)
    return isNaN(fallback.getTime()) ? null : fallback
  }
  return null
}

const formatLocalTime = (val) => {
  const d = parseUtcDate(val)
  if (!d) return val || 'Unknown'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const formatLocalDateShort = (val) => {
  const d = parseUtcDate(val)
  if (!d) return (val || '').split(' ')[0].slice(5)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * 共通スタイル定義
 */
const S = {
  page: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    backgroundColor: 'var(--card, var(--dt-card))',
    color: 'var(--foreground, var(--dt-foreground))',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    fontSize: '13px',
    overflow: 'hidden',
    boxSizing: 'border-box'
  },
  flexRow: { display: 'flex', alignItems: 'center' },
  flexCol: { display: 'flex', flexDirection: 'column' },
  flexBetween: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  ellipsis: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  btnPrimary: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    padding: '5px 12px',
    borderRadius: '6px',
    fontSize: '12px',
    fontWeight: 500,
    backgroundColor: 'var(--primary, var(--dt-primary))',
    color: 'var(--primary-foreground, var(--dt-primary-foreground))',
    border: 'none',
    cursor: 'pointer',
    flexShrink: 0,
    whiteSpace: 'nowrap'
  },
  btnAction: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    padding: '4px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: 500,
    border: '1px solid var(--border, var(--dt-border))',
    backgroundColor: 'var(--card, var(--dt-card))',
    color: 'var(--foreground, var(--dt-foreground))',
    cursor: 'pointer'
  },
  input: {
    width: '100%',
    backgroundColor: 'var(--card, var(--dt-card))',
    border: '1px solid var(--ui-stroke-primary)',
    borderRadius: '6px',
    padding: '6px 10px',
    fontSize: '12px',
    color: 'var(--foreground, var(--dt-foreground))',
    outline: 'none',
    boxSizing: 'border-box'
  }
}

// =============================================================================
// 2. ロジック層 (Custom Hook)
// =============================================================================

/**
 * 記憶管理の状態・CRUD・データ取得を統括するカスタムフック
 */
function useMemoryState() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('all')

  const focusedProfileAtom = host.state?.focusedSessionProfile || host.state?.profile
  const hostProfileName = useValue(focusedProfileAtom)

  const [selectedProfile, setSelectedProfile] = useState(() => hostProfileName || 'default')
  const [availableProfiles, setAvailableProfiles] = useState(() => {
    const initial = ['default']
    if (hostProfileName && !initial.includes(hostProfileName)) {
      initial.unshift(hostProfileName)
    }
    return initial
  })
  const [selectedId, setSelectedId] = useState(null)
  const [total, setTotal] = useState(0)
  const [listTotal, setListTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [memoryLoadError, setMemoryLoadError] = useState(false)
  const [categories, setCategories] = useState({})
  const [dbPath, setDbPath] = useState('')
  const [pluginVersion, setPluginVersion] = useState(null)
  const [versionStatus, setVersionStatus] = useState('checking')
  const [showAddModal, setShowAddModal] = useState(false)
  const [editId, setEditId] = useState(null)
  const [formCategory, setFormCategory] = useState('preference')
  const [formContent, setFormContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [isCompact, setIsCompact] = useState(false)
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)

  const containerRef = useRef(null)
  const profileLoadRef = useRef(0)
  const memoryLoadRef = useRef(0)
  const isManuallySelected = useRef(false)
  const prevHostProfileRef = useRef(hostProfileName)

  const clearProfileView = () => {
    setItems([])
    setSelectedId(null)
    setListTotal(0)
    setOffset(0)
    setMemoryLoadError(false)
    setTotal(0)
    setCategories({})
    setDbPath('')
    setPluginVersion(null)
    setVersionStatus('checking')
    setLoading(true)
    setMobileDetailOpen(false)
    setShowAddModal(false)
    setEditId(null)
    setFormContent('')
  }

  // プロファイル変更
  const handleSelectProfile = (newProfile) => {
    if (!newProfile || newProfile === selectedProfile) return
    isManuallySelected.current = true
    profileLoadRef.current += 1
    memoryLoadRef.current += 1
    clearProfileView()
    setSelectedProfile(newProfile)
  }

  // ホストで選択されたプロファイルの自動追従
  useEffect(() => {
    if (hostProfileName) {
      if (hostProfileName !== prevHostProfileRef.current) {
        prevHostProfileRef.current = hostProfileName
        isManuallySelected.current = false
        profileLoadRef.current += 1
        memoryLoadRef.current += 1
        clearProfileView()
        setSelectedProfile(hostProfileName)
      } else if (!isManuallySelected.current && selectedProfile !== hostProfileName) {
        profileLoadRef.current += 1
        memoryLoadRef.current += 1
        clearProfileView()
        setSelectedProfile(hostProfileName)
      }
    }
  }, [hostProfileName, selectedProfile])

  // コンテナ幅の監視（レスポンシブ切り替え）
  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setIsCompact(e.contentRect.width < 580))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 統計情報の取得
  const loadStats = useCallback(async (profile, requestId) => {
    try {
      const data = await api(`/stats?profile=${encodeURIComponent(profile)}`)
      if (requestId !== profileLoadRef.current) return null
      setTotal(data.total_memories || 0)
      setCategories(data.categories || {})
      setDbPath(data.db_path || '')
      if (Array.isArray(data.available_profiles) && data.available_profiles.length > 0) {
        setAvailableProfiles(data.available_profiles)
      }
      return data
    } catch (e) {
      console.warn('Error loading memory stats:', e)
      return null
    }
  }, [])

  // メモリ一覧の取得
  const fetchMemories = useCallback(async (profile, q, cat, currentProfileId, requestedOffset = 0) => {
    const reqId = ++memoryLoadRef.current
    setLoading(true)
    setMemoryLoadError(false)
    try {
      const params = new URLSearchParams({
        profile,
        limit: String(MEMORY_PAGE_SIZE),
        offset: String(requestedOffset)
      })
      if (q.trim()) params.append('query', q.trim())
      if (cat && cat !== 'all') params.append('category', cat)

      const data = await api(`/memories?${params.toString()}`)
      if (reqId !== memoryLoadRef.current || currentProfileId !== profileLoadRef.current) return
      const loaded = Array.isArray(data.items) ? data.items : []
      const responseTotal = Number(data.total)
      const totalCount = Number.isFinite(responseTotal) && responseTotal >= 0
        ? responseTotal
        : loaded.length
      const lastValidOffset = Math.max(
        0,
        Math.floor((totalCount - 1) / MEMORY_PAGE_SIZE) * MEMORY_PAGE_SIZE
      )
      if (requestedOffset > lastValidOffset) {
        return fetchMemories(profile, q, cat, currentProfileId, lastValidOffset)
      }
      setItems(loaded)
      setListTotal(totalCount)
      setOffset(requestedOffset)
      setSelectedId((prev) => (prev && loaded.some((i) => i.id === prev) ? prev : (loaded[0]?.id ?? null)))
    } catch (e) {
      console.error('Error loading memories:', e)
      if (reqId === memoryLoadRef.current && currentProfileId === profileLoadRef.current) {
        setItems([])
        setSelectedId(null)
        setListTotal(0)
        setOffset(0)
        setMemoryLoadError(true)
      }
    } finally {
      if (reqId === memoryLoadRef.current && currentProfileId === profileLoadRef.current) {
        setLoading(false)
      }
    }
  }, [])

  // プロファイルのロード & バージョン検証
  const loadCurrentProfile = useCallback(async () => {
    const requestId = ++profileLoadRef.current
    memoryLoadRef.current += 1
    clearProfileView()
    const stats = await loadStats(selectedProfile, requestId)
    if (requestId !== profileLoadRef.current) return
    if (!stats) {
      setVersionStatus('unavailable')
      setItems([])
      setSelectedId(null)
      setListTotal(0)
      setMemoryLoadError(false)
      setTotal(0)
      setCategories({})
      setDbPath('')
      setLoading(false)
      return
    }

    const profileVersion = typeof stats.plugin_version === 'string' ? stats.plugin_version : null
    setPluginVersion(profileVersion)
    if (isPluginVersionCompatible(profileVersion)) {
      setVersionStatus('compatible')
      await fetchMemories(selectedProfile, query, selectedCategory, requestId)
      return
    }

    setVersionStatus('mismatch')
    setItems([])
    setSelectedId(null)
    setListTotal(0)
    setMemoryLoadError(false)
    setLoading(false)
  }, [selectedProfile, loadStats, fetchMemories, query, selectedCategory])

  // プロファイル切り替え時のみプロファイル全体を再読み込み
  useEffect(() => {
    loadCurrentProfile()
  }, [selectedProfile])

  // 検索クエリやカテゴリ変更時は、互換性検証済みのまま一覧のみをデバウンス更新
  const isInitialMount = useRef(true)
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false
      return
    }
    if (versionStatus !== 'compatible') return

    const timer = setTimeout(() => {
      fetchMemories(selectedProfile, query, selectedCategory, profileLoadRef.current, 0)
    }, 200)

    return () => clearTimeout(timer)
  }, [query, selectedCategory, versionStatus, selectedProfile, fetchMemories])

  const handleQueryChange = (value) => {
    setQuery(value)
    setOffset(0)
  }

  const handleCategoryChange = (value) => {
    setSelectedCategory(value)
    setOffset(0)
  }

  const loadPage = (requestedOffset) => {
    const lastPageOffset = Math.max(0, Math.floor((listTotal - 1) / MEMORY_PAGE_SIZE) * MEMORY_PAGE_SIZE)
    const nextOffset = Math.max(0, Math.min(requestedOffset, lastPageOffset))
    if (nextOffset === offset) return Promise.resolve()
    setOffset(nextOffset)
    return fetchMemories(
      selectedProfile,
      query,
      selectedCategory,
      profileLoadRef.current,
      nextOffset
    )
  }

  const goToPreviousPage = () => loadPage(offset - MEMORY_PAGE_SIZE)
  const goToNextPage = () => loadPage(offset + MEMORY_PAGE_SIZE)
  const retryMemories = () => fetchMemories(
    selectedProfile,
    query,
    selectedCategory,
    profileLoadRef.current,
    offset
  )

  const activeMemory = useMemo(() => items.find((i) => i.id === selectedId) || null, [items, selectedId])

  const handleSelectItem = (id) => {
    setSelectedId(id)
    if (isCompact) setMobileDetailOpen(true)
  }

  // 保存処理 (新規 / 更新)
  const handleSave = async (e) => {
    e.preventDefault()
    if (!formContent.trim()) return
    const targetProfile = selectedProfile
    const requestId = profileLoadRef.current
    setSaving(true)
    try {
      const method = editId ? 'PUT' : 'POST'
      const endpoint = editId ? `/memories/${editId}` : '/memories'
      await api(`${endpoint}?profile=${encodeURIComponent(targetProfile)}`, {
        method,
        body: { content: formContent.trim(), category: formCategory }
      })
      setShowAddModal(false)
      setEditId(null)
      setFormContent('')
      if (requestId !== profileLoadRef.current) return
      await loadStats(targetProfile, requestId)
      if (requestId !== profileLoadRef.current) return
      await fetchMemories(targetProfile, query, selectedCategory, requestId)
    } catch (err) {
      console.error('Error saving memory:', err)
      alert(t.saveError(err.message))
    } finally {
      setSaving(false)
    }
  }

  // 削除処理
  const handleDelete = async (id) => {
    if (!confirm(t.deleteConfirm(id))) return
    const targetProfile = selectedProfile
    const requestId = profileLoadRef.current
    try {
      await api(`/memories/${id}?profile=${encodeURIComponent(targetProfile)}`, { method: 'DELETE' })
      if (selectedId === id) setSelectedId(null)
      if (isCompact) setMobileDetailOpen(false)
      if (requestId !== profileLoadRef.current) return
      await loadStats(targetProfile, requestId)
      if (requestId !== profileLoadRef.current) return
      await fetchMemories(targetProfile, query, selectedCategory, requestId)
    } catch (err) {
      console.error('Error deleting memory:', err)
      alert(t.deleteError(err.message))
    }
  }

  // モーダルオープン
  const handleOpenModal = (item = null) => {
    setEditId(item?.id ?? null)
    setFormCategory(item?.category || 'preference')
    setFormContent(item?.content || '')
    setShowAddModal(true)
  }

  const handleCloseModal = () => setShowAddModal(false)

  const reloadAll = () => loadCurrentProfile()

  const categoryPills = [
    { key: 'all', label: t.all, count: total },
    { key: 'preference', label: t.preference, count: categories['preference'] || 0 },
    { key: 'project', label: t.project, count: categories['project'] || 0 },
    { key: 'rule', label: t.rule, count: categories['rule'] || 0 },
    { key: 'general', label: t.general, count: categories['general'] || 0 }
  ]

  return {
    items,
    loading,
    query,
    setQuery: handleQueryChange,
    selectedCategory,
    setSelectedCategory: handleCategoryChange,
    selectedProfile,
    handleSelectProfile,
    availableProfiles,
    selectedId,
    activeMemory,
    total,
    listTotal,
    offset,
    pageSize: MEMORY_PAGE_SIZE,
    memoryLoadError,
    goToPreviousPage,
    goToNextPage,
    retryMemories,
    dbPath,
    pluginVersion,
    versionStatus,
    showAddModal,
    editId,
    formCategory,
    setFormCategory,
    formContent,
    setFormContent,
    saving,
    isCompact,
    mobileDetailOpen,
    setMobileDetailOpen,
    containerRef,
    handleSelectItem,
    handleSave,
    handleDelete,
    handleOpenModal,
    handleCloseModal,
    reloadAll,
    categoryPills
  }
}

// =============================================================================
// 3. UI部品層 (Presentational Components)
// =============================================================================

/**
 * トップヘッダー（検索バー、+新規作成ボタン、カテゴリピル）
 */
function MemoryTopHeader({
  query,
  setQuery,
  categoryPills,
  selectedCategory,
  onSelectCategory,
  onOpenAddModal
}) {
  return jsxs('div', {
    style: { padding: '14px 18px 10px 18px', borderBottom: '1px solid var(--ui-stroke-secondary)', ...S.flexCol, gap: '10px', flexShrink: 0 },
    children: [
      jsxs('div', {
        style: { ...S.flexBetween, gap: '12px' },
        children: [
          // 検索バー
          jsxs('div', {
            style: { display: 'flex', flexDirection: 'column', flex: 1, maxWidth: '320px', minWidth: 0 },
            children: [
              jsxs('div', {
                style: { position: 'relative', display: 'flex', alignItems: 'center' },
                children: [
                  jsx('span', {
                    style: { position: 'absolute', left: '8px', color: 'var(--ui-text-quaternary)', display: 'flex', pointerEvents: 'none' },
                    children: jsx(Codicon, { name: 'search', size: '0.9rem' })
                  }),
                  jsx('input', {
                    type: 'text',
                    placeholder: t.searchPlaceholder,
                    value: query,
                    onChange: (e) => setQuery(e.target.value),
                    style: { width: '100%', border: 'none', background: 'transparent', paddingLeft: '28px', paddingRight: '8px', paddingTop: '4px', paddingBottom: '4px', fontSize: '13px', color: 'var(--foreground, var(--dt-foreground))', outline: 'none' }
                  })
                ]
              }),
              jsx('span', {
                style: { paddingLeft: '8px', fontSize: '10px', lineHeight: 1.25, color: 'var(--ui-text-quaternary)' },
                children: t.searchHint
              })
            ]
          }),
          jsx('button', {
            onClick: () => onOpenAddModal(),
            style: S.btnPrimary,
            children: [jsx(Codicon, { name: 'add', size: '0.8rem' }), ` ${t.addMemory}`]
          })
        ]
      }),

      // カテゴリピル
      jsxs('div', {
        style: { ...S.flexCol, gap: '6px' },
        children: [
          jsx('span', { style: { fontSize: '11px', color: 'var(--ui-text-secondary)', fontWeight: 500 }, children: t.category }),
          jsx('div', {
            style: { display: 'flex', alignItems: 'center', gap: '8px', overflowX: 'auto', whiteSpace: 'nowrap', scrollbarWidth: 'none', paddingBottom: '2px' },
            children: categoryPills.map((pill) => {
              const isSelected = selectedCategory === pill.key
              return jsxs('button', {
                key: pill.key,
                onClick: () => onSelectCategory(pill.key),
                style: {
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '5px',
                  padding: '3px 12px',
                  borderRadius: '9999px',
                  fontSize: '12px',
                  fontWeight: isSelected ? 600 : 400,
                  backgroundColor: isSelected ? 'var(--ui-row-active-background)' : 'transparent',
                  color: isSelected ? 'var(--foreground, var(--dt-foreground))' : 'var(--ui-text-secondary)',
                  border: isSelected ? '1.5px solid var(--ui-accent)' : '1px solid var(--ui-stroke-secondary)',
                  cursor: 'pointer',
                  flexShrink: 0
                },
                children: [
                  jsx('span', { children: pill.label }),
                  pill.count > 0 && jsx('span', { style: { fontSize: '10px', opacity: 0.7, fontVariantNumeric: 'tabular-nums' }, children: pill.count })
                ]
              })
            })
          })
        ]
      })
    ]
  })
}

/**
 * サブ情報バー（プロファイル切り替え、SQLite保存先パス、件数表示、リフレッシュ）
 */
function MemorySubBar({
  selectedProfile,
  onSelectProfile,
  availableProfiles,
  dbPath,
  total,
  onReload
}) {
  return jsxs('div', {
    style: { ...S.flexBetween, padding: '6px 18px', backgroundColor: 'var(--ui-bg-tertiary)', borderBottom: '1px solid var(--ui-stroke-secondary)', fontSize: '11px', color: 'var(--ui-text-secondary)', flexShrink: 0 },
    children: [
      jsxs('div', {
        style: { ...S.flexRow, gap: '8px', minWidth: 0 },
        children: [
          jsx('span', { style: { fontWeight: 500, color: 'var(--ui-text-secondary)' }, children: t.profileLabel }),
          jsx('select', {
            value: selectedProfile,
            onChange: (e) => onSelectProfile(e.target.value),
            style: { padding: '2px 6px', borderRadius: '4px', border: '1px solid var(--ui-stroke-primary)', backgroundColor: 'var(--card, var(--dt-card))', fontSize: '11px', fontWeight: 600, color: 'var(--foreground, var(--dt-foreground))', cursor: 'pointer', outline: 'none' },
            children: availableProfiles.map((p) => jsx('option', { key: p, value: p, children: p === 'default' ? t.defaultProfileLabel : p }, p))
          }),
          jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: '•' }),
          jsx('span', { style: { color: 'var(--ui-text-tertiary)' }, children: t.storageLabel }),
          jsx('span', {
            style: { fontWeight: 500, color: 'var(--foreground, var(--dt-foreground))', ...S.ellipsis, maxWidth: '320px' },
            title: dbPath || t.storageUnknown,
            children: dbPath ? `SQLite (${dbPath})` : t.storageUnknown
          })
        ]
      }),
      jsxs('div', {
        style: { ...S.flexRow, gap: '10px', flexShrink: 0 },
        children: [
          jsx('span', { style: { fontVariantNumeric: 'tabular-nums' }, children: t.totalCount(total) }),
          jsx('button', {
            onClick: onReload,
            style: { display: 'flex', alignItems: 'center', gap: '3px', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, fontSize: '11px' },
            children: [jsx(Codicon, { name: 'refresh', size: '0.75rem' }), ` ${t.refresh}`]
          })
        ]
      })
    ]
  })
}

/**
 * 単一メモリアイテム（左カラム内）
 */
function MemoryListItem({
  item,
  isSelected,
  onSelect
}) {
  const catStyles = getCategoryStyles()
  const cat = catStyles[item.category] || catStyles.general

  return jsxs('div', {
    onClick: onSelect,
    style: {
      padding: '10px 14px',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: '8px',
      backgroundColor: isSelected ? 'var(--ui-row-active-background)' : 'transparent',
      borderLeft: isSelected ? '3px solid var(--ui-accent)' : '3px solid transparent',
      borderBottom: '1px solid var(--ui-stroke-quaternary)'
    },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 },
        children: [
          jsx('span', { style: { width: '7px', height: '7px', borderRadius: '50%', backgroundColor: cat.dot, flexShrink: 0 } }),
          jsxs('div', {
            style: { display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 },
            children: [
              jsx('span', { style: { fontSize: '12px', fontWeight: isSelected ? 600 : 500, color: 'var(--foreground, var(--dt-foreground))', whiteSpace: 'nowrap' }, children: `Memory #${item.id}` }),
              jsx('span', { style: { fontSize: '11px', color: 'var(--ui-text-secondary)', ...S.ellipsis }, children: item.content })
            ]
          })
        ]
      }),
      jsx('span', { style: { fontSize: '9px', color: 'var(--ui-text-quaternary)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }, children: formatLocalDateShort(item.created_at) })
    ]
  })
}

/**
 * 左カラム: メモリ一覧
 */
function MemoryListColumn({
  loading,
  loadError,
  items,
  query,
  selectedId,
  onSelectItem,
  isCompact,
  offset,
  pageSize,
  total,
  onRetry,
  onPreviousPage,
  onNextPage
}) {
  const page = Math.floor(offset / pageSize) + 1
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const previousDisabled = loading || offset <= 0
  const nextDisabled = loading || offset + pageSize >= total

  return jsxs('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      width: isCompact ? '100%' : '210px',
      minWidth: isCompact ? '100%' : '190px',
      maxWidth: isCompact ? '100%' : '230px',
      borderRight: isCompact ? 'none' : '1px solid var(--ui-stroke-secondary)',
      backgroundColor: 'var(--card, var(--dt-card))',
      overflow: 'hidden',
      height: '100%',
      boxSizing: 'border-box'
    },
    children: [
      jsx('div', {
        style: { flex: 1, minHeight: 0, overflowY: 'auto' },
        children: loading ? (
          jsx('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '30px 10px', fontSize: '11px', color: 'var(--ui-text-secondary)' }, children: t.loading })
        ) : loadError ? (
          jsxs('div', {
            style: { padding: '24px 14px', textAlign: 'center', fontSize: '11px', color: 'var(--ui-text-secondary)' },
            children: [
              jsx('div', { style: { fontWeight: 500, color: 'var(--foreground, var(--dt-foreground))', marginBottom: '8px' }, children: t.listLoadError }),
              jsx('button', { type: 'button', onClick: onRetry, style: S.btnAction, children: t.retry })
            ]
          })
        ) : items.length === 0 ? (
          jsxs('div', {
            style: { padding: '24px 14px', textAlign: 'center', fontSize: '11px', color: 'var(--ui-text-secondary)' },
            children: [
              jsx('div', { style: { fontWeight: 500, color: 'var(--foreground, var(--dt-foreground))', marginBottom: '4px' }, children: query ? t.noMatches : t.noMemories }),
              jsx('p', { style: { fontSize: '10px', opacity: 0.75, margin: 0, lineHeight: 1.4 }, children: query ? t.tryOtherKeyword : t.emptyHint })
            ]
          })
        ) : (
          items.map((item) => jsx(MemoryListItem, {
            key: item.id,
            item,
            isSelected: item.id === selectedId,
            onSelect: () => onSelectItem(item.id)
          }))
        )
      }),
      !loading && !loadError && total > pageSize ? jsxs('div', {
        style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px', padding: '8px 10px', borderTop: '1px solid var(--ui-stroke-secondary)', fontSize: '10px', color: 'var(--ui-text-secondary)', flexShrink: 0 },
        children: [
          jsx('button', {
            type: 'button',
            disabled: previousDisabled,
            onClick: onPreviousPage,
            style: { ...S.btnAction, padding: '3px 7px', opacity: previousDisabled ? 0.45 : 1, cursor: previousDisabled ? 'not-allowed' : 'pointer' },
            children: t.previousPage
          }),
          jsx('span', { style: { whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }, children: t.pageStatus(page, pageCount, total) }),
          jsx('button', {
            type: 'button',
            disabled: nextDisabled,
            onClick: onNextPage,
            style: { ...S.btnAction, padding: '3px 7px', opacity: nextDisabled ? 0.45 : 1, cursor: nextDisabled ? 'not-allowed' : 'pointer' },
            children: t.nextPage
          })
        ]
      }) : null
    ]
  })
}

/**
 * 右カラム: メモリ詳細ビュー
 */
function MemoryDetailColumn({
  activeMemory,
  onEdit,
  onDelete,
  isCompact,
  onBackToList
}) {
  return jsx('div', {
    style: { flex: 1, overflowY: 'auto', padding: '20px 24px', backgroundColor: 'var(--card, var(--dt-card))', height: '100%', boxSizing: 'border-box' },
    children: activeMemory ? (
      jsxs('div', {
        style: { maxWidth: '580px', display: 'flex', flexDirection: 'column', gap: '18px' },
        children: [
          isCompact && jsx('button', {
            onClick: onBackToList,
            style: { ...S.btnAction, border: 'none', padding: 0 },
            children: [jsx(Codicon, { name: 'arrow-left', size: '0.85rem' }), ` ${t.backToList}`]
          }),
          jsxs('div', {
            style: { ...S.flexBetween, alignItems: 'flex-start', gap: '12px' },
            children: [
              jsxs('div', {
                style: { display: 'flex', alignItems: 'center', gap: '8px' },
                children: [
                  jsx('h2', { style: { fontSize: '18px', fontWeight: 600, margin: 0, color: 'var(--foreground, var(--dt-foreground))' }, children: `Memory #${activeMemory.id}` }),
                  renderBadge(activeMemory.category)
                ]
              }),
              jsxs('div', {
                style: { display: 'flex', gap: '6px' },
                children: [
                  jsx('button', { onClick: () => onEdit(activeMemory), style: S.btnAction, children: [jsx(Codicon, { name: 'edit', size: '0.8rem' }), ` ${t.edit}`] }),
                  jsx('button', { onClick: () => onDelete(activeMemory.id), style: { ...S.btnAction, color: 'var(--ui-red)' }, children: [jsx(Codicon, { name: 'trash', size: '0.8rem' }), ` ${t.delete}`] })
                ]
              })
            ]
          }),

          // メタ情報バー
          jsxs('div', {
            style: { display: 'flex', gap: '16px', padding: '10px 14px', backgroundColor: 'var(--ui-bg-tertiary)', borderRadius: '6px', fontSize: '11px', color: 'var(--ui-text-secondary)' },
            children: [
              jsxs('div', { children: [t.created, jsx('span', { style: { fontWeight: 500, color: 'var(--foreground, var(--dt-foreground))' }, children: formatLocalTime(activeMemory.created_at) })] }),
              jsxs('div', { children: [t.updated, jsx('span', { style: { fontWeight: 500, color: 'var(--foreground, var(--dt-foreground))' }, children: formatLocalTime(activeMemory.updated_at) })] }),
              jsxs('div', { children: [t.source, jsx('span', { style: { fontWeight: 500, color: 'var(--foreground, var(--dt-foreground))' }, children: activeMemory.source || 'manual' })] })
            ]
          }),

          // 本文
          jsxs('div', {
            style: { ...S.flexCol, gap: '6px' },
            children: [
              jsx('span', { style: { fontSize: '11px', fontWeight: 600, color: 'var(--ui-text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: t.contentHeading }),
              jsx('div', {
                style: { padding: '14px 16px', backgroundColor: 'var(--card, var(--dt-card))', borderRadius: '8px', border: '1px solid var(--ui-stroke-secondary)', fontSize: '13px', lineHeight: 1.6, color: 'var(--foreground, var(--dt-foreground))', whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
                children: activeMemory.content
              })
            ]
          })
        ]
      })
    ) : (
      jsx('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--ui-text-tertiary)', fontSize: '12px' }, children: t.selectPrompt })
    )
  })
}

/**
 * 記憶の追加・編集モーダルダイアログ
 */
function MemoryEditModal({
  isOpen,
  editId,
  formCategory,
  setFormCategory,
  formContent,
  setFormContent,
  saving,
  onSave,
  onClose
}) {
  // Escapeキーで閉じる
  useEffect(() => {
    if (!isOpen) return
    const onKeyDown = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return jsx('div', {
    style: { position: 'fixed', inset: 0, backgroundColor: 'color-mix(in srgb, var(--ui-base) 40%, transparent)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, padding: '16px' },
    children: jsxs('div', {
      style: { width: '100%', maxWidth: '440px', backgroundColor: 'var(--card, var(--dt-card))', borderRadius: '10px', boxShadow: 'var(--shadow-nous)', padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' },
      children: [
        jsxs('div', {
          style: S.flexBetween,
          children: [
            jsx('h3', { style: { fontSize: '15px', fontWeight: 600, margin: 0, color: 'var(--foreground, var(--dt-foreground))' }, children: editId ? t.editModalTitle(editId) : t.addModalTitle }),
            jsx('button', { onClick: onClose, style: { background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ui-text-quaternary)', padding: 0 }, children: jsx(Codicon, { name: 'close', size: '1rem' }) })
          ]
        }),
        jsxs('form', {
          onSubmit: onSave,
          style: { ...S.flexCol, gap: '12px' },
          children: [
            jsxs('div', {
              style: { ...S.flexCol, gap: '4px' },
              children: [
                jsx('label', { style: { fontSize: '11px', fontWeight: 500, color: 'var(--ui-text-secondary)' }, children: t.category }),
                jsx('select', {
                  value: formCategory,
                  onChange: (e) => setFormCategory(e.target.value),
                  style: S.input,
                  children: [
                    jsx('option', { value: 'preference', children: `${t.preference} (preference)` }),
                    jsx('option', { value: 'project', children: `${t.project} (project)` }),
                    jsx('option', { value: 'rule', children: `${t.rule} (rule)` }),
                    jsx('option', { value: 'general', children: `${t.general} (general)` })
                  ]
                })
              ]
            }),
            jsxs('div', {
              style: { ...S.flexCol, gap: '4px' },
              children: [
                jsx('label', { style: { fontSize: '11px', fontWeight: 500, color: 'var(--ui-text-secondary)' }, children: t.contentLabel }),
                jsx('textarea', {
                  rows: 5,
                  required: true,
                  placeholder: t.placeholderExample,
                  value: formContent,
                  onChange: (e) => setFormContent(e.target.value),
                  style: { ...S.input, padding: '10px', lineHeight: 1.5 }
                })
              ]
            }),
            jsxs('div', {
              style: { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px', paddingTop: '6px', borderTop: '1px solid var(--ui-stroke-secondary)' },
              children: [
                jsx('button', { type: 'button', onClick: onClose, style: S.btnAction, children: t.cancel }),
                jsx('button', {
                  type: 'submit',
                  disabled: saving || !formContent.trim(),
                  style: { ...S.btnPrimary, cursor: saving || !formContent.trim() ? 'not-allowed' : 'pointer', opacity: saving || !formContent.trim() ? 0.5 : 1 },
                  children: saving ? t.saving : t.save
                })
              ]
            })
          ]
        })
      ]
    })
  })
}

// =============================================================================
// 4. メインエントリー (Container & Plugin Registration)
// =============================================================================

/**
 * Persistent Memory (SQLite) 管理ページ
 */
function MemoryManagementPage() {
  const state = useMemoryState()

  if (state.versionStatus !== 'compatible') {
    const message = state.versionStatus === 'checking'
      ? t.versionChecking
      : state.versionStatus === 'mismatch'
        ? t.versionMismatch(DESKTOP_PLUGIN_VERSION, state.pluginVersion || t.unknownVersion)
        : t.versionUnavailable

    const hint = state.versionStatus === 'mismatch'
      ? (t.versionMismatchHint || '')
      : state.versionStatus === 'unavailable'
        ? (t.versionUnavailableHint || '')
        : ''

    return jsxs('div', {
      ref: state.containerRef,
      style: S.page,
      children: [
        // Keep the profile selector available so users can switch to a compatible backend.
        jsx(MemorySubBar, {
          selectedProfile: state.selectedProfile,
          onSelectProfile: state.handleSelectProfile,
          availableProfiles: state.availableProfiles,
          dbPath: state.dbPath,
          total: state.total,
          onReload: state.reloadAll
        }),
        jsxs('div', {
          style: { display: 'flex', flex: 1, flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '24px', textAlign: 'center', gap: '8px' },
          children: [
            jsx('div', {
              style: { fontSize: '13px', color: 'var(--foreground, var(--dt-foreground))', fontWeight: 500 },
              children: message
            }),
            hint ? jsx('div', {
              style: { fontSize: '12px', color: 'var(--ui-text-secondary)', maxWidth: '480px', lineHeight: 1.5 },
              children: hint
            }) : null,
            state.versionStatus !== 'checking' ? jsx('button', {
              onClick: state.reloadAll,
              style: { ...S.btnAction, marginTop: '8px' },
              children: [
                typeof Codicon !== 'undefined' ? jsx(Codicon, { name: 'refresh', size: '0.8rem' }) : null,
                ` ${t.retry || t.refresh || 'Reload'}`
              ].filter(Boolean)
            }) : null
          ]
        })
      ]
    })
  }

  return jsxs('div', {
    ref: state.containerRef,
    style: S.page,
    children: [
      // 1. トップヘッダー（検索 & カテゴリピル）
      jsx(MemoryTopHeader, {
        query: state.query,
        setQuery: state.setQuery,
        categoryPills: state.categoryPills,
        selectedCategory: state.selectedCategory,
        onSelectCategory: state.setSelectedCategory,
        onOpenAddModal: state.handleOpenModal
      }),

      // 2. サブ情報バー（プロファイル & DBパス）
      jsx(MemorySubBar, {
        selectedProfile: state.selectedProfile,
        onSelectProfile: state.handleSelectProfile,
        availableProfiles: state.availableProfiles,
        dbPath: state.dbPath,
        total: state.total,
        onReload: state.reloadAll
      }),

      // 3. メイン2カラム
      jsxs('div', {
        style: { display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' },
        children: [
          // 左カラム（リスト）
          (!state.isCompact || !state.mobileDetailOpen) && jsx(MemoryListColumn, {
            loading: state.loading,
            loadError: state.memoryLoadError,
            items: state.items,
            query: state.query,
            selectedId: state.selectedId,
            onSelectItem: state.handleSelectItem,
            isCompact: state.isCompact,
            offset: state.offset,
            pageSize: state.pageSize,
            total: state.listTotal,
            onRetry: state.retryMemories,
            onPreviousPage: state.goToPreviousPage,
            onNextPage: state.goToNextPage
          }),

          // 右カラム（詳細ビュー）
          (!state.isCompact || (state.mobileDetailOpen && state.activeMemory)) && jsx(MemoryDetailColumn, {
            activeMemory: state.activeMemory,
            onEdit: state.handleOpenModal,
            onDelete: state.handleDelete,
            isCompact: state.isCompact,
            onBackToList: () => state.setMobileDetailOpen(false)
          })
        ]
      }),

      // 4. 追加・編集モーダル
      jsx(MemoryEditModal, {
        isOpen: state.showAddModal,
        editId: state.editId,
        formCategory: state.formCategory,
        setFormCategory: state.setFormCategory,
        formContent: state.formContent,
        setFormContent: state.setFormContent,
        saving: state.saving,
        onSave: state.handleSave,
        onClose: state.handleCloseModal
      })
    ]
  })
}

export default {
  id: 'sqlite-memory',
  name: 'Persistent Memory (SQLite)',
  description: 'SQLite-backed persistent long-term memory browser and manager.',
  defaultEnabled: true,
  register(ctx) {
    _rest = ctx.rest
    return ctx.registerMany([
      {
        id: 'memory-page',
        area: ROUTES_AREA,
        data: { path: '/memory' },
        render: () => jsx(MemoryManagementPage, {})
      },
      {
        id: 'memory-nav',
        area: SIDEBAR_NAV_AREA,
        order: 45,
        data: { codicon: 'bookmark', label: 'Memory', path: '/memory' }
      }
    ])
  }
}
