# AntiGravity Mobile Chat Bridge

スマホから同一LAN上のAntiGravityチャットへアクセスし、
レスポンスをMDファイルに記録・表示するシステムです。

## セットアップ

### 1. 依存パッケージのインストール（PC側）

```bash
pip install -r requirements.txt
```

### 2. config.json を編集

```json
{
  "port": 8765,
  "output_md_path": "./output.md",
  "anthropic_api_key": "sk-ant-xxxxxxxxxxxx",
  "model": "claude-sonnet-4-6",
  "system_prompt": "あなたは親切なAIアシスタントです。日本語で丁寧に回答してください。"
}
```

| 設定項目 | 説明 |
|---|---|
| `port` | サーバーのポート番号（デフォルト: 8765） |
| `output_md_path` | レスポンスを書き出すMDファイルのパス |
| `anthropic_api_key` | Anthropic APIキー。または環境変数 `ANTHROPIC_API_KEY` |
| `model` | 使用するClaudeモデル |
| `system_prompt` | AIへのシステムプロンプト |

### 3. サーバー起動（PC側）

```bash
python server.py
```

起動すると、スマホからアクセスするURLが表示されます:

```
=== AntiGravity Mobile Bridge ===
  PC上のURL    : http://localhost:8765
  スマホからのURL: http://192.168.1.10:8765
  出力MDファイル  : /path/to/output.md
```

### 4. スマホから接続

表示された `http://192.168.1.x:8765` にスマホのブラウザでアクセス。

## 機能

- **チャット**: スマホからメッセージを送信、AIの返答がリアルタイムで表示
- **MDファイル**: 会話ログが `output_md_path` のMDファイルに自動保存
- **MD表示**: 右上の「MD」ボタンで現在のMDファイルの内容を確認
- **会話リセット**: 「リセット」ボタンで会話履歴とMDファイルをクリア
- **自動再接続**: ネットワーク切断時に自動で再接続

## ファイル構成

```
Gravity/
├── server.py          # メインサーバー（AntiGravityロジック込み）
├── config.json        # 設定ファイル
├── requirements.txt   # Pythonパッケージ
├── output.md          # 会話ログ（自動生成）
└── static/
    └── index.html     # スマホ用チャットUI
```
