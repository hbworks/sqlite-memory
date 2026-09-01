/**
 * SQLite Persistent Memory — Official Hermes Desktop UI Style
 *
 * Implements a clean, 2-column master-detail layout consistent with
 * Hermes official Settings, Toolsets, and Messaging pages.
 */

import {
  cn,
  Codicon,
  host,
  useValue,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Tip
} from '@hermes/plugin-sdk'
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

let _rest = null

async function api(path, options = {}) {
  // 1. Try ctx.rest first (Desktop SDK native bridge)
  if (_rest) {
    try {
      const cleanPath = path.startsWith('/') ? path.slice(1) : path
      const [route, queryStr] = cleanPath.split('?')
      const params = {}
      if (queryStr) {
        new URLSearchParams(queryStr).forEach((val, key) => {
          params[key] = val
        })
      }
      return await _rest(cleanPath, { ...options, params: { ...(options.params || {}), ...params } })
    } catch (e) {
      console.warn('ctx.rest call error, falling back to fetch:', e)
    }
  }

  // 2. Direct fetch fallback with session token auth
  const token = typeof window !== 'undefined' ? (window.__HERMES_SESSION_TOKEN__ || '') : ''
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...(options.headers || {})
  }
  const fullUrl = path.startsWith('/api') ? path : `/api/plugins/sqlite_memory${path.startsWith('/') ? path : '/' + path}`

  const res = await fetch(fullUrl, {
    ...options,
    headers,
    body: options.body ? (typeof options.body === 'string' ? options.body : JSON.stringify(options.body)) : undefined
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  return await res.json()
}

// カテゴリ別カラー定義（公式バッジスタイル）
const CATEGORY_STYLES = {
  preference: { label: '設定・好み', bg: '#eff6ff', color: '#1d4ed8', border: '#bfdbfe', dot: '#3b82f6' },
  project: { label: 'プロジェクト', bg: '#faf5ff', color: '#7e22ce', border: '#e9d5ff', dot: '#a855f7' },
  rule: { label: 'ルール', bg: '#fffbeb', color: '#b45309', border: '#fde68a', dot: '#f59e0b' },
  general: { label: '一般', bg: '#f3f4f6', color: '#374151', border: '#e5e7eb', dot: '#9ca3af' }
}

function MemoryManagementPage() {
  const containerRef = useRef(null)
  const [containerWidth, setContainerWidth] = useState(800)

  // 0. アクティブなプロファイル（bot）のリアルタイム監視
  const focusedProfileAtom = host?.state?.focusedSessionProfile || host?.state?.profile
  const focusedProfileRaw = (typeof useValue === 'function' && focusedProfileAtom) ? useValue(focusedProfileAtom) : null
  const hostProfileName = useMemo(() => {
    if (!focusedProfileRaw) return 'default'
    if (typeof focusedProfileRaw === 'string') return focusedProfileRaw
    if (typeof focusedProfileRaw === 'object') {
      return focusedProfileRaw.name || focusedProfileRaw.id || focusedProfileRaw.profile || 'default'
    }
    return 'default'
  }, [focusedProfileRaw])

  const [selectedProfile, setSelectedProfile] = useState(hostProfileName || 'default')
  const [availableProfiles, setAvailableProfiles] = useState(['default', 'assistant', 'research', 'coding', 'buddy', 'copywriter'])
  const prevHostProfileRef = useRef(hostProfileName)

  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('all')
  const [categories, setCategories] = useState({})
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)

  // Edit / Add modal state
  const [showAddModal, setShowAddModal] = useState(false)
  const [editingItem, setEditingItem] = useState(null)
  const [formContent, setFormContent] = useState('')
  const [formCategory, setFormCategory] = useState('preference')
  const [saving, setSaving] = useState(false)
  const [dbPath, setDbPath] = useState('')

  // 画面幅が極端に狭い場合の判定（< 520px）
  const isCompact = containerWidth < 520

  // Hermes のボットが実際に切り替わった時だけ selectedProfile を追従
  useEffect(() => {
    if (hostProfileName && hostProfileName !== prevHostProfileRef.current) {
      prevHostProfileRef.current = hostProfileName
      setSelectedProfile(hostProfileName)
    }
  }, [hostProfileName])

  // プロファイル切り替え時に選択をリセット
  useEffect(() => {
    setSelectedId(null)
  }, [selectedProfile])

  useEffect(() => {
    if (!containerRef.current) return
    const update = () => {
      if (containerRef.current) {
        setContainerWidth(containerRef.current.getBoundingClientRect().width || 800)
      }
    }
    update()
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          if (entry.contentRect) setContainerWidth(entry.contentRect.width)
        }
      })
      ro.observe(containerRef.current)
      return () => ro.disconnect()
    } else {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
  }, [])

  // Load stats
  const loadStats = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      params.set('profile', selectedProfile || 'default')
      const url = `/stats?${params.toString()}`
      const data = await api(url)
      if (data) {
        if (data.categories) {
          setCategories(data.categories)
        }
        if (data.db_path) {
          setDbPath(data.db_path)
        }
        if (Array.isArray(data.available_profiles) && data.available_profiles.length > 0) {
          setAvailableProfiles(data.available_profiles)
        }
      }
    } catch (e) {
      console.warn('Failed to load stats', e)
    }
  }, [selectedProfile])

  // Load memories list
  const loadMemories = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (query.trim()) params.set('query', query.trim())
      if (selectedCategory !== 'all') params.set('category', selectedCategory)
      params.set('profile', selectedProfile || 'default')
      params.set('limit', '100')

      const data = await api(`/memories?${params.toString()}`)
      const memoryItems = data.items || []
      setItems(memoryItems)
      setTotal(data.total || 0)

      if (memoryItems.length > 0) {
        setSelectedId((prev) => (prev && memoryItems.some(i => i.id === prev) ? prev : memoryItems[0].id))
      } else {
        setSelectedId(null)
      }
    } catch (e) {
      console.error('Failed to load memories', e)
      setError(e.message || 'データ取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [query, selectedCategory, selectedProfile])

  useEffect(() => {
    loadStats()
    loadMemories()
  }, [loadStats, loadMemories])

  const activeMemory = useMemo(() => items.find((i) => i.id === selectedId) || null, [items, selectedId])

  const handleSelectItem = (id) => {
    setSelectedId(id)
    if (isCompact) setMobileDetailOpen(true)
  }

  // Save new / edited memory
  const handleSave = async (e) => {
    e.preventDefault()
    if (!formContent.trim()) return
    setSaving(true)
    try {
      const profileParam = `?profile=${encodeURIComponent(selectedProfile || 'default')}`
      if (editingItem) {
        await api(`/memories/${editingItem.id}${profileParam}`, {
          method: 'PUT',
          body: { content: formContent.trim(), category: formCategory }
        })
      } else {
        const created = await api(`/memories${profileParam}`, {
          method: 'POST',
          body: { content: formContent.trim(), category: formCategory }
        })
        if (created && created.id) {
          setSelectedId(created.id)
          if (isCompact) setMobileDetailOpen(true)
        }
      }
      setShowAddModal(false)
      setEditingItem(null)
      setFormContent('')
      loadStats()
      loadMemories()
    } catch (e) {
      alert('保存に失敗しました: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  // Delete memory
  const handleDelete = async (id) => {
    if (!confirm(`記憶 #${id} を削除してもよろしいですか？`)) return
    try {
      const profileParam = `?profile=${encodeURIComponent(selectedProfile || 'default')}`
      await api(`/memories/${id}${profileParam}`, { method: 'DELETE' })
      if (isCompact) setMobileDetailOpen(false)
      loadStats()
      loadMemories()
    } catch (e) {
      alert('削除に失敗しました: ' + e.message)
    }
  }

  // Open Edit modal
  const handleOpenEdit = (item) => {
    setEditingItem(item)
    setFormContent(item.content)
    setFormCategory(item.category || 'preference')
    setShowAddModal(true)
  }

  // Open Create modal
  const handleOpenCreate = () => {
    setEditingItem(null)
    setFormContent('')
    setFormCategory('preference')
    setShowAddModal(true)
  }

  const categoryPills = [
    { key: 'all', label: 'すべて', count: total },
    { key: 'preference', label: '設定・好み', count: categories['preference'] || 0 },
    { key: 'project', label: 'プロジェクト', count: categories['project'] || 0 },
    { key: 'rule', label: 'ルール', count: categories['rule'] || 0 },
    { key: 'general', label: '一般', count: categories['general'] || 0 }
  ]

  return jsxs('div', {
    ref: containerRef,
    style: {
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
    children: [
      // 1. 公式風トップヘッダー（検索バー + カテゴリピル群）
      jsxs('div', {
        style: {
          padding: '14px 18px 10px 18px',
          borderBottom: '1px solid var(--border, #e5e7eb)',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          flexShrink: 0,
          background: 'var(--background, #ffffff)'
        },
        children: [
          // 上段: 検索バー + 「+ 記憶を追加」ボタン
          jsxs('div', {
            style: {
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px'
            },
            children: [
              // 検索バー
              jsxs('div', {
                style: {
                  position: 'relative',
                  display: 'flex',
                  alignItems: 'center',
                  flex: 1,
                  maxWidth: '320px'
                },
                children: [
                  jsx('span', {
                    style: {
                      position: 'absolute',
                      left: '8px',
                      color: '#9ca3af',
                      display: 'flex',
                      alignItems: 'center',
                      pointerEvents: 'none'
                    },
                    children: jsx(Codicon, { name: 'search', size: '0.9rem' })
                  }),
                  jsx('input', {
                    type: 'text',
                    placeholder: '「Rust」などを検索',
                    value: query,
                    onChange: (e) => setQuery(e.target.value),
                    style: {
                      width: '100%',
                      boxSizing: 'border-box',
                      border: 'none',
                      background: 'transparent',
                      paddingLeft: '28px',
                      paddingRight: '8px',
                      paddingTop: '4px',
                      paddingBottom: '4px',
                      fontSize: '13px',
                      color: 'var(--foreground, #111827)',
                      outline: 'none'
                    }
                  })
                ]
              }),

              // 記憶を追加ボタン
              jsx('button', {
                onClick: handleOpenCreate,
                style: {
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
                children: [jsx(Codicon, { name: 'add', size: '0.8rem' }), ' 記憶を追加']
              })
            ]
          }),

          // 下段: カテゴリラベル + ピル一覧（公式Messaging画面の「適用対象」ピルスタイル）
          jsxs('div', {
            style: {
              display: 'flex',
              flexDirection: 'column',
              gap: '6px'
            },
            children: [
              jsx('span', {
                style: { fontSize: '11px', color: '#6b7280', fontWeight: 500 },
                children: 'カテゴリ'
              }),
              jsxs('div', {
                style: {
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  overflowX: 'auto',
                  whiteSpace: 'nowrap',
                  scrollbarWidth: 'none',
                  msOverflowStyle: 'none',
                  paddingBottom: '2px'
                },
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
                      flexShrink: 0,
                      whiteSpace: 'nowrap',
                      transition: 'all 0.1s ease'
                    },
                    children: [
                      jsx('span', { children: pill.label }),
                      pill.count > 0 && (
                        jsx('span', {
                          style: {
                            fontSize: '10px',
                            opacity: 0.7,
                            fontVariantNumeric: 'tabular-nums'
                          },
                          children: pill.count
                        })
                      )
                    ]
                  })
                })
              })
            ]
          })
        ]
      }),

      // 2. サブ情報バー（Storage情報 & 合計件数 & 更新）
      jsxs('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '6px 18px',
          backgroundColor: '#fafafa',
          borderBottom: '1px solid var(--border, #e5e7eb)',
          fontSize: '11px',
          color: '#6b7280',
          flexShrink: 0
        },
        children: [
          jsxs('div', {
            style: { display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 },
            children: [
              jsx('span', { style: { fontWeight: 500, color: '#374151' }, children: 'Profile:' }),
              jsx('select', {
                value: selectedProfile,
                onChange: (e) => setSelectedProfile(e.target.value),
                style: {
                  padding: '2px 6px',
                  borderRadius: '4px',
                  border: '1px solid #d1d5db',
                  backgroundColor: '#ffffff',
                  fontSize: '11px',
                  fontWeight: 600,
                  color: '#111827',
                  cursor: 'pointer',
                  outline: 'none'
                },
                children: availableProfiles.map((p) =>
                  jsx('option', { key: p, value: p, children: p === 'default' ? 'デフォルト (~/.hermes)' : p }, p)
                )
              }),
              jsx('span', { style: { color: '#d1d5db' }, children: '•' }),
              jsx('span', { style: { color: '#6b7280' }, children: 'Storage:' }),
              jsx('span', {
                style: { fontWeight: 500, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '320px' },
                title: dbPath || 'SQLite (~/.hermes/memory.db)',
                children: dbPath ? `SQLite (${dbPath})` : 'SQLite (~/.hermes/memory.db)'
              })
            ]
          }),
          jsxs('div', {
            style: { display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 },
            children: [
              jsx('span', { style: { fontVariantNumeric: 'tabular-nums' }, children: `合計: ${total} 件` }),
              jsx('button', {
                onClick: () => { loadStats(); loadMemories(); },
                style: {
                  display: 'flex',
                  alignItems: 'center',
                  gap: '3px',
                  background: 'none',
                  border: 'none',
                  color: 'inherit',
                  cursor: 'pointer',
                  padding: 0,
                  fontSize: '11px'
                },
                children: [jsx(Codicon, { name: 'refresh', size: '0.75rem' }), ' 更新']
              })
            ]
          })
        ]
      }),

      // 3. メイン2カラム（公式スリムリスト + 詳細ビュー）
      jsxs('div', {
        style: {
          display: 'flex',
          flex: 1,
          overflow: 'hidden',
          position: 'relative'
        },
        children: [
          // 左カラム（リスト）: 公式の幅（210px）でスリムに配置
          (!isCompact || !mobileDetailOpen || !activeMemory) && (
            jsxs('div', {
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
              children: [
                loading ? (
                  jsx('div', {
                    style: { display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '30px 10px', fontSize: '11px', color: '#6b7280' },
                    children: '読み込み中...'
                  })
                ) : items.length === 0 ? (
                  jsxs('div', {
                    style: { padding: '24px 14px', textAlign: 'center', fontSize: '11px', color: '#6b7280' },
                    children: [
                      jsx('div', { style: { fontWeight: 500, color: '#111827', marginBottom: '4px' }, children: query ? '一致なし' : '記憶がありません' }),
                      jsx('p', {
                        style: { fontSize: '10px', opacity: 0.75, margin: 0, lineHeight: 1.4 },
                        children: query ? '別のキーワードをお試しください' : '「+ 記憶を追加」から登録できます'
                      })
                    ]
                  })
                ) : (
                  items.map((item) => {
                    const isSelected = item.id === selectedId
                    const catStyle = CATEGORY_STYLES[item.category] || CATEGORY_STYLES.general
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
                        transition: 'all 0.1s ease',
                        borderBottom: '1px solid rgba(0, 0, 0, 0.03)'
                      },
                      children: [
                        jsxs('div', {
                          style: { display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 },
                          children: [
                            // カテゴリドット
                            jsx('span', {
                              style: {
                                width: '7px',
                                height: '7px',
                                borderRadius: '50%',
                                backgroundColor: catStyle.dot,
                                flexShrink: 0
                              }
                            }),
                            // タイトル / 本文
                            jsxs('div', {
                              style: { display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 },
                              children: [
                                jsxs('div', {
                                  style: { display: 'flex', alignItems: 'center', gap: '4px' },
                                  children: [
                                    jsx('span', {
                                      style: {
                                        fontSize: '12px',
                                        fontWeight: isSelected ? 600 : 500,
                                        color: '#111827',
                                        whiteSpace: 'nowrap'
                                      },
                                      children: `Memory #${item.id}`
                                    })
                                  ]
                                }),
                                jsx('span', {
                                  style: {
                                    fontSize: '11px',
                                    color: '#6b7280',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap'
                                  },
                                  children: item.content
                                })
                              ]
                            })
                          ]
                        }),
                        // 右側の小さなステータスドットまたは日付
                        jsx('span', {
                          style: {
                            fontSize: '9px',
                            color: '#9ca3af',
                            fontVariantNumeric: 'tabular-nums',
                            flexShrink: 0
                          },
                          children: (item.created_at || '').split(' ')[0].slice(5) // MM-DD
                        })
                      ]
                    })
                  })
                )
              ]
            })
          ),

          // 右カラム（詳細ビュー）: 公式Messaging設定風のクリーンなレイアウト
          (!isCompact || (mobileDetailOpen && activeMemory)) && (
            jsx('div', {
              style: {
                flex: 1,
                overflowY: 'auto',
                padding: '20px 24px',
                backgroundColor: 'var(--background, #ffffff)',
                height: '100%',
                boxSizing: 'border-box'
              },
              children: activeMemory ? (
                jsxs('div', {
                  style: { maxWidth: '580px', display: 'flex', flexDirection: 'column', gap: '18px' },
                  children: [
                    // コンパクト画面用の「← 一覧に戻る」
                    isCompact && (
                      jsx('button', {
                        onClick: () => setMobileDetailOpen(false),
                        style: {
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                          background: 'none',
                          border: 'none',
                          fontSize: '12px',
                          color: '#6b7280',
                          cursor: 'pointer',
                          padding: 0,
                          fontWeight: 500
                        },
                        children: [jsx(Codicon, { name: 'arrow-left', size: '0.85rem' }), ' 一覧に戻る']
                      })
                    ),

                    // タイトル + バッジ + 編集/削除ボタン
                    jsxs('div', {
                      style: {
                        display: 'flex',
                        alignItems: 'flex-start',
                        justifyContent: 'space-between',
                        gap: '12px'
                      },
                      children: [
                        jsxs('div', {
                          style: { display: 'flex', flexDirection: 'column', gap: '4px' },
                          children: [
                            jsxs('div', {
                              style: { display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' },
                              children: [
                                jsx('h2', {
                                  style: { fontSize: '18px', fontWeight: 700, margin: 0, color: '#111827', letterSpacing: '-0.02em' },
                                  children: `Memory #${activeMemory.id}`
                                }),
                                jsx('span', {
                                  style: {
                                    padding: '2px 8px',
                                    borderRadius: '12px',
                                    fontSize: '11px',
                                    fontWeight: 500,
                                    backgroundColor: (CATEGORY_STYLES[activeMemory.category] || CATEGORY_STYLES.general).bg,
                                    color: (CATEGORY_STYLES[activeMemory.category] || CATEGORY_STYLES.general).color,
                                    border: `1px solid ${(CATEGORY_STYLES[activeMemory.category] || CATEGORY_STYLES.general).border}`
                                  },
                                  children: (CATEGORY_STYLES[activeMemory.category] || CATEGORY_STYLES.general).label
                                })
                              ]
                            }),
                            jsx('p', {
                              style: { fontSize: '11px', color: '#6b7280', margin: 0, lineHeight: 1.4 },
                              children: 'セッション開始前の自動想起 (Prefetch) および検索ツールから参照されます。'
                            })
                          ]
                        }),

                        // 編集 / 削除 アクションボタン
                        jsxs('div', {
                          style: { display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 },
                          children: [
                            jsx('button', {
                              onClick: () => handleOpenEdit(activeMemory),
                              style: {
                                padding: '4px 10px',
                                borderRadius: '6px',
                                fontSize: '12px',
                                fontWeight: 500,
                                border: '1px solid #d1d5db',
                                backgroundColor: 'transparent',
                                color: '#111827',
                                cursor: 'pointer'
                              },
                              children: '編集'
                            }),
                            jsx('button', {
                              onClick: () => handleDelete(activeMemory.id),
                              style: {
                                padding: '4px 10px',
                                borderRadius: '6px',
                                fontSize: '12px',
                                fontWeight: 500,
                                border: '1px solid #fecaca',
                                backgroundColor: 'transparent',
                                color: '#dc2626',
                                cursor: 'pointer'
                              },
                              children: '削除'
                            })
                          ]
                        })
                      ]
                    }),

                    // 記憶内容カード
                    jsxs('div', {
                      style: { display: 'flex', flexDirection: 'column', gap: '6px' },
                      children: [
                        jsx('span', {
                          style: { fontSize: '11px', fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.04em' },
                          children: '記憶内容'
                        }),
                        jsx('div', {
                          style: {
                            padding: '14px 16px',
                            borderRadius: '8px',
                            backgroundColor: '#fafafa',
                            border: '1px solid #e5e7eb',
                            fontSize: '13px',
                            lineHeight: 1.6,
                            color: '#111827',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                            userSelect: 'text'
                          },
                          children: activeMemory.content
                        })
                      ]
                    }),

                    // メタデータ詳細表
                    jsxs('div', {
                      style: {
                        padding: '12px 16px',
                        borderRadius: '8px',
                        backgroundColor: '#fafafa',
                        border: '1px solid #e5e7eb',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '8px',
                        fontSize: '11px'
                      },
                      children: [
                        jsxs('div', {
                          style: { display: 'grid', gridTemplateColumns: '85px 1fr', gap: '8px', alignItems: 'center' },
                          children: [
                            jsx('span', { style: { color: '#6b7280' }, children: 'ID' }),
                            jsx('span', { style: { fontFamily: 'monospace', color: '#111827' }, children: `#${activeMemory.id}` })
                          ]
                        }),
                        jsxs('div', {
                          style: { display: 'grid', gridTemplateColumns: '85px 1fr', gap: '8px', alignItems: 'center' },
                          children: [
                            jsx('span', { style: { color: '#6b7280' }, children: 'Category' }),
                            jsx('span', { style: { fontFamily: 'monospace', color: '#111827' }, children: activeMemory.category || 'general' })
                          ]
                        }),
                        jsxs('div', {
                          style: { display: 'grid', gridTemplateColumns: '85px 1fr', gap: '8px', alignItems: 'center' },
                          children: [
                            jsx('span', { style: { color: '#6b7280' }, children: 'Source' }),
                            jsx('span', { style: { color: '#111827' }, children: activeMemory.source || 'manual' })
                          ]
                        }),
                        jsxs('div', {
                          style: { display: 'grid', gridTemplateColumns: '85px 1fr', gap: '8px', alignItems: 'center' },
                          children: [
                            jsx('span', { style: { color: '#6b7280' }, children: 'Created At' }),
                            jsx('span', { style: { fontFamily: 'monospace', color: '#111827', fontVariantNumeric: 'tabular-nums' }, children: activeMemory.created_at || '-' })
                          ]
                        }),
                        jsxs('div', {
                          style: { display: 'grid', gridTemplateColumns: '85px 1fr', gap: '8px', alignItems: 'center' },
                          children: [
                            jsx('span', { style: { color: '#6b7280' }, children: 'Updated At' }),
                            jsx('span', { style: { fontFamily: 'monospace', color: '#111827', fontVariantNumeric: 'tabular-nums' }, children: activeMemory.updated_at || '-' })
                          ]
                        })
                      ]
                    })
                  ]
                })
              ) : (
                jsxs('div', {
                  style: {
                    height: '100%',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    textAlign: 'center',
                    fontSize: '12px',
                    color: '#6b7280',
                    padding: '20px',
                    gap: '6px'
                  },
                  children: [
                    jsx('div', {
                      style: { fontSize: '13px', fontWeight: 500, color: '#111827' },
                      children: items.length === 0 ? '記憶がありません' : '左側のリストから記憶を選択してください'
                    }),
                    jsx('p', {
                      style: { fontSize: '11px', color: '#6b7280', maxWidth: '320px', margin: 0, lineHeight: 1.5 },
                      children: items.length === 0
                        ? '「+ 記憶を追加」から手動で登録するか、会話中にエージェントへ指示すると自動蓄積されます。'
                        : '選択すると内容の全文確認、編集、削除が行えます。'
                    })
                  ]
                })
              )
            })
          )
        ]
      }),

      // 4. 追加 / 編集 モーダル
      showAddModal && (
        jsx('div', {
          style: {
            position: 'fixed',
            inset: 0,
            zIndex: 50,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: 'rgba(0, 0, 0, 0.4)',
            padding: '16px',
            boxSizing: 'border-box'
          },
          children: jsxs('div', {
            style: {
              width: '100%',
              maxWidth: '460px',
              backgroundColor: '#ffffff',
              border: '1px solid #e5e7eb',
              borderRadius: '12px',
              padding: '20px',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxSizing: 'border-box'
            },
            children: [
              jsxs('div', {
                style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
                children: [
                  jsx('h3', {
                    style: { fontSize: '14px', fontWeight: 700, color: '#111827', margin: 0 },
                    children: editingItem ? '記憶を編集' : '新しい記憶を追加'
                  }),
                  jsx('button', {
                    onClick: () => setShowAddModal(false),
                    style: { background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', padding: '2px' },
                    children: jsx(Codicon, { name: 'close', size: '1rem' })
                  })
                ]
              }),
              jsxs('form', {
                onSubmit: handleSave,
                style: { display: 'flex', flexDirection: 'column', gap: '12px' },
                children: [
                  jsxs('div', {
                    style: { display: 'flex', flexDirection: 'column', gap: '4px' },
                    children: [
                      jsx('label', { style: { fontSize: '11px', fontWeight: 500, color: '#374151' }, children: 'カテゴリ' }),
                      jsx('select', {
                        value: formCategory,
                        onChange: (e) => setFormCategory(e.target.value),
                        style: {
                          width: '100%',
                          backgroundColor: '#fafafa',
                          border: '1px solid #d1d5db',
                          borderRadius: '6px',
                          padding: '6px 10px',
                          fontSize: '12px',
                          color: '#111827',
                          outline: 'none',
                          boxSizing: 'border-box'
                        },
                        children: [
                          jsx('option', { value: 'preference', children: '設定・好み (preference)' }),
                          jsx('option', { value: 'project', children: 'プロジェクト (project)' }),
                          jsx('option', { value: 'rule', children: 'ルール (rule)' }),
                          jsx('option', { value: 'general', children: '一般 (general)' })
                        ]
                      })
                    ]
                  }),
                  jsxs('div', {
                    style: { display: 'flex', flexDirection: 'column', gap: '4px' },
                    children: [
                      jsx('label', { style: { fontSize: '11px', fontWeight: 500, color: '#374151' }, children: '記憶内容' }),
                      jsx('textarea', {
                        rows: 5,
                        required: true,
                        placeholder: '例: ユーザーはフロントエンドでVanilla CSSを優先する方針',
                        value: formContent,
                        onChange: (e) => setFormContent(e.target.value),
                        style: {
                          width: '100%',
                          backgroundColor: '#fafafa',
                          border: '1px solid #d1d5db',
                          borderRadius: '6px',
                          padding: '10px',
                          fontSize: '12px',
                          color: '#111827',
                          outline: 'none',
                          lineHeight: 1.5,
                          boxSizing: 'border-box'
                        }
                      })
                    ]
                  }),
                  jsxs('div', {
                    style: {
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'flex-end',
                      gap: '8px',
                      paddingTop: '6px',
                      borderTop: '1px solid #e5e7eb'
                    },
                    children: [
                      jsx('button', {
                        type: 'button',
                        onClick: () => setShowAddModal(false),
                        style: {
                          padding: '5px 12px',
                          borderRadius: '6px',
                          fontSize: '12px',
                          border: '1px solid #d1d5db',
                          backgroundColor: 'transparent',
                          color: '#374151',
                          cursor: 'pointer'
                        },
                        children: 'キャンセル'
                      }),
                      jsx('button', {
                        type: 'submit',
                        disabled: saving || !formContent.trim(),
                        style: {
                          padding: '5px 14px',
                          borderRadius: '6px',
                          fontSize: '12px',
                          fontWeight: 500,
                          backgroundColor: '#18181b',
                          color: '#ffffff',
                          border: 'none',
                          cursor: saving || !formContent.trim() ? 'not-allowed' : 'pointer',
                          opacity: saving || !formContent.trim() ? 0.5 : 1
                        },
                        children: saving ? '保存中...' : '保存する'
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
  id: 'sqlite_memory',
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
