# SQLite Memory Plugin for Hermes Agent

Hermes Agent 向けのローカル完結型・ゼロ依存の永続メモリプラグインです。
Python 標準の `sqlite3` と FTS5 全文検索を用いて、セッションをまたいだ長期記憶と自動想起 (Prefetch) を提供します。

---

## 📁 構成ファイル

```text
sqlite_memory/
├── __init__.py          # プラグイン本体 (MemoryProvider 実装)
├── config_schema.py     # UI/設定定義スキーマ
├── plugin.yaml          # プラグイン定義マニフェスト
├── dashboard/           # Webダッシュボード / API定義
│   ├── manifest.json
│   └── plugin_api.py
└── README.md            # 本ドキュメント
```

---

## 🚀 別PCへの導入方法 (Installation)

### 1. プラグインの配置
ZIPを解凍し、フォルダ `sqlite_memory` を以下のパスに配置します：

```bash
mkdir -p ~/.hermes/plugins
cp -r sqlite_memory ~/.hermes/plugins/
```

※ 全プロファイルやバンドル標準として使いたい場合は以下にも配置できます：
```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/sqlite_memory
cp -r sqlite_memory/* ~/.hermes/hermes-agent/plugins/memory/sqlite_memory/
```

### 2. 設定ファイル (`config.yaml`) の編集
`~/.hermes/config.yaml`（または使用しているプロファイルの `config.yaml`）の `memory:` セクションに以下を追記します：

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
Hermes Agent を起動・再起動すれば、自動的に有効化されます。

---

## 🛠️ 提供されるツール (Agent Tools)

| ツール名 | 説明 |
| :--- | :--- |
| `sqlite_remember` | 新しい事実や設定・ルールを記憶する (`content`, `category`) |
| `sqlite_search` | 記憶をキーワード/全文検索する (`query`, `limit`) |
| `sqlite_forget` | 不要な記憶をID指定で削除する (`memory_id`) |
| `sqlite_list_memories` | 最近保存された記憶の一覧を取得する (`category`, `limit`) |

---

## 💾 データベースの確認・バックアップ
記憶データは `~/.hermes/memory.db` に保存されます。

```bash
# 記憶の一覧を確認
sqlite3 ~/.hermes/memory.db "SELECT id, category, content, created_at FROM memories;"
```
