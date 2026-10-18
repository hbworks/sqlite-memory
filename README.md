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
> **Install only one Desktop copy.** Do not also copy this file under a second Desktop plugin directory (for example, `sqlite_memory`): every copy exports `id: 'sqlite-memory'`, regardless of its folder name, so Hermes rejects the later one as a duplicate. If you choose standalone installation instead, keep exactly one `sqlite-memory/plugin.js` under `~/.hermes/desktop-plugins/`.
> If a duplicate-ID toast appears, disable/remove the extra source package or standalone copy (not only a generated unified-package copy, which can be recreated), then run **⌘K → Reload desktop plugins**.

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
  sqlite-memory: # (Both "sqlite-memory" and "sqlite_memory" are supported)
    # Optional: omit db_path to use the active profile's Hermes home/memory.db.
    # Set db_path only for a custom or intentionally shared database.
    # db_path: "/path/to/custom/memory.db"
    auto_extract: true
    max_recall: 5
```

When `db_path` is omitted, memories are stored in `memory.db` under the active profile's Hermes home. An explicit `db_path` overrides this location; profiles configured with the same path share one database.

When `auto_extract` is enabled, only the first non-empty line of a user message is checked, and the entire line must match an explicit English or Japanese memory directive (for example, `Please remember that ...`). Trigger phrases in later lines or pasted examples are ignored. Use `sqlite_remember` for explicit, unambiguous storage.

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
| `sqlite_stats` | Returns memory count, category counts, last update time, database size, and WAL size | None |

---

## 💾 Database Inspection & Backup

By default, memories are stored in `memory.db` under the active profile's Hermes home. On the standard Unix layout, the default profile uses `~/.hermes/memory.db`, while a named profile uses `~/.hermes/profiles/<profile>/memory.db`. If `HERMES_HOME` is configured, use that profile's configured home.

Set `db_path` explicitly to use a custom location. Profiles configured with the same path will share one database.

```bash
# Default-profile example. For a named profile, point DB_PATH at that profile's Hermes home.
DB_PATH="$HOME/.hermes/memory.db"

# Inspect the 10 most recent memories
sqlite3 "$DB_PATH" "SELECT id, category, content, created_at FROM memories ORDER BY id DESC LIMIT 10;"

# Create a consistent backup, including committed WAL contents
BACKUP_PATH="${DB_PATH%.db}_backup.db"
python3 -c 'import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()' "$DB_PATH" "$BACKUP_PATH"
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
> **Desktop 用コピーは 1 つだけにしてください。** 同じファイルを `sqlite_memory` など別名の Desktop プラグインフォルダにも配置しないでください。フォルダ名にかかわらず各コピーの ID は `sqlite-memory` のため、重複すると後続の読み込みが拒否されます。Standalone 方式を選ぶ場合は、`~/.hermes/desktop-plugins/` 内に `sqlite-memory/plugin.js` を 1 つだけ配置してください。
> duplicate-ID の通知が出た場合は、生成済みコピーだけでなく重複元の package または standalone コピーも無効化して 1 つにし、**⌘K → Reload desktop plugins** を実行してください。

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
  sqlite-memory: # (互換性のため "sqlite-memory" と "sqlite_memory" の両記法に対応)
    # db_path は省略可能。省略時は現在のプロファイルの Hermes home 配下に保存されます。
    # カスタム／共有 DB を使う場合のみ db_path を指定してください。
    # db_path: "/path/to/custom/memory.db"
    auto_extract: true
    max_recall: 5
```

`db_path` を省略すると、現在のプロファイルの Hermes home 直下に `memory.db` が作成されます。`db_path` を明示すると保存先が上書きされ、複数プロファイルに同じパスを指定した場合は同じ DB を共有します。

`auto_extract` を有効にしても、各ユーザー発言の先頭の空でない1行だけを検査し、その行全体が英語または日本語の明示的な記憶指示に一致した場合に限り保存します。後続行の引用や貼り付けた例に含まれる語句は抽出されません。確実に保存する場合は `sqlite_remember` を使用してください。

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
| `sqlite_stats` | 記憶件数・カテゴリ別件数・最終更新日時・DB本体サイズ・WALサイズを返す | なし |

---

## 💾 データベースの確認・バックアップ

記憶データはデフォルトで、現在のプロファイルの Hermes home 直下にある `memory.db` に保存されます。標準的な Unix 配置では、default プロファイルは `~/.hermes/memory.db`、名前付きプロファイルは `~/.hermes/profiles/<profile>/memory.db` です。`HERMES_HOME` を設定している場合は、そのプロファイルの Hermes home を使用してください。

`db_path` を明示すると、この既定の保存先を上書きします。複数プロファイルに同じパスを指定した場合は DB を共有します。

```bash
# default プロファイルの例。名前付きプロファイルでは DB_PATH をその Hermes home に変更してください。
DB_PATH="$HOME/.hermes/memory.db"

# 記憶の一覧を確認
sqlite3 "$DB_PATH" "SELECT id, category, content, created_at FROM memories ORDER BY id DESC LIMIT 10;"

# WAL にコミット済みの内容も含めて整合性のあるバックアップを作成
BACKUP_PATH="${DB_PATH%.db}_backup.db"
python3 -c 'import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()' "$DB_PATH" "$BACKUP_PATH"
```
