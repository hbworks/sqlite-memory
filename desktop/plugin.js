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
  user: { label: 'ユーザー情報', bg: '#fdf4ff', color: '#86198f', border: '#f0abfc', dot: '#d946ef' },
  project: { label: 'プロジェクト', bg: '#f0fdf4', color: '#15803d', border: '#bbf7d0', dot: '#22c55e' },
  rule: { label: 'ルール・制約', bg: '#fffbeb', color: '#b45309', border: '#fde68a', dot: '#f59e0b' },
  general: { label: '一般記憶', bg: '#f4f4f5', color: '#3f3f46', border: '#e4e4e7', dot: '#71717a' },
  fact: { label: 'ファクト', bg: '#f0fdfa', color: '#0f766e', border: '#99f6e4', dot: '#14b8a6' },
}

const CATEGORY_LIST = [
  { id: 'all', label: 'すべての記憶' },
  { id: 'preference', label: '設定・好み' },
  { id: 'user', label: 'ユーザー情報' },
  { id: 'project', label: 'プロジェクト' },
  { id: 'rule', label: 'ルール・制約' },
  { id: 'fact', label: 'ファクト' },
  { id: 'general', label: '一般記憶' },
]

function CategoryBadge({ category }) {
  const style = CATEGORY_STYLES[category] || CATEGORY_STYLES.general
  return jsx('span', {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: '5px',
      padding: '2px 8px',
      borderRadius: '9999px',
      fontSize: '11px',
      fontWeight: 500,
      backgroundColor: style.bg,
      color: style.color,
      border: `1px solid ${style.border}`,
      lineHeight: '1.2'
    },
    children: [
      jsx('span', {
        key: 'dot',
        style: {
          width: '5px',
          height: '5px',
          borderRadius: '50%',
          backgroundColor: style.dot
        }
      }),
      style.label
    ]
  })
}

function MemoryManagementPage() {
  const [memories, setMemories] = useState([])
  const [stats, setStats] = useState({ total_memories: 0, categories: {} })
  const [selectedCategory, setSelectedCategory] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedMemory, setSelectedMemory] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // 編集・新規作成ステート
  const [isEditing, setIsEditing] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [formCategory, setFormCategory] = useState('general')
  const [formContent, setFormContent] = useState('')
  const [saving, setSaving] = useState(false)

  // データフェッチ
  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const [statsData, memoriesData] = await Promise.all([
        api('/stats').catch(() => ({ total_memories: 0, categories: {} })),
        api(searchQuery.trim()
          ? `/search?q=${encodeURIComponent(searchQuery.trim())}&limit=50`
          : `/memories?limit=50${selectedCategory !== 'all' ? `&category=${selectedCategory}` : ''}`)
      ])

      setStats(statsData)
      const list = memoriesData.results || memoriesData.memories || []
      setMemories(list)

      // 選択状態の維持または先頭選択
      if (list.length > 0) {
        setSelectedMemory(prev => {
          if (!prev) return list[0]
          const found = list.find(m => m.id === prev.id)
          return found || list[0]
        })
      } else {
        setSelectedMemory(null)
      }
    } catch (err) {
      console.error('Failed to load memories:', err)
      setError(err.message || 'データの取得に失敗しました')
    } finally {
      setLoading(false)
    }
  }, [selectedCategory, searchQuery])

  useEffect(() => {
    loadData()
  }, [loadData])

  // 新規作成ハンドラ
  const handleCreate = async () => {
    if (!formContent.trim()) return
    try {
      setSaving(true)
      await api('/memories', {
        method: 'POST',
        body: { content: formContent.trim(), category: formCategory }
      })
      setIsCreating(false)
      setFormContent('')
      await loadData()
    } catch (err) {
      alert(`保存に失敗しました: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  // 編集保存ハンドラ
  const handleUpdate = async () => {
    if (!selectedMemory || !formContent.trim()) return
    try {
      setSaving(true)
      await api(`/memories/${selectedMemory.id}`, {
        method: 'PUT',
        body: { content: formContent.trim(), category: formCategory }
      })
      setIsEditing(false)
      await loadData()
    } catch (err) {
      alert(`更新に失敗しました: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  // 削除ハンドラ
  const handleDelete = async (id) => {
    if (!confirm('この記憶を削除してもよろしいですか？')) return
    try {
      await api(`/memories/${id}`, { method: 'DELETE' })
      if (selectedMemory?.id === id) {
        setSelectedMemory(null)
      }
      await loadData()
    } catch (err) {
      alert(`削除に失敗しました: ${err.message}`)
    }
  }

  return jsx('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      backgroundColor: '#ffffff',
      color: '#18181b',
      fontFamily: 'system-ui, -apple-system, sans-serif'
    },
    children: [
      // 1. トップヘッダー
      jsx('div', {
        key: 'header',
        style: {
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px 24px',
          borderBottom: '1px solid #e4e4e7',
          backgroundColor: '#ffffff'
        },
        children: [
          jsx('div', {
            key: 'title-area',
            children: [
              jsx('h1', {
                key: 'h1',
                style: { fontSize: '18px', fontWeight: 600, margin: 0, color: '#09090b' },
                children: 'SQLite 永続記憶 (Persistent Memory)'
              }),
              jsx('p', {
                key: 'sub',
                style: { fontSize: '12px', color: '#71717a', margin: '4px 0 0 0' },
                children: `合計 ${stats.total_memories || memories.length} 件の長期記憶が保存されています`
              })
            ]
          }),
          jsx('div', {
            key: 'action-area',
            style: { display: 'flex', gap: '8px' },
            children: [
              jsx('button', {
                key: 'refresh-btn',
                onClick: loadData,
                style: {
                  padding: '6px 12px',
                  borderRadius: '6px',
                  border: '1px solid #e4e4e7',
                  backgroundColor: '#ffffff',
                  color: '#3f3f46',
                  fontSize: '13px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                },
                children: [
                  jsx(Codicon, { key: 'icon', name: 'refresh' }),
                  '更新'
                ]
              }),
              jsx('button', {
                key: 'add-btn',
                onClick: () => {
                  setFormCategory('general')
                  setFormContent('')
                  setIsCreating(true)
                  setIsEditing(false)
                },
                style: {
                  padding: '6px 14px',
                  borderRadius: '6px',
                  border: 'none',
                  backgroundColor: '#18181b',
                  color: '#ffffff',
                  fontSize: '13px',
                  fontWeight: 500,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                },
                children: [
                  jsx(Codicon, { key: 'icon', name: 'add' }),
                  '記憶を追加'
                ]
              })
            ]
          })
        ]
      }),

      // 2. メインコンテンツ（2カラム構成）
      jsx('div', {
        key: 'main-content',
        style: {
          display: 'flex',
          flex: 1,
          overflow: 'hidden'
        },
        children: [
          // 左側: リスト & 検索カラム
          jsx('div', {
            key: 'left-col',
            style: {
              width: '380px',
              borderRight: '1px solid #e4e4e7',
              display: 'flex',
              flexDirection: 'column',
              backgroundColor: '#fafafa'
            },
            children: [
              // 検索バー
              jsx('div', {
                key: 'search-box',
                style: { padding: '12px 16px', borderBottom: '1px solid #e4e4e7' },
                children: jsx('input', {
                  type: 'text',
                  placeholder: '記憶を検索 (全文検索)...',
                  value: searchQuery,
                  onChange: (e) => setSearchQuery(e.target.value),
                  style: {
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    border: '1px solid #d4d4d8',
                    fontSize: '13px',
                    outline: 'none',
                    backgroundColor: '#ffffff',
                    boxSizing: 'border-box'
                  }
                })
              }),

              // カテゴリタブ
              jsx('div', {
                key: 'category-tabs',
                style: {
                  display: 'flex',
                  gap: '4px',
                  padding: '8px 12px',
                  overflowX: 'auto',
                  borderBottom: '1px solid #e4e4e7',
                  backgroundColor: '#ffffff'
                },
                children: CATEGORY_LIST.map(cat => {
                  const active = selectedCategory === cat.id
                  return jsx('button', {
                    key: cat.id,
                    onClick: () => setSelectedCategory(cat.id),
                    style: {
                      padding: '4px 10px',
                      borderRadius: '4px',
                      border: 'none',
                      backgroundColor: active ? '#18181b' : 'transparent',
                      color: active ? '#ffffff' : '#71717a',
                      fontSize: '12px',
                      fontWeight: active ? 500 : 400,
                      cursor: 'pointer',
                      whiteSpace: 'nowrap'
                    },
                    children: cat.label
                  })
                })
              }),

              // 記憶カード一覧
              jsx('div', {
                key: 'memory-list',
                style: { flex: 1, overflowY: 'auto', padding: '12px' },
                children: loading
                  ? jsx('div', { style: { padding: '24px', textAlign: 'center', color: '#a1a1aa', fontSize: '13px' }, children: '読み込み中...' })
                  : memories.length === 0
                    ? jsx('div', { style: { padding: '24px', textAlign: 'center', color: '#a1a1aa', fontSize: '13px' }, children: '記憶が見つかりません' })
                    : memories.map(mem => {
                        const isSelected = selectedMemory?.id === mem.id
                        return jsx('div', {
                          key: mem.id,
                          onClick: () => {
                            setSelectedMemory(mem)
                            setIsCreating(false)
                            setIsEditing(false)
                          },
                          style: {
                            padding: '12px',
                            marginBottom: '8px',
                            borderRadius: '8px',
                            backgroundColor: isSelected ? '#ffffff' : '#ffffff',
                            border: `1px solid ${isSelected ? '#18181b' : '#e4e4e7'}`,
                            boxShadow: isSelected ? '0 1px 3px rgba(0,0,0,0.05)' : 'none',
                            cursor: 'pointer',
                            transition: 'all 0.15s ease'
                          },
                          children: [
                            jsx('div', {
                              key: 'top',
                              style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' },
                              children: [
                                jsx(CategoryBadge, { key: 'badge', category: mem.category || 'general' }),
                                jsx('span', {
                                  key: 'id',
                                  style: { fontSize: '11px', color: '#a1a1aa', fontFamily: 'monospace' },
                                  children: `#${mem.id}`
                                })
                              ]
                            }),
                            jsx('div', {
                              key: 'content',
                              style: {
                                fontSize: '13px',
                                color: '#27272a',
                                lineHeight: '1.4',
                                display: '-webkit-box',
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: 'vertical',
                                overflow: 'hidden'
                              },
                              children: mem.content
                            })
                          ]
                        })
                      })
              })
            ]
          }),

          // 右側: 詳細ビュー / 編集ビュー
          jsx('div', {
            key: 'right-col',
            style: { flex: 1, overflowY: 'auto', padding: '24px', backgroundColor: '#ffffff' },
            children: isCreating
              ? jsx('div', {
                  key: 'create-form',
                  style: { maxWidth: '640px' },
                  children: [
                    jsx('h2', { key: 'h2', style: { fontSize: '16px', fontWeight: 600, margin: '0 0 16px 0' }, children: '新規記憶の追加' }),
                    jsx('div', {
                      key: 'cat-select',
                      style: { marginBottom: '16px' },
                      children: [
                        jsx('label', { key: 'label', style: { display: 'block', fontSize: '12px', fontWeight: 500, color: '#71717a', marginBottom: '6px' }, children: 'カテゴリ' }),
                        jsx('select', {
                          key: 'select',
                          value: formCategory,
                          onChange: (e) => setFormCategory(e.target.value),
                          style: { width: '100%', padding: '8px 12px', borderRadius: '6px', border: '1px solid #d4d4d8', fontSize: '13px' },
                          children: CATEGORY_LIST.filter(c => c.id !== 'all').map(c => jsx('option', { key: c.id, value: c.id, children: c.label }))
                        })
                      ]
                    }),
                    jsx('div', {
                      key: 'content-input',
                      style: { marginBottom: '16px' },
                      children: [
                        jsx('label', { key: 'label', style: { display: 'block', fontSize: '12px', fontWeight: 500, color: '#71717a', marginBottom: '6px' }, children: '記憶内容' }),
                        jsx('textarea', {
                          key: 'textarea',
                          rows: 6,
                          value: formContent,
                          onChange: (e) => setFormContent(e.target.value),
                          placeholder: 'エージェントが記憶すべき事実・設定・ルールを入力...',
                          style: { width: '100%', padding: '10px 12px', borderRadius: '6px', border: '1px solid #d4d4d8', fontSize: '13px', boxSizing: 'border-box' }
                        })
                      ]
                    }),
                    jsx('div', {
                      key: 'buttons',
                      style: { display: 'flex', gap: '8px' },
                      children: [
                        jsx('button', {
                          key: 'save',
                          onClick: handleCreate,
                          disabled: saving || !formContent.trim(),
                          style: { padding: '8px 16px', borderRadius: '6px', backgroundColor: '#18181b', color: '#fff', border: 'none', cursor: 'pointer' },
                          children: saving ? '保存中...' : '保存'
                        }),
                        jsx('button', {
                          key: 'cancel',
                          onClick: () => setIsCreating(false),
                          style: { padding: '8px 16px', borderRadius: '6px', backgroundColor: '#f4f4f5', color: '#3f3f46', border: '1px solid #e4e4e7', cursor: 'pointer' },
                          children: 'キャンセル'
                        })
                      ]
                    })
                  ]
                })
              : selectedMemory
                ? jsx('div', {
                    key: 'detail-view',
                    style: { maxWidth: '680px' },
                    children: [
                      jsx('div', {
                        key: 'detail-header',
                        style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' },
                        children: [
                          jsx('div', {
                            key: 'left',
                            style: { display: 'flex', alignItems: 'center', gap: '8px' },
                            children: [
                              jsx(CategoryBadge, { key: 'badge', category: selectedMemory.category || 'general' }),
                              jsx('span', { key: 'id', style: { color: '#a1a1aa', fontSize: '12px' }, children: `ID: #${selectedMemory.id}` })
                            ]
                          }),
                          jsx('div', {
                            key: 'right',
                            style: { display: 'flex', gap: '8px' },
                            children: [
                              jsx('button', {
                                key: 'edit',
                                onClick: () => {
                                  setFormCategory(selectedMemory.category || 'general')
                                  setFormContent(selectedMemory.content || '')
                                  setIsEditing(true)
                                },
                                style: { padding: '6px 12px', borderRadius: '6px', border: '1px solid #e4e4e7', backgroundColor: '#fff', fontSize: '12px', cursor: 'pointer' },
                                children: '編集'
                              }),
                              jsx('button', {
                                key: 'del',
                                onClick: () => handleDelete(selectedMemory.id),
                                style: { padding: '6px 12px', borderRadius: '6px', border: '1px solid #fee2e2', backgroundColor: '#fff', color: '#dc2626', fontSize: '12px', cursor: 'pointer' },
                                children: '削除'
                              })
                            ]
                          })
                        ]
                      }),
                      isEditing
                        ? jsx('div', {
                            key: 'edit-form',
                            children: [
                              jsx('div', {
                                key: 'cat',
                                style: { marginBottom: '16px' },
                                children: [
                                  jsx('label', { key: 'lbl', style: { display: 'block', fontSize: '12px', color: '#71717a', marginBottom: '4px' }, children: 'カテゴリ' }),
                                  jsx('select', {
                                    key: 'sel',
                                    value: formCategory,
                                    onChange: (e) => setFormCategory(e.target.value),
                                    style: { width: '100%', padding: '8px 12px', borderRadius: '6px', border: '1px solid #d4d4d8', fontSize: '13px' },
                                    children: CATEGORY_LIST.filter(c => c.id !== 'all').map(c => jsx('option', { key: c.id, value: c.id, children: c.label }))
                                  })
                                ]
                              }),
                              jsx('div', {
                                key: 'cnt',
                                style: { marginBottom: '16px' },
                                children: [
                                  jsx('label', { key: 'lbl', style: { display: 'block', fontSize: '12px', color: '#71717a', marginBottom: '4px' }, children: '記憶内容' }),
                                  jsx('textarea', {
                                    key: 'txt',
                                    rows: 6,
                                    value: formContent,
                                    onChange: (e) => setFormContent(e.target.value),
                                    style: { width: '100%', padding: '10px 12px', borderRadius: '6px', border: '1px solid #d4d4d8', fontSize: '13px', boxSizing: 'border-box' }
                                  })
                                ]
                              }),
                              jsx('div', {
                                key: 'btns',
                                style: { display: 'flex', gap: '8px' },
                                children: [
                                  jsx('button', {
                                    key: 'save',
                                    onClick: handleUpdate,
                                    disabled: saving || !formContent.trim(),
                                    style: { padding: '8px 16px', borderRadius: '6px', backgroundColor: '#18181b', color: '#fff', border: 'none', cursor: 'pointer' },
                                    children: saving ? '保存中...' : '更新'
                                  }),
                                  jsx('button', {
                                    key: 'cancel',
                                    onClick: () => setIsEditing(false),
                                    style: { padding: '8px 16px', borderRadius: '6px', backgroundColor: '#f4f4f5', color: '#3f3f46', border: '1px solid #e4e4e7', cursor: 'pointer' },
                                    children: 'キャンセル'
                                  })
                                ]
                              })
                            ]
                          })
                        : jsx('div', {
                            key: 'view-body',
                            children: [
                              jsx('div', {
                                key: 'content-box',
                                style: {
                                  padding: '16px',
                                  backgroundColor: '#fafafa',
                                  borderRadius: '8px',
                                  border: '1px solid #e4e4e7',
                                  fontSize: '14px',
                                  lineHeight: '1.6',
                                  color: '#18181b',
                                  whiteSpace: 'pre-wrap',
                                  marginBottom: '20px'
                                },
                                children: selectedMemory.content
                              }),
                              jsx('div', {
                                key: 'meta-info',
                                style: { borderTop: '1px solid #e4e4e7', paddingTop: '16px', display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' },
                                children: [
                                  jsx('div', {
                                    key: 'source',
                                    children: [
                                      jsx('span', { key: 'lbl', style: { fontSize: '11px', color: '#a1a1aa', display: 'block' }, children: 'ソース' }),
                                      jsx('span', { key: 'val', style: { fontSize: '12px', color: '#3f3f46' }, children: selectedMemory.source || 'manual' })
                                    ]
                                  }),
                                  jsx('div', {
                                    key: 'created',
                                    children: [
                                      jsx('span', { key: 'lbl', style: { fontSize: '11px', color: '#a1a1aa', display: 'block' }, children: '作成日時' }),
                                      jsx('span', { key: 'val', style: { fontSize: '12px', color: '#3f3f46' }, children: selectedMemory.created_at || '-' })
                                    ]
                                  })
                                ]
                              })
                            ]
                          })
                    ]
                  })
                : jsx('div', {
                    key: 'empty',
                    style: { padding: '48px 0', textAlign: 'center', color: '#a1a1aa', fontSize: '13px' },
                    children: '左側のリストから記憶を選択してください'
                  })
        ]
      })
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
