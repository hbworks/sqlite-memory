/**
 * SQLite Persistent Memory — Official Hermes Desktop UI Style
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

let _rest = null

async function api(path, options = {}) {
  const cleanPath = path.startsWith('/') ? path : '/' + path

  // 1. Electron 環境の正規 IPC ブリッジ（window.hermesDesktop.api）を最優先
  if (typeof window !== 'undefined' && window.hermesDesktop?.api) {
    let body = options.body
    if (typeof body === 'string') {
      try { body = JSON.parse(body) } catch (_) {}
    }
    const method = options.method || 'GET'

    // まず /api/plugins/sqlite-memory を試行
    try {
      return await window.hermesDesktop.api({
        path: `/api/plugins/sqlite-memory${cleanPath}`,
        method,
        body
      })
    } catch (err1) {
      // 404等の場合は互換エンドポイント /api/plugins/sqlite_memory を試行
      try {
        return await window.hermesDesktop.api({
          path: `/api/plugins/sqlite_memory${cleanPath}`,
          method,
          body
        })
      } catch (err2) {
        throw err1
      }
    }
  }

  // 2. SDK の ctx.rest が利用可能な場合
  if (_rest) {
    try {
      const relPath = cleanPath.startsWith('/') ? cleanPath.slice(1) : cleanPath
      const [route, queryStr] = relPath.split('?')
      const params = {}
      if (queryStr) new URLSearchParams(queryStr).forEach((v, k) => { params[k] = v })
      return await _rest(relPath, { ...options, params: { ...(options.params || {}), ...params } })
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
  const res = await fetch(fullUrl, { ...options, headers })
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`)
  return await res.json()
}

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

const I18N = {
  ja: {
    preference: '設定・好み',
    project: 'プロジェクト',
    rule: 'ルール',
    general: '一般',
    all: 'すべて',
    searchPlaceholder: '「Rust」などを検索',
    addMemory: '記憶を追加',
    category: 'カテゴリ',
    totalCount: (t) => `合計: ${t} 件`,
    refresh: '更新',
    loading: '読み込み中...',
    noMatches: '一致なし',
    noMemories: '記憶がありません',
    tryOtherKeyword: '別のキーワードをお試しください',
    emptyHint: '「+ 記憶を追加」から登録できます',
    backToList: '一覧に戻る',
    edit: '編集',
    delete: '削除',
    created: '作成: ',
    updated: '更新: ',
    source: '登録元: ',
    contentHeading: '記憶の内容',
    selectPrompt: '左の一覧から記憶を選択してください',
    editModalTitle: (id) => `Memory #${id} を編集`,
    addModalTitle: '新しい記憶を追加',
    contentLabel: '記憶内容',
    placeholderExample: '例: このプロジェクトでは strict モードを有効にする',
    cancel: 'キャンセル',
    saving: '保存中...',
    save: '保存する',
    deleteConfirm: (id) => `Memory #${id} を削除しますか？`,
    saveError: (m) => `保存エラー: ${m}`,
    deleteError: (m) => `削除エラー: ${m}`,
    defaultProfileLabel: 'デフォルト (~/.hermes)',
    profileLabel: 'プロファイル:',
    storageLabel: '保存先:'
  },
  en: {
    preference: 'Preference',
    project: 'Project',
    rule: 'Rule',
    general: 'General',
    all: 'All',
    searchPlaceholder: 'Search "Rust", etc...',
    addMemory: 'Add Memory',
    category: 'Category',
    totalCount: (t) => `Total: ${t}`,
    refresh: 'Refresh',
    loading: 'Loading...',
    noMatches: 'No matches found',
    noMemories: 'No memories found',
    tryOtherKeyword: 'Try a different keyword',
    emptyHint: 'Add one using "+ Add Memory"',
    backToList: 'Back to list',
    edit: 'Edit',
    delete: 'Delete',
    created: 'Created: ',
    updated: 'Updated: ',
    source: 'Source: ',
    contentHeading: 'Memory Content',
    selectPrompt: 'Select a memory from the left list',
    editModalTitle: (id) => `Edit Memory #${id}`,
    addModalTitle: 'Add New Memory',
    contentLabel: 'Memory Content',
    placeholderExample: 'e.g., Always use strict mode for this project',
    cancel: 'Cancel',
    saving: 'Saving...',
    save: 'Save',
    deleteConfirm: (id) => `Delete Memory #${id}?`,
    saveError: (m) => `Save error: ${m}`,
    deleteError: (m) => `Delete error: ${m}`,
    defaultProfileLabel: 'Default (~/.hermes)',
    profileLabel: 'Profile:',
    storageLabel: 'Storage:'
  }
};

const getCategoryStyles = () => {
  const loc = getLocale();
  const t = I18N[loc] || I18N.en;
  return {
    preference: { label: t.preference, dot: '#6366f1', badgeBg: 'rgba(99, 102, 241, 0.1)', badgeColor: '#4f46e5' },
    project:    { label: t.project,    dot: '#10b981', badgeBg: 'rgba(16, 185, 129, 0.1)', badgeColor: '#059669' },
    rule:       { label: t.rule,       dot: '#f59e0b', badgeBg: 'rgba(245, 158, 11, 0.1)', badgeColor: '#d97706' },
    general:    { label: t.general,    dot: '#8b5cf6', badgeBg: 'rgba(139, 92, 246, 0.1)', badgeColor: '#7c3aed' }
  };
};

const CATEGORY_STYLES = getCategoryStyles();

const renderBadge = (catKey) => {
  const styles = getCategoryStyles();
  const cat = styles[catKey] || styles.general;
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
  });
};

// UTC 日時文字列をブラウザのローカルタイムゾーン（JST等）に変換するヘルパー
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
    // 'YYYY-MM-DD HH:MM:SS' 等のUTC形式（タイムゾーン指定なし）の場合、'Z' を補完してUTCとして解釈
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
  if (!d) return val || '不明'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const formatLocalDateShort = (val) => {
  const d = parseUtcDate(val)
  if (!d) return (val || '').split(' ')[0].slice(5)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const S = {
  page: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    backgroundColor: 'var(--background, #ffffff)',
    color: 'var(--foreground, #111827)',
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
    backgroundColor: 'var(--primary, #18181b)',
    color: 'var(--primary-foreground, #ffffff)',
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
    border: '1px solid #e5e7eb',
    backgroundColor: '#ffffff',
    color: '#374151',
    cursor: 'pointer'
  },
  input: {
    width: '100%',
    backgroundColor: '#fafafa',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    padding: '6px 10px',
    fontSize: '12px',
    color: '#111827',
    outline: 'none',
    boxSizing: 'border-box'
  }
}

function MemoryManagementPage() {
  const t = I18N[getLocale()] || I18N.en
  const categoryStyles = getCategoryStyles()

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('all')

  const getInitialProfile = () => {
    try {
      const stored = typeof localStorage !== 'undefined' ? localStorage.getItem('hermes_sqlite_memory_profile') : null
      if (stored) return stored
    } catch (_) {}
    return 'assistant'
  }

  const [selectedProfile, setSelectedProfile] = useState(getInitialProfile)
  const [availableProfiles, setAvailableProfiles] = useState(['assistant', 'buddy', 'coding', 'research', 'default'])
  const [selectedId, setSelectedId] = useState(null)
  const [total, setTotal] = useState(0)
  const [categories, setCategories] = useState({})
  const [dbPath, setDbPath] = useState('')
  const [showAddModal, setShowAddModal] = useState(false)
  const [editId, setEditId] = useState(null)
  const [formCategory, setFormCategory] = useState('preference')
  const [formContent, setFormContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [isCompact, setIsCompact] = useState(false)
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)

  const containerRef = useRef(null)
  const isManuallySelected = useRef(false)
  const focusedProfileAtom = host.state?.focusedSessionProfile || host.state?.profile
  const hostProfileName = useValue(focusedProfileAtom)

  const handleSelectProfile = (newProfile) => {
    isManuallySelected.current = true
    setSelectedProfile(newProfile)
    try { localStorage.setItem('hermes_sqlite_memory_profile', newProfile) } catch (_) {}
  }

  useEffect(() => {
    if (!isManuallySelected.current && hostProfileName && hostProfileName !== 'default') {
      const stored = typeof localStorage !== 'undefined' ? localStorage.getItem('hermes_sqlite_memory_profile') : null
      if (!stored) {
        setSelectedProfile(hostProfileName)
      }
    }
  }, [hostProfileName])

  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setIsCompact(e.contentRect.width < 580))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Escapeキーでモーダルを閉じる
  useEffect(() => {
    if (!showAddModal) return
    const onKeyDown = (e) => { if (e.key === 'Escape') setShowAddModal(false) }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [showAddModal])

  const loadStats = useCallback(async () => {
    try {
      const data = await api(`/stats?profile=${encodeURIComponent(selectedProfile)}`)
      setTotal(data.total_memories || 0)
      setCategories(data.categories || {})
      setDbPath(data.db_path || '')
      if (Array.isArray(data.available_profiles) && data.available_profiles.length > 0) {
        setAvailableProfiles(data.available_profiles)
      }
    } catch (e) {
      console.warn('Error loading memory stats:', e)
    }
  }, [selectedProfile])

  const loadMemories = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ profile: selectedProfile, limit: '100', offset: '0' })
      if (query.trim()) params.append('query', query.trim())
      if (selectedCategory && selectedCategory !== 'all') params.append('category', selectedCategory)

      const data = await api(`/memories?${params.toString()}`)
      const loaded = data.items || []
      setItems(loaded)
      setSelectedId((prev) => (prev && loaded.some((i) => i.id === prev) ? prev : (loaded[0]?.id ?? null)))
    } catch (e) {
      console.error('Error loading memories:', e)
    } finally {
      setLoading(false)
    }
  }, [selectedProfile, query, selectedCategory])

  useEffect(() => {
    loadStats()
    loadMemories()
  }, [loadStats, loadMemories])

  const activeMemory = useMemo(() => items.find((i) => i.id === selectedId) || null, [items, selectedId])

  const handleSelectItem = (id) => {
    setSelectedId(id)
    if (isCompact) setMobileDetailOpen(true)
  }

  const handleSave = async (e) => {
    e.preventDefault()
    if (!formContent.trim()) return
    setSaving(true)
    try {
      const method = editId ? 'PUT' : 'POST'
      const endpoint = editId ? `/memories/${editId}` : '/memories'
      await api(`${endpoint}?profile=${encodeURIComponent(selectedProfile)}`, {
        method,
        body: { content: formContent.trim(), category: formCategory }
      })
      setShowAddModal(false)
      setEditId(null)
      setFormContent('')
      await Promise.all([loadStats(), loadMemories()])
    } catch (err) {
      console.error('Error saving memory:', err)
      alert(t.saveError(err.message))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm(t.deleteConfirm(id))) return
    try {
      await api(`/memories/${id}?profile=${encodeURIComponent(selectedProfile)}`, { method: 'DELETE' })
      if (selectedId === id) setSelectedId(null)
      if (isCompact) setMobileDetailOpen(false)
      await Promise.all([loadStats(), loadMemories()])
    } catch (err) {
      console.error('Error deleting memory:', err)
      alert(t.deleteError(err.message))
    }
  }

  const handleOpenModal = (item = null) => {
    setEditId(item?.id ?? null)
    setFormCategory(item?.category || 'preference')
    setFormContent(item?.content || '')
    setShowAddModal(true)
  }

  const categoryPills = [
    { key: 'all', label: t.all, count: total },
    { key: 'preference', label: t.preference, count: categories['preference'] || 0 },
    { key: 'project', label: t.project, count: categories['project'] || 0 },
    { key: 'rule', label: t.rule, count: categories['rule'] || 0 },
    { key: 'general', label: t.general, count: categories['general'] || 0 }
  ]

  return jsxs('div', {
    ref: containerRef,
    style: S.page,
    children: [
      // 1. トップヘッダー
      jsxs('div', {
        style: { padding: '14px 18px 10px 18px', borderBottom: '1px solid var(--border, #e5e7eb)', ...S.flexCol, gap: '10px', flexShrink: 0 },
        children: [
          jsxs('div', {
            style: { ...S.flexBetween, gap: '12px' },
            children: [
              // 検索バー
              jsxs('div', {
                style: { position: 'relative', display: 'flex', alignItems: 'center', flex: 1, maxWidth: '320px' },
                children: [
                  jsx('span', {
                    style: { position: 'absolute', left: '8px', color: '#9ca3af', display: 'flex', pointerEvents: 'none' },
                    children: jsx(Codicon, { name: 'search', size: '0.9rem' })
                  }),
                  jsx('input', {
                    type: 'text',
                    placeholder: t.searchPlaceholder,
                    value: query,
                    onChange: (e) => setQuery(e.target.value),
                    style: { width: '100%', border: 'none', background: 'transparent', paddingLeft: '28px', paddingRight: '8px', paddingTop: '4px', paddingBottom: '4px', fontSize: '13px', color: 'var(--foreground, #111827)', outline: 'none' }
                  })
                ]
              }),
              jsx('button', { onClick: () => handleOpenModal(), style: S.btnPrimary, children: [jsx(Codicon, { name: 'add', size: '0.8rem' }), ` ${t.addMemory}`] })
            ]
          }),

          // カテゴリピル
          jsxs('div', {
            style: { ...S.flexCol, gap: '6px' },
            children: [
              jsx('span', { style: { fontSize: '11px', color: '#6b7280', fontWeight: 500 }, children: t.category }),
              jsx('div', {
                style: { display: 'flex', alignItems: 'center', gap: '8px', overflowX: 'auto', whiteSpace: 'nowrap', scrollbarWidth: 'none', paddingBottom: '2px' },
                children: categoryPills.map((pill) => {
                  const isSelected = selectedCategory === pill.key
                  return jsxs('button', {
                    key: pill.key,
                    onClick: () => setSelectedCategory(pill.key),
                    style: {
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      padding: '3px 12px',
                      borderRadius: '9999px',
                      fontSize: '12px',
                      fontWeight: isSelected ? 600 : 400,
                      backgroundColor: isSelected ? '#f3f4f6' : 'transparent',
                      color: isSelected ? '#111827' : '#4b5563',
                      border: isSelected ? '1.5px solid #111827' : '1px solid #e5e7eb',
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
      }),

      // 2. サブ情報バー
      jsxs('div', {
        style: { ...S.flexBetween, padding: '6px 18px', backgroundColor: '#fafafa', borderBottom: '1px solid var(--border, #e5e7eb)', fontSize: '11px', color: '#6b7280', flexShrink: 0 },
        children: [
          jsxs('div', {
            style: { ...S.flexRow, gap: '8px', minWidth: 0 },
            children: [
              jsx('span', { style: { fontWeight: 500, color: '#374151' }, children: t.profileLabel }),
              jsx('select', {
                value: selectedProfile,
                onChange: (e) => handleSelectProfile(e.target.value),
                style: { padding: '2px 6px', borderRadius: '4px', border: '1px solid #d1d5db', backgroundColor: '#ffffff', fontSize: '11px', fontWeight: 600, color: '#111827', cursor: 'pointer', outline: 'none' },
                children: availableProfiles.map((p) => jsx('option', { key: p, value: p, children: p === 'default' ? t.defaultProfileLabel : p }, p))
              }),
              jsx('span', { style: { color: '#d1d5db' }, children: '•' }),
              jsx('span', { style: { color: '#6b7280' }, children: t.storageLabel }),
              jsx('span', { style: { fontWeight: 500, color: '#111827', ...S.ellipsis, maxWidth: '320px' }, title: dbPath || 'SQLite (~/.hermes/memory.db)', children: dbPath ? `SQLite (${dbPath})` : 'SQLite (~/.hermes/memory.db)' })
            ]
          }),
          jsxs('div', {
            style: { ...S.flexRow, gap: '10px', flexShrink: 0 },
            children: [
              jsx('span', { style: { fontVariantNumeric: 'tabular-nums' }, children: t.totalCount(total) }),
              jsx('button', {
                onClick: () => { loadStats(); loadMemories(); },
                style: { display: 'flex', alignItems: 'center', gap: '3px', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, fontSize: '11px' },
                children: [jsx(Codicon, { name: 'refresh', size: '0.75rem' }), ` ${t.refresh}`]
              })
            ]
          })
        ]
      }),

      // 3. メイン2カラム
      jsxs('div', {
        style: { display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' },
        children: [
          // 左カラム（リスト）
          (!isCompact || !mobileDetailOpen) && (
            jsx('div', {
              style: {
                display: 'flex',
                flexDirection: 'column',
                width: isCompact ? '100%' : '210px',
                minWidth: isCompact ? '100%' : '190px',
                maxWidth: isCompact ? '100%' : '230px',
                borderRight: isCompact ? 'none' : '1px solid var(--border, #e5e7eb)',
                backgroundColor: 'var(--background, #ffffff)',
                overflowY: 'auto',
                height: '100%',
                boxSizing: 'border-box'
              },
              children: loading ? (
                jsx('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '30px 10px', fontSize: '11px', color: '#6b7280' }, children: t.loading })
              ) : items.length === 0 ? (
                jsxs('div', {
                  style: { padding: '24px 14px', textAlign: 'center', fontSize: '11px', color: '#6b7280' },
                  children: [
                    jsx('div', { style: { fontWeight: 500, color: '#111827', marginBottom: '4px' }, children: query ? t.noMatches : t.noMemories }),
                    jsx('p', { style: { fontSize: '10px', opacity: 0.75, margin: 0, lineHeight: 1.4 }, children: query ? t.tryOtherKeyword : t.emptyHint })
                  ]
                })
              ) : (
                items.map((item) => {
                  const isSelected = item.id === selectedId
                  const catStyles = getCategoryStyles()
                  const cat = catStyles[item.category] || catStyles.general
                  return jsxs('div', {
                    key: item.id,
                    onClick: () => handleSelectItem(item.id),
                    style: {
                      padding: '10px 14px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '8px',
                      backgroundColor: isSelected ? '#f3f4f6' : 'transparent',
                      borderLeft: isSelected ? '3px solid #111827' : '3px solid transparent',
                      borderBottom: '1px solid rgba(0, 0, 0, 0.03)'
                    },
                    children: [
                      jsxs('div', {
                        style: { display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 },
                        children: [
                          jsx('span', { style: { width: '7px', height: '7px', borderRadius: '50%', backgroundColor: cat.dot, flexShrink: 0 } }),
                          jsxs('div', {
                            style: { display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 },
                            children: [
                              jsx('span', { style: { fontSize: '12px', fontWeight: isSelected ? 600 : 500, color: '#111827', whiteSpace: 'nowrap' }, children: `Memory #${item.id}` }),
                              jsx('span', { style: { fontSize: '11px', color: '#6b7280', ...S.ellipsis }, children: item.content })
                            ]
                          })
                        ]
                      }),
                      jsx('span', { style: { fontSize: '9px', color: '#9ca3af', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }, children: formatLocalDateShort(item.created_at) })
                    ]
                  })
                })
              )
            })
          ),

          // 右カラム（詳細ビュー）
          (!isCompact || (mobileDetailOpen && activeMemory)) && (
            jsx('div', {
              style: { flex: 1, overflowY: 'auto', padding: '20px 24px', backgroundColor: 'var(--background, #ffffff)', height: '100%', boxSizing: 'border-box' },
              children: activeMemory ? (
                jsxs('div', {
                  style: { maxWidth: '580px', display: 'flex', flexDirection: 'column', gap: '18px' },
                  children: [
                    isCompact && jsx('button', { onClick: () => setMobileDetailOpen(false), style: { ...S.btnAction, border: 'none', padding: 0 }, children: [jsx(Codicon, { name: 'arrow-left', size: '0.85rem' }), ` ${t.backToList}`] }),
                    jsxs('div', {
                      style: { ...S.flexBetween, alignItems: 'flex-start', gap: '12px' },
                      children: [
                        jsxs('div', {
                          style: { display: 'flex', alignItems: 'center', gap: '8px' },
                          children: [
                            jsx('h2', { style: { fontSize: '18px', fontWeight: 600, margin: 0, color: '#111827' }, children: `Memory #${activeMemory.id}` }),
                            renderBadge(activeMemory.category)
                          ]
                        }),
                        jsxs('div', {
                          style: { display: 'flex', gap: '6px' },
                          children: [
                            jsx('button', { onClick: () => handleOpenModal(activeMemory), style: S.btnAction, children: [jsx(Codicon, { name: 'edit', size: '0.8rem' }), ` ${t.edit}`] }),
                            jsx('button', { onClick: () => handleDelete(activeMemory.id), style: { ...S.btnAction, color: '#dc2626' }, children: [jsx(Codicon, { name: 'trash', size: '0.8rem' }), ` ${t.delete}`] })
                          ]
                        })
                      ]
                    }),

                    // メタ情報バー
                    jsxs('div', {
                      style: { display: 'flex', gap: '16px', padding: '10px 14px', backgroundColor: '#fafafa', borderRadius: '6px', fontSize: '11px', color: '#6b7280' },
                      children: [
                        jsxs('div', { children: [t.created, jsx('span', { style: { fontWeight: 500, color: '#111827' }, children: formatLocalTime(activeMemory.created_at) })] }),
                        jsxs('div', { children: [t.updated, jsx('span', { style: { fontWeight: 500, color: '#111827' }, children: formatLocalTime(activeMemory.updated_at) })] }),
                        jsxs('div', { children: [t.source, jsx('span', { style: { fontWeight: 500, color: '#111827' }, children: activeMemory.source || 'manual' })] })
                      ]
                    }),

                    // 本文
                    jsxs('div', {
                      style: { ...S.flexCol, gap: '6px' },
                      children: [
                        jsx('span', { style: { fontSize: '11px', fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: t.contentHeading }),
                        jsx('div', {
                          style: { padding: '14px 16px', backgroundColor: '#f9fafb', borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: '13px', lineHeight: 1.6, color: '#111827', whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
                          children: activeMemory.content
                        })
                      ]
                    })
                  ]
                })
              ) : (
                jsx('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#9ca3af', fontSize: '12px' }, children: t.selectPrompt })
              )
            })
          )
        ]
      }),

      // 4. 追加・編集モーダル
      showAddModal && (
        jsx('div', {
          style: { position: 'fixed', inset: 0, backgroundColor: 'rgba(0, 0, 0, 0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, padding: '16px' },
          children: jsxs('div', {
            style: { width: '100%', maxWidth: '440px', backgroundColor: '#ffffff', borderRadius: '10px', boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.1)', padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' },
            children: [
              jsxs('div', {
                style: S.flexBetween,
                children: [
                  jsx('h3', { style: { fontSize: '15px', fontWeight: 600, margin: 0, color: '#111827' }, children: editId ? t.editModalTitle(editId) : t.addModalTitle }),
                  jsx('button', { onClick: () => setShowAddModal(false), style: { background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 0 }, children: jsx(Codicon, { name: 'close', size: '1rem' }) })
                ]
              }),
              jsxs('form', {
                onSubmit: handleSave,
                style: { ...S.flexCol, gap: '12px' },
                children: [
                  jsxs('div', {
                    style: { ...S.flexCol, gap: '4px' },
                    children: [
                      jsx('label', { style: { fontSize: '11px', fontWeight: 500, color: '#374151' }, children: t.modalCategoryLabel }),
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
                      jsx('label', { style: { fontSize: '11px', fontWeight: 500, color: '#374151' }, children: t.modalContentLabel }),
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
                    style: { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px', paddingTop: '6px', borderTop: '1px solid #e5e7eb' },
                    children: [
                      jsx('button', { type: 'button', onClick: () => setShowAddModal(false), style: S.btnAction, children: t.cancel }),
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
      )
    ]
  })
}

export default {
  id: 'sqlite-memory',
  name: 'Persistent Memory',
  description: 'SQLite-backed persistent long-term memory browser and manager.',
  defaultEnabled: true,
  register(ctx) {
    _rest = ctx.rest
    ctx.registerMany([
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
