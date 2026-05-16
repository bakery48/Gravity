# AntiGravity Mobile Chat Bridge

スマホから同一LAN上のPCで動作している **Antigravity** のチャット欄へ
メッセージを送信し、Antigravity が出力する MD ファイルの更新差分を
スマホにリアルタイム表示するブリッジサーバーです。

**API キー不要** — Antigravity 本体に入力するため従量課金なし。

## 仕組み

```
 [スマホ]                [PC: server.py]              [Antigravity アプリ]
   │ メッセージ入力        │                                │
   ├─── POST /api/send ──>│                                │
   │                      │  ウィンドウを Activate         │
   │                      ├──── クリップボード貼付 ──────>│
   │                      ├──── Enter キー押下 ──────────>│
   │                      │                                │  処理中...
   │                      │                                ↓
   │                      │                          output.md に書き出し
   │                      │  500ms 監視で差分検知         │
   │ <─── SSE: md_update ─┤                                │
   │ 返答バブル表示        │                                │
```

## セットアップ (Windows)

### 1. リポジトリを取得
```cmd
git clone <repo>
cd Gravity
git checkout claude/mobile-antigravity-chat-c8q8d
```

### 2. 依存パッケージをインストール
```cmd
pip install -r requirements.txt
```

### 3. `config.json` を編集
```json
{
  "port": 8765,
  "output_md_path": "C:/path/to/output.md",
  "window_title_substring": "Antigravity",
  "send_key": "enter",
  "paste_delay_ms": 150
}
```

| 設定項目 | 説明 |
|---|---|
| `port` | サーバーポート（デフォルト 8765） |
| `output_md_path` | Antigravity が返答を書き出す MD ファイルのパス |
| `window_title_substring` | Antigravity のウィンドウタイトルに含まれる文字列 |
| `send_key` | 送信キー。`enter` または `ctrl+enter` |
| `paste_delay_ms` | ウィンドウアクティブ化後・貼付後の待機時間(ms) |

### 4. サーバー起動
```cmd
python server.py
```

```
=== AntiGravity Mobile Bridge ===
  PC         : http://localhost:8765
  スマホ (LAN): http://192.168.1.10:8765
  対象ウィンドウ: タイトルに 'Antigravity' を含むもの
  送信キー    : enter
  監視 MD     : C:\path\to\output.md
```

### 5. スマホから接続
PCと同じWi-Fiにつないだスマホのブラウザで `http://192.168.x.x:8765` を開く。

## 使い方

1. PC で **Antigravity を起動**しておく（チャット欄が表示された状態にしておくのが理想）
2. スマホでメッセージ入力 → 送信
3. PC側で Antigravity のウィンドウが自動的に手前に出てきて、メッセージが貼り付け＋Enter で送信される
4. Antigravity が返答を `output.md` に書き出す
5. スマホに自動でバブル表示される

## トラブルシュート

| 症状 | 対処 |
|---|---|
| スマホからアクセスできない | Windows ファイアウォールで TCP 8765 を許可。ルーターのAP分離をOFF |
| 「ウィンドウが見つかりません」エラー | `window_title_substring` を実際のウィンドウタイトルに合わせて変更 |
| 文字が一部しか入力されない | `paste_delay_ms` を 300〜500 に増やす |
| Enter が反応しない | `send_key` を `ctrl+enter` に変更 |
| MD 更新が反映されない | `output_md_path` のパスが正しいか、Antigravity が本当にそのファイルに書いているか確認 |

## ファイル構成

```
Gravity/
├── server.py          # ブリッジサーバー本体
├── config.json        # 設定
├── requirements.txt   # 依存パッケージ
└── static/
    └── index.html     # スマホ用 LINE 風 UI
```
