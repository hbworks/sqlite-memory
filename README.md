# SQLite Memory Plugin for Hermes Agent

[ English | [日本語](#japanese) ]

A zero-dependency, local persistent long-term memory plugin for Hermes Agent.  
Utilizes standard Python `sqlite3` and FTS5 full-text search to provide cross-session long-term memory and automatic context prefetching.  
Includes Web dashboard API definitions as well as a bundled Hermes Desktop GUI (Unified Package).

---

## ⚖️ Why SQLite FTS5? (vs External Vector DBs)

- **Daemonless & Container-Free**: No need to spin up or maintain external services like Qdrant, Milvus, or Redis. Operates entirely out of a single local `.db` file.
- **Ultra-Lightweight & Fast**: Uses standard SQLite FTS5 full-text indexing, achieving sub-millisecond keyword and BM25-ranked memory recall with negligible RAM usage.
- **Unified Package**: Bundles the Agent core logic, Web Dashboard API, and Desktop GUI in a single folder—installed and recognized automatically with zero configuration.

---

## 📁 Directory Structure

```text
sqlite-memory/
├── __init__.py          # Plugin entrypoint (MemoryProvider implementation)
├── config_schema.py     # Schema definition for UI & settings
├── plugin.yaml          # Plugin manifest
├── dashboard/           # Web dashboard definition & API
│   ├── manifest.json
│   └── plugin_api.py
├── desktop/             # Hermes Desktop GUI plugin (Unified Package)
│   └── plugin.js
└── README.md            # Documentation
```

---

## 🚀 Installation

### 1. Place the Plugin

Install into Hermes Agent's plugin directory `~/.hermes/plugins/`:

```bash
mkdir -p ~/.hermes/plugins

# Option A: Copy
cp -r ./sqlite-memory ~/.hermes/plugins/

# Option B: Symlink (Recommended)
ln -s "$(pwd)/sqlite-memory" ~/.hermes/plugins/sqlite-memory
```

> [!TIP]
> **Unified Package:**
> Because the desktop GUI is bundled under `~/.hermes/plugins/sqlite-memory/desktop/plugin.js`, Hermes Desktop automatically detects and loads the memory GUI without requiring manual separate installation.
> (If you prefer standalone desktop installation, you can also place `desktop/plugin.js` directly under `~/.hermes/desktop-plugins/sqlite-memory/plugin.js`).

※ To install as a core/system-wide bundle across all profiles:
```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/sqlite-memory
cp -r ./sqlite-memory/* ~/.hermes/hermes-agent/plugins/memory/sqlite-memory/
```

### 2. Configure Agent (`config.yaml`)

Add the following to `~/.hermes/config.yaml` or your profile's `config.yaml`:

```yaml
memory:
  memory_enabled: true
  provider: sqlite-memory
  sqlite-memory:
    db_path: "~/.hermes/memory.db"
    auto_extract: true
    max_recall: 5
```

### 3. Restart Hermes

Restart Hermes Agent to activate the plugin.

---

## 🛠️ Provided Tools (Agent Tools)

| Tool | Description | Key Parameters |
| :--- | :--- | :--- |
| `sqlite_remember` | Stores a new fact, rule, or preference | `content` (memory body), `category` (category tag) |
| `sqlite_search` | Keyword and full-text search (FTS5) over memories | `query` (search terms), `limit` (max results) |
| `sqlite_forget` | Deletes a memory record by ID | `memory_id` (memory ID to remove) |
| `sqlite_list_memories` | Lists recent memories with optional category filter | `category` (filter category), `limit` (count) |

---

## 💾 Database Inspection & Backup

Memories are stored in `~/.hermes/memory.db` as standard SQLite:

```bash
# Inspect the 10 most recent memories
sqlite3 ~/.hermes/memory.db "SELECT id, category, content, created_at FROM memories ORDER BY id DESC LIMIT 10;"

# Create a manual backup
cp ~/.hermes/memory.db ~/.hermes/memory_backup.db
```

<br>

---
<a id="japanese"></a>

# SQLite Memory Plugin for Hermes Agent (日本語)

[ [English](#sqlite-memory-plugin-for-hermes-agent) | 日本語 ]

Hermes Agent 向けのローカル完結型・ゼロ依存の永続メモリプラグインです。  
Python 標準の `sqlite3` と FTS5 全文検索を用いて、セッションをまたいだ長期記憶と自動想起 (Prefetch) を提供します。  
Hermes Web ダッシュボード用の API に加え、Hermes Desktop 用の GUI プラグイン（Unified Package）も同梱されています。

---

## ⚖️ なぜ外部ベクトルDBではなく SQLite FTS5 なのか？

- **デーモン・コンテナ不要**: Qdrant や Milvus、Redis 等の外部サーバーを常駐させる必要がなく、単一のローカル `.db` ファイルのみで完結。
- **超軽量・省メモリ・ミリ秒検索**: Python 標準の SQLite FTS5（全文検索エンジン）を活用し、メモリフットプリントを最小限に抑えながら BM25 スコアによる高速想起を実現。
- **Unified Package（ワンストップ導入）**: Agent コアロジック、Web ダッシュボード API、Desktop 用 GUI が 1 つのディレクトリにパッケージ化されており、配置するだけで即座に GUI 付きで動作。

---

## 📁 構成ファイル

```text
sqlite-memory/
├── __init__.py          # プラグイン本体 (MemoryProvider 実装)
├── config_schema.py     # UI/設定定義スキーマ
├── plugin.yaml          # プラグイン定義マニフェスト
├── dashboard/           # Webダッシュボード用定義 & API
│   ├── manifest.json
│   └── plugin_api.py
├── desktop/             # Hermes Desktop 向け GUI プラグイン (Unified Package)
│   └── plugin.js
└── README.md            # 本ドキュメント
```

---

## 🚀 導入方法 (Installation)

### 1. プラグインの配置

Hermes Agent のプラグインディレクトリ `~/.hermes/plugins/` に配置します。

```bash
mkdir -p ~/.hermes/plugins

# コピーして配置する場合
cp -r ./sqlite-memory ~/.hermes/plugins/

# またはシンボリックリンクで配置する場合（推奨）
ln -s "$(pwd)/sqlite-memory" ~/.hermes/plugins/sqlite-memory
```

> [!TIP]
> **Unified Package について:**
> `~/.hermes/plugins/sqlite-memory/desktop/plugin.js` に Desktop 用 GUI が同梱されているため、Hermes Desktop は自動的にこの GUI を認識します。  
> （個別に Desktop プラグインとして配置したい場合も、本フォルダ内の `desktop/plugin.js` を `~/.hermes/desktop-plugins/sqlite-memory/plugin.js` に配置することで利用可能です）

※ バンドル標準（全プロファイル共通）として配置したい場合：
```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/sqlite-memory
cp -r ./sqlite-memory/* ~/.hermes/hermes-agent/plugins/memory/sqlite-memory/
```

### 2. 設定ファイル (`config.yaml`) の編集

`~/.hermes/config.yaml`（または使用するプロファイルの `config.yaml`）の `memory:` セクションに以下を追記します：

```yaml
memory:
  memory_enabled: true
  provider: sqlite-memory
  sqlite-memory:
    db_path: "~/.hermes/memory.db"
    auto_extract: true
    max_recall: 5
```

### 3. Hermes の再起動

Hermes Agent を起動・再起動すれば自動的に有効化されます。

---

## 🛠️ 提供されるツール (Agent Tools)

| ツール名 | 説明 | 主要引数 |
| :--- | :--- | :--- |
| `sqlite_remember` | 新しい事実や設定・ルールを記憶する | `content`（記憶内容）, `category`（カテゴリ） |
| `sqlite_search` | 記憶をキーワード/全文検索する | `query`（検索語句）, `limit`（最大件数） |
| `sqlite_forget` | 不要な記憶をID指定で削除する | `memory_id`（記憶ID） |
| `sqlite_list_memories` | 最近保存された記憶の一覧を取得する | `category`（絞り込み）, `limit`（件数） |

---

## 💾 データベースの確認・バックアップ

記憶データはデフォルトで `~/.hermes/memory.db` に SQLite 形式で保存されます。

```bash
# 記憶の一覧を確認
sqlite3 ~/.hermes/memory.db "SELECT id, category, content, created_at FROM memories ORDER BY id DESC LIMIT 10;"

# バックアップの作成
cp ~/.hermes/memory.db ~/.hermes/memory_backup.db
```
