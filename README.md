# SQLite Memory Plugin for Hermes Agent

Hermes Agent 向けのローカル完結型・ゼロ依存の永続メモリプラグインです。
Python 標準の `sqlite3` と FTS5 全文検索を用いて、セッションをまたいだ長期記憶と自動想起 (Prefetch) を提供します。
Hermes Web ダッシュボード用の API に加え、Hermes Desktop 用の GUI プラグイン（Unified Package）も同梱されています。

---

## 📁 構成ファイル

```text
sqlite_memory/
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
cp -r ./sqlite_memory ~/.hermes/plugins/

# またはシンボリックリンクで配置する場合（推奨）
ln -s "$(pwd)/sqlite_memory" ~/.hermes/plugins/sqlite_memory
```

> [!TIP]
> **Unified Package について:**
> `~/.hermes/plugins/sqlite_memory/desktop/plugin.js` に Desktop 用 GUI が同梱されているため、Hermes Desktop は自動的にこの GUI を認識します。
> （個別に Desktop プラグインとして配置したい場合は `sqlite-memory-gui/` または本フォルダ内の `desktop/plugin.js` を `~/.hermes/desktop-plugins/sqlite_memory/plugin.js` に配置することも可能です）

※ バンドル標準（全プロファイル共通）として配置したい場合：
```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/sqlite_memory
cp -r ./sqlite_memory/* ~/.hermes/hermes-agent/plugins/memory/sqlite_memory/
```

### 2. 設定ファイル (`config.yaml`) の編集

`~/.hermes/config.yaml`（または使用するプロファイルの `config.yaml`）の `memory:` セクションに以下を追記します：

```yaml
memory:
  memory_enabled: true
  provider: sqlite_memory
  sqlite_memory:
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
