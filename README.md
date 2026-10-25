# SQLite Memory Plugin for Hermes Agent

[ English | [日本語](#japanese) ]

A zero-dependency, local persistent long-term memory plugin for Hermes Agent.  
Utilizes standard Python `sqlite3` and FTS5 full-text search to provide cross-session long-term memory and automatic context prefetching.  
Includes a dashboard API and a bundled Hermes Desktop GUI (Unified Package). The agent provider, the API, and the Desktop pane are enabled separately.

The product name is `Persistent Memory (SQLite)` in the plugin manifest, the settings schema, and the Desktop plugin export. Navigation stays short: the sidebar and dashboard tab are `Memory`. The recall indicator is `SQLite Memory`.

---

## ⚖️ Why SQLite FTS5? (vs External Vector DBs)

- **Daemonless & Container-Free**: No need to spin up or maintain external services like Qdrant, Milvus, or Redis. The store is a local SQLite database. WAL mode also creates `<db>-wal` and `<db>-shm` beside the `.db` file.
- **Ultra-Lightweight**: Uses standard SQLite FTS5 full-text indexing and BM25 ranking. Recall speed depends on the local database; this package does not claim a fixed latency.
- **Unified Package**: Bundles the Agent core logic, dashboard API, and Desktop GUI in one folder. Placing the folder does not by itself turn on the API or the Desktop pane.

---

## 📁 Directory Structure

```text
sqlite-memory/
├── __init__.py          # Plugin entrypoint (MemoryProvider implementation)
├── config_schema.py     # Declared settings schema for the Desktop panel
├── memory_schema.py     # Shared memories/FTS schema initialization
├── memory_stats.py      # Shared read-only counts and file sizes
├── plugin.yaml          # Plugin manifest
├── dashboard/           # Dashboard API (no built frontend in this package)
│   ├── manifest.json    # entry/css point at dist/index.js and dist/style.css, which are not shipped
│   └── plugin_api.py
├── desktop/             # Hermes Desktop GUI (Unified Package)
│   └── plugin.js
├── tests/               # Provider, dashboard, and desktop checks
└── README.md            # Documentation
```

---

## 🚀 Installation

### 1. Place the Plugin

Hermes searches for memory providers in this order: the bundled `plugins/memory/<name>/`, the active `$HERMES_HOME/plugins/<name>/`, project-local `./.hermes/plugins/<name>/` when `HERMES_ENABLE_PROJECT_PLUGINS` is enabled, then Python entry points in `hermes_agent.memory_providers`. An earlier source wins when names collide. For this plugin, the per-profile user-install path is `$HERMES_HOME/plugins/sqlite-memory/`. `$HERMES_HOME` is `~/.hermes` for the default profile and `~/.hermes/profiles/<profile>` for a named profile.

Default profile:

```bash
mkdir -p ~/.hermes/plugins

# Option A: Copy
cp -r ./sqlite-memory ~/.hermes/plugins/

# Option B: Symlink (Recommended)
ln -s "$(pwd)/sqlite-memory" ~/.hermes/plugins/sqlite-memory
```

Named profile — install into that profile's Hermes home. `~/.hermes/plugins/` is not scanned:

```bash
mkdir -p "$HERMES_HOME/plugins"
ln -s "$(pwd)/sqlite-memory" "$HERMES_HOME/plugins/sqlite-memory"
```

A copy under the running install's `plugins/memory/sqlite-memory/` is bundled: every profile can see it, and it wins over that profile's `$HERMES_HOME/plugins/sqlite-memory`. Use it only when you mean to ship a bundled provider. That directory is the installed `plugins/memory/` package, not `~/.hermes/hermes-agent/plugins/memory/`.

> [!TIP]
> **Unified Package:**
> Hermes copies `desktop/plugin.js` into the app-level `desktop-plugins/sqlite-memory/` (normally `~/.hermes/desktop-plugins/`, not a profile directory). That generated copy stays off until you enable it in **Settings → Plugins**. `defaultEnabled: true` in `plugin.js` applies to a standalone file, not to this generated copy.
> **Install only one Desktop copy.** Do not also copy this file under a second Desktop plugin directory (for example, `sqlite_memory`): every copy exports `id: 'sqlite-memory'`, regardless of its folder name, so Hermes rejects the later one as a duplicate. If you choose standalone installation instead, keep exactly one `sqlite-memory/plugin.js` under `~/.hermes/desktop-plugins/`. A standalone copy uses the file's `defaultEnabled: true` unless you have disabled it.
> If a duplicate-ID toast appears, disable/remove the extra source package or standalone copy (not only a generated unified-package copy, which can be recreated), then run **⌘K → Reload desktop plugins**.
>
> **Multi-profile note:** For a user-installed provider, each profile scans its own `$HERMES_HOME/plugins/`; a named profile does not scan `~/.hermes/plugins/`. A bundled provider with the same ID takes precedence over the profile copy. Project-local discovery is separately opt-in as described above. Symlink every profile that should track this checkout, or update each copy yourself. The Desktop pane is one app-level copy. Its version check compares that copy with the selected profile backend's `plugin_version`. A stale profile plugin can fail that check even when the default-profile install is current.

### 2. Configure Agent (`config.yaml`)

Add the following to the target profile's `config.yaml` (`~/.hermes/config.yaml` for the default profile, `~/.hermes/profiles/<profile>/config.yaml` for a named profile):

```yaml
plugins:
  enabled:
    - sqlite-memory  # required before /api/plugins/sqlite-memory/ is mounted; append to an existing list

memory:
  memory_enabled: true
  provider: sqlite-memory
  sqlite-memory: # (Both "sqlite-memory" and "sqlite_memory" are supported as config sections)
    # Optional: omit db_path to use the active profile's Hermes home/memory.db.
    # Set db_path only for a custom or intentionally shared database.
    # db_path: "/path/to/custom/memory.db"
    auto_extract: true
    max_recall: 5
```

`memory.provider: sqlite-memory` activates the agent provider. Listing `sqlite-memory` in `plugins.disabled` blocks that load. `plugins.enabled` is a separate gate: without it, a user install's dashboard and Desktop API are not mounted. The mounted path is `/api/plugins/sqlite-memory/` only. `sqlite_memory` is a config-section and toolset spelling, not an API mount.

The provider reads these settings from `config.yaml` only. The Desktop memory-provider settings panel writes `<hermes_home>/sqlite-memory/config.json`. It may set `memory.provider` in `config.yaml`, but it does not copy `db_path`, `auto_extract`, or `max_recall` there. Those panel edits do not change agent behavior.

The interactive `hermes memory setup` wizard saves provider-specific values under `memory.sqlite-memory` in `config.yaml`.

When `db_path` is omitted, the agent stores memories in `memory.db` under the active profile's Hermes home. An explicit `db_path` overrides this location; profiles configured with the same path share one database. For a named profile, the dashboard reads only that profile's `config.yaml` and falls back to its `memory.db`; it does not inherit `db_path` from the Hermes root config. The default/root profile continues to use the root `config.yaml`.

When `auto_extract` is enabled, only the first non-empty line is checked. It must be an explicit leading remember request (for example, `Please remember that ...`, `remember: ...`, or `覚えておいて：...`) or a preference directive (for example, `今後は日本語で答えて`). Later lines and quoted or pasted examples are ignored. The captured text is stored as category `preference`. Use `sqlite_remember` for explicit, unambiguous storage.

Agent `sqlite_search` (default 5) and `sqlite_list_memories` (default 10) clamp `limit` to 1–100. Prefetch uses `max_recall`, clamped to 5–100: values below 5 become 5, and values above 100 become 100. A bool or other non-numeric value falls back to 5. A numeric value is truncated with `int()` before that clamp. Dashboard `GET /memories` defaults to 50 and accepts 1–200. The Desktop page requests 100.

### 3. Restart Hermes

Restart the gateway, and start a new agent session, so the provider and API reload. If the Desktop pane is still absent, enable **sqlite-memory** in **Settings → Plugins**. After removing a duplicate Desktop copy, run **⌘K → Reload desktop plugins**.

---

## 🛠️ Provided Tools (Agent Tools)

| Tool | Description | Key Parameters |
| :--- | :--- | :--- |
| `sqlite_remember` | Stores a new fact, rule, or preference | `content` (required), `category` (default `general`) |
| `sqlite_search` | Keyword search: FTS5 BM25, then LIKE if FTS returns nothing | `query` (required), `limit` (default 5, clamped to 1–100) |
| `sqlite_forget` | Deletes a memory record by ID | `memory_id` (positive integer) |
| `sqlite_list_memories` | Lists memories, newest `updated_at` first, with an optional category filter | `category` (optional), `limit` (default 10, clamped to 1–100) |
| `sqlite_stats` | Returns memory count, category counts, last update time, database size, and WAL size | None |

For queries without Japanese kana/kanji, `sqlite_search` drops tokens shorter than 2 characters and keeps at most 10 terms before the FTS query. Japanese kana/kanji runs are split into overlapping 2–4-character fragments (up to 500), taken across the whole run rather than only from the start. A LIKE fallback runs only when FTS returns no rows.

---

## 💾 Database Inspection & Backup

By default, the agent stores memories in `memory.db` under the active profile's Hermes home. On the standard Unix layout, the default profile uses `~/.hermes/memory.db`, while a named profile uses `~/.hermes/profiles/<profile>/memory.db`. If `HERMES_HOME` is configured, use that profile's configured home. Connections use WAL, so `<db>-wal` and `<db>-shm` may sit beside the database.

Set `db_path` in that profile's `config.yaml` to use a custom location. Profiles whose agent config names the same path share one database. The dashboard follows the same profile-local rule: a named profile uses only its own `config.yaml`, then its own `memory.db` if `db_path` is absent.

```bash
# Default-profile example. For a named profile, point DB_PATH at that profile's Hermes home.
DB_PATH="${HERMES_HOME:-$HOME/.hermes}/memory.db"

# Inspect the 10 newest rows by id
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
ダッシュボード API と Hermes Desktop 用 GUI（Unified Package）を同梱しています。エージェント本体、API、Desktop 画面は別々に有効化します。

製品名は `Persistent Memory (SQLite)` です。プラグインマニフェスト、設定スキーマ、Desktop プラグインの export がこの名前です。ナビは短く、サイドバーとダッシュボードタブは `Memory` です。想起インジケータは `SQLite Memory` です。

---

## ⚖️ なぜ外部ベクトルDBではなく SQLite FTS5 なのか？

- **デーモン・コンテナ不要**: Qdrant や Milvus、Redis 等の外部サーバーを常駐させる必要がありません。保存先はローカルの SQLite データベースです。WAL モードのため、`.db` の隣に `<db>-wal` と `<db>-shm` ができます。
- **超軽量・省メモリ**: Python 標準の SQLite FTS5 で全文索引を作り、BM25 で順位付けします。想起速度はローカル DB に依存し、固定のミリ秒値は保証しません。
- **Unified Package**: Agent コアロジック、ダッシュボード API、Desktop 用 GUI が 1 つのディレクトリに入っています。配置しただけでは API も Desktop 画面も有効になりません。

---

## 📁 構成ファイル

```text
sqlite-memory/
├── __init__.py          # プラグイン本体 (MemoryProvider 実装)
├── config_schema.py     # Desktop 設定パネル向けの declared schema
├── memory_schema.py     # Agent / Dashboard 共通のDB・FTSスキーマ初期化
├── memory_stats.py      # 件数とファイルサイズの共通読み取り
├── plugin.yaml          # プラグイン定義マニフェスト
├── dashboard/           # ダッシュボード API（このパッケージにビルド済みフロントエンドはない）
│   ├── manifest.json    # entry/css は未同梱の dist/index.js と dist/style.css を指す
│   └── plugin_api.py
├── desktop/             # Hermes Desktop 向け GUI (Unified Package)
│   └── plugin.js
├── tests/               # プロバイダ、ダッシュボード、Desktop のテスト
└── README.md            # 本ドキュメント
```

---

## 🚀 導入方法 (Installation)

### 1. プラグインの配置

Hermes はメモリプロバイダを次の順に探索します。同梱 `plugins/memory/<name>/` → 現在の `$HERMES_HOME/plugins/<name>/` → `HERMES_ENABLE_PROJECT_PLUGINS` が有効な場合のプロジェクト内 `./.hermes/plugins/<name>/` → `hermes_agent.memory_providers` の Python entry point。同名なら先に見つかったものが優先されます。このプラグインをプロファイルごとにユーザー配置する場合のパスは `$HERMES_HOME/plugins/sqlite-memory/` です。`$HERMES_HOME` は、default プロファイルでは `~/.hermes`、名前付きプロファイルでは `~/.hermes/profiles/<profile>` です。

default プロファイル:

```bash
mkdir -p ~/.hermes/plugins

# コピーして配置する場合
cp -r ./sqlite-memory ~/.hermes/plugins/

# またはシンボリックリンクで配置する場合（推奨）
ln -s "$(pwd)/sqlite-memory" ~/.hermes/plugins/sqlite-memory
```

名前付きプロファイルは、そのプロファイルの Hermes home に置いてください。`~/.hermes/plugins/` は探索されません。

```bash
mkdir -p "$HERMES_HOME/plugins"
ln -s "$(pwd)/sqlite-memory" "$HERMES_HOME/plugins/sqlite-memory"
```

実行中のインストールの `plugins/memory/sqlite-memory/` に置いたコピーは同梱扱いになります。全プロファイルから見え、各プロファイルの `$HERMES_HOME/plugins/sqlite-memory` より優先されます。同梱プロバイダとして配る意図があるときだけ使ってください。そのディレクトリはインストール済みの `plugins/memory/` であり、`~/.hermes/hermes-agent/plugins/memory/` ではありません。

> [!TIP]
> **Unified Package について:**
> Hermes は `desktop/plugin.js` をアプリ共通の `desktop-plugins/sqlite-memory/`（通常は `~/.hermes/desktop-plugins/`。プロファイル配下ではない）へコピーします。この生成コピーは **Settings → Plugins** で有効にするまでオフです。`plugin.js` の `defaultEnabled: true` は standalone のファイルにだけ効き、生成コピーには効きません。
> **Desktop 用コピーは 1 つだけにしてください。** 同じファイルを `sqlite_memory` など別名の Desktop プラグインフォルダにも配置しないでください。フォルダ名にかかわらず各コピーの ID は `sqlite-memory` のため、重複すると後続の読み込みが拒否されます。Standalone 方式を選ぶ場合は、`~/.hermes/desktop-plugins/` 内に `sqlite-memory/plugin.js` を 1 つだけ配置してください。standalone は、無効化していなければファイル自身の `defaultEnabled: true` で読み込まれます。
> duplicate-ID の通知が出た場合は、生成済みコピーだけでなく重複元の package または standalone コピーも無効化して 1 つにし、**⌘K → Reload desktop plugins** を実行してください。
>
> **マルチプロファイル利用時の注意:** ユーザー配置プロバイダの探索先は各プロファイル自身の `$HERMES_HOME/plugins/` です。名前付きプロファイルは `~/.hermes/plugins/` を探索しません。同名の場合は共有の同梱プロバイダがプロファイル側のコピーより優先されます。プロジェクト内プロバイダの探索は上記のとおり別途 opt-in です。この checkout を追うプロファイルにはそれぞれシンボリックリンクを置くか、コピーを個別に更新してください。Desktop 画面はアプリ共通の 1 コピーです。バージョン確認は、そのコピーと選択中プロファイルのバックエンド `plugin_version` を比べます。default 側が新しくても、プロファイル側が古いとこの確認に失敗します。

### 2. 設定ファイル (`config.yaml`) の編集

対象プロファイルの `config.yaml`（default は `~/.hermes/config.yaml`、名前付きは `~/.hermes/profiles/<profile>/config.yaml`）に以下を追記します。

```yaml
plugins:
  enabled:
    - sqlite-memory  # /api/plugins/sqlite-memory/ をマウントする前に必要。既存リストへ追加する

memory:
  memory_enabled: true
  provider: sqlite-memory
  sqlite-memory: # (設定セクションとしては "sqlite-memory" と "sqlite_memory" の両記法に対応)
    # db_path は省略可能。省略時は現在のプロファイルの Hermes home 配下に保存されます。
    # カスタム／共有 DB を使う場合のみ db_path を指定してください。
    # db_path: "/path/to/custom/memory.db"
    auto_extract: true
    max_recall: 5
```

`memory.provider: sqlite-memory` でエージェントのプロバイダが有効になります。`plugins.disabled` に `sqlite-memory` があると、その読み込みは止まります。`plugins.enabled` は別の条件です。入っていないと、ユーザーインストールのダッシュボード／Desktop API はマウントされません。マウントされるパスは `/api/plugins/sqlite-memory/` だけです。`sqlite_memory` は設定セクション名と toolset 名であり、API のマウント名ではありません。

プロバイダが読むのは `config.yaml` だけです。Desktop のメモリプロバイダ設定パネルは `<hermes_home>/sqlite-memory/config.json` に書きます。`config.yaml` の `memory.provider` は更新することがありますが、`db_path`、`auto_extract`、`max_recall` はコピーしません。これらのパネル編集はエージェントの動作に反映されません。

対話型の `hermes memory setup` は、プロバイダ固有の値を `config.yaml` の `memory.sqlite-memory` に保存します。

`db_path` を省略すると、エージェントは現在のプロファイルの Hermes home 直下に `memory.db` を作ります。`db_path` を明示すると保存先が上書きされ、複数プロファイルのエージェント設定が同じパスなら同じ DB を共有します。名前付きプロファイルのダッシュボード API は、そのプロファイル自身の `config.yaml` だけを読み、`db_path` が無ければそのプロファイルの `memory.db` を使います。Hermes root の `config.yaml` にある `db_path` は引き継ぎません。default/root プロファイルでは引き続き root の `config.yaml` を使います。

`auto_extract` を有効にすると、各発言の先頭の空でない1行だけを検査します。`Please remember that ...`、`remember: ...`、`覚えておいて：...` のような明示的な記憶指示、または `今後は日本語で答えて` のような応答設定指示に一致した場合だけ保存します。後続行や引用・貼り付けた例は対象外です。保存先は `preference` カテゴリです。確実に保存する場合は `sqlite_remember` を使用してください。

エージェントの `sqlite_search`（既定 5）と `sqlite_list_memories`（既定 10）は `limit` を 1〜100 に切り詰めます。Prefetch が使う `max_recall` は 5〜100 です。5 未満は 5、100 超は 100 になります。bool や数値でない値は 5 に戻り、数値は `int()` で切り捨ててからこの範囲に合わせます。ダッシュボードの `GET /memories` は既定 50、受け付ける範囲は 1〜200 です。Desktop 画面は 100 件を要求します。

### 3. Hermes の再起動

プロバイダと API を読み直すため、gateway を再起動し、エージェントのセッションを新しく開始してください。Desktop 画面が出ない場合は **Settings → Plugins** で **sqlite-memory** を有効にしてください。重複した Desktop コピーを外したあとは **⌘K → Reload desktop plugins** を実行してください。

---

## 🛠️ 提供されるツール (Agent Tools)

| ツール名 | 説明 | 主要引数 |
| :--- | :--- | :--- |
| `sqlite_remember` | 新しい事実や設定・ルールを記憶する | `content`（必須）, `category`（既定 `general`） |
| `sqlite_search` | キーワード検索。FTS5 の BM25 のあと、0 件なら LIKE | `query`（必須）, `limit`（既定 5、1〜100） |
| `sqlite_forget` | 不要な記憶を ID 指定で削除する | `memory_id`（正の整数） |
| `sqlite_list_memories` | `updated_at` の新しい順に一覧する。カテゴリ絞り込み可 | `category`（任意）, `limit`（既定 10、1〜100） |
| `sqlite_stats` | 記憶件数・カテゴリ別件数・最終更新日時・DB本体サイズ・WALサイズを返す | なし |

仮名・漢字を含まない検索では、`sqlite_search` は 2 文字未満の語を捨て、FTS に最大 10 語を渡します。日本語の仮名・漢字の連続部分は、重複する 2〜4 文字の断片（最大 500 個）に分けます。長い文でも先頭だけで切らず、文全体から取ります。LIKE へのフォールバックは FTS が 0 件のときだけです。

---

## 💾 データベースの確認・バックアップ

エージェントはデフォルトで、現在のプロファイルの Hermes home 直下にある `memory.db` に保存します。標準的な Unix 配置では、default プロファイルは `~/.hermes/memory.db`、名前付きプロファイルは `~/.hermes/profiles/<profile>/memory.db` です。`HERMES_HOME` を設定している場合は、そのプロファイルの Hermes home を使用してください。接続は WAL なので、データベースの隣に `<db>-wal` と `<db>-shm` ができることがあります。

カスタムの保存先にするには、そのプロファイルの `config.yaml` に `db_path` を書きます。エージェント設定が同じパスのプロファイルは DB を共有します。ダッシュボードも同じプロファイル内の設定を使い、`db_path` が無ければそのプロファイル自身の `memory.db` にフォールバックします。

```bash
# default プロファイルの例。名前付きプロファイルでは DB_PATH をその Hermes home に変更してください。
DB_PATH="${HERMES_HOME:-$HOME/.hermes}/memory.db"

# id が新しい 10 行を確認
sqlite3 "$DB_PATH" "SELECT id, category, content, created_at FROM memories ORDER BY id DESC LIMIT 10;"

# WAL にコミット済みの内容も含めて整合性のあるバックアップを作成
BACKUP_PATH="${DB_PATH%.db}_backup.db"
python3 -c 'import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()' "$DB_PATH" "$BACKUP_PATH"
```
