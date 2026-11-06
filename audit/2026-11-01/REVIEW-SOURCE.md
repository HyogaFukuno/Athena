# Athena ソース精査 — 2026-11-01

## 1. 結論

- **すぐ直すべき致命的な不具合は見つかりませんでした。** 10/29〜10/31 の監査で挙がった P1（設定移行・ワールド破棄・装飾の後始末・ログイン重複）はパッチ 0355〜0367 で直っていることを確認しました。
- 新しく見つけた中で優先度が高いのは、次の 4 件です。
  1. **自動入れ替えスクリプトが、試合を通さない短い AOT 記録を作る**（M1）。10/31 に測った「試合で学習した AOT」の効果（最初の参加 219 → 41 ms）を、デプロイのたびに取りこぼすおそれがあります。
  2. **/disguise の本名がチャットの Tab 補完で漏れる**（M2）。
  3. **AthenaGuard の猶予が ping に比例して無制限に伸びる**（M3）。enforce を有効にした時点で抜け道になります。
  4. **`pandaspigot.yml.bak` に BungeeGuard のトークンが残り、パーミッションが元より緩くなり得る**（M4）。
- 低優先度の項目は、スレッド前提の抜け・失敗時の挙動・ドキュメントの古さ・パッチ列の整理が中心です。

## 2. 対象と深さ

- ルート `perf/java25-audit-2026-10-30`（`d2c89f8`）、生成 Server ツリー `fd8b5723c`（パッチ 0383 まで）。
- Athena の差分は、0137（MCP 名への置き換え）以降で **297 ファイル、+18,581 / −1,934 行**、うち新規 43 ファイル・9,380 行。
- **通読したもの**: `AthenaBungeeGuard`、`HandshakeListener`/`LoginListener` の差分、`AthenaLegacy17`・`AthenaLegacy17Decoder`、`AthenaLegacy17Encoder`（入口と状態管理）、`AthenaGuard`（設定・timer・攻撃・違反集計）、`athena.server` パッケージ全体（API 実装・設定・コマンド）、`AthenaTeleportQueue`、`AthenaChunkPreloader`、`AthenaBossBar`、`PacketDecompressor`、`NetworkManager` の hand-off 部分、`AthenaSectionStore`（open・build・slim 読み込み）、`AthenaTemplateDataManager`、`CraftServer.loadTemplateWorld`、disguise の送信経路、`deploy/` の起動スクリプトと unit、CI。
- **詳しく見ていないもの**: `World`/`Chunk`/`ChunkSection` などの大きな最適化差分（前回監査で経路を確認済み）、`AthenaGuard` の物理モデル本体（`evaluate` 以下の境界値）、`AthenaLegacy17Encoder` の個々のパケット変換。ここに指摘がないことは、正しさの保証ではありません。
- 今回はコードを変更していません。テストも実行していません（前回 6,514 件成功から、ソースの変更は 0368〜0383 のみ）。再現テストを書いた項目はありません。各項目に「確認の種類」を記します。

## 3. レベルの定義

| レベル | 意味 |
|---|---|
| 高 | 本番で今すぐ壊れる・データや認証を失う。今回は該当なし |
| 中 | 条件付きで本番に影響する、または機能の目的を損なう。次の作業で直す |
| 低 | 起きにくい・影響が小さい・堅牢化。まとめて直せばよい |
| 保守 | 不具合ではないが、運用や今後の変更のコストを上げているもの |

## 4. 中

### M1 — 自動入れ替えが、試合を通さない AOT 記録を作る

- 場所: [athena_sg_when_empty.sh:19](deploy/athena_sg_when_empty.sh)、[start-small.sh:140](deploy/start-small.sh)
- 確認: コードと 10/31 の計測記録（`audit/2026-10-31/TICK-SPIKES.md` §3.1・§4.2）の突き合わせ。本番では未確認。
- 入れ替えスクリプトは、新しい jar で起動したあと **25 秒待って、空なら止めて再起動**します。jar が新しければキーも新しいので、この 25 秒の起動が `record-<server>.aotconf` を書きます。次の起動で `start-small.sh` は **一番新しい record** から共有キャッシュを作ります。
- TICK-SPIKES は「起動と停止だけの記録では、ログインの約 800 クラスと最初のマップの約 150 クラスが入らず、最初の参加が 115〜219 ms になる」と測り、本番の手順を「sgbench3 でボット 1 試合を流して記録する」と決めています。入れ替えスクリプトはその手順と食い違っていて、sgbench3 より先に走れば、薄い記録からキャッシュが作られます。sgbench3 の記録があっても、あとから書かれた sg1/sg2 の短い record の方が新しければ、そちらが選ばれます。
- 直し方の案:
  - 入れ替えスクリプトから 25 秒後の再起動をやめ、「キャッシュがなければ sgbench3 で 1 試合流す」手順を案内するだけにする。
  - `start-small.sh` の record の選び方を「最新」ではなく「大きい（クラス数が多い）もの」にする、または `ATHENA_AOT_RECORD_ONLY=sgbench3` のように記録してよいサーバーを限定する。

### M2 — /disguise の本名が、チャットの Tab 補完で漏れる

- 場所: [CraftServer.java:1777-1782](PandaSpigot-Server/src/main/java/org/bukkit/craftbukkit/CraftServer.java)（`tabCompleteChat`）、`org.bukkit.command.Command#tabComplete` の既定実装
- 確認: コードの読み取り。
- タブリスト・名札・表示名は `PacketPlayOutPlayerInfo.athenaSeenBy` と `athenaUpdateDisplayName` で偽名に揃っています（`PlayerList`・`CraftPlayer.registerPlayer` のすべての ADD_PLAYER を確認しました）。
- ところが `tabCompleteChat` と、Bukkit のコマンドの既定の補完は `p.getName()`（本名）で候補を作ります。他のプレイヤーがチャットで Tab を押すと、変装中のプレイヤーの **本名** が候補に出ます。`/disguise` の目的をそのまま崩します。
- 直し方の案: 補完の候補を作るところで、見る側が本人でなければ `athenaDisguise.getName()` を使う。`Command#tabComplete` の既定実装（API 側）にも同じ変更が要ります。プラグインの独自補完は `PlayerDisguiseEvent` で対応してもらう旨を API のドキュメントに書く。

### M3 — AthenaGuard の猶予が、ping に比例して上限なく伸びる

- 場所: [AthenaGuard.java:192-193](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaGuard.java)（ノックバックの猶予）、[:245](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaGuard.java)（テレポート待ち）、[:665](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaGuard.java)（ポーション効果の猶予）
- 確認: コードの読み取り。enforce 無効の今の本番では影響なし。
- `player.ping` は KeepAlive の往復時間の移動平均（[PlayerConnection.java:2077](PandaSpigot-Server/src/main/java/net/minecraft/server/PlayerConnection.java)）で、**クライアントが応答を遅らせるだけで大きくできます**（タイムアウトの 30 秒近くまで）。
- `grantMoves = 10 + ping / 50` なので、ping 5,000 ms で 110 tick、ping 20,000 ms で 410 tick、1 発殴られるたびにノックバック分の水平・垂直移動が許されます。reach の lookback は `Math.min(20, …)` で抑えてあるのに、こちらは抑えていません。
- 直し方の案: Guard で使う ping を `Math.min(player.ping, 1000)` 程度に丸める共通メソッドを作り、4 か所で使う。enforce を有効にする前に入れてください。

### M4 — `pandaspigot.yml.bak` にトークンが残り、パーミッションが緩くなり得る

- 場所: [AthenaConfig.java:178](PandaSpigot-Server/src/main/java/athena/server/AthenaConfig.java)
- 確認: コードの読み取り。
- 移行のとき `Files.copy(source, source + ".bak", REPLACE_EXISTING)` で元ファイルを丸ごと控えます。中身には BungeeGuard のトークンが含まれます。`Files.copy` は `COPY_ATTRIBUTES` なしだとパーミッションを引き継がず、umask の既定（多くは 644）で作ります。元の `pandaspigot.yml` を 600 にしていても、`.bak` は誰でも読めるファイルになり得ます。`athena.yml` 側は 0363 でパーミッションを保つようにしてあり、そちらとも食い違っています。
- また、移行がすでに済んだあとも、Athena のキーが `pandaspigot.yml` に紛れ込むたびに `.bak` を上書きします。
- 直し方の案: `.bak` を元のパーミッション（取れなければ 600）で作る。ログに「`.bak` にトークンが含まれる」と出す。

## 5. 低

| # | 内容 | 場所 | 確認 | 直し方の案 |
|---|---|---|---|---|
| L1 | `teleportAsync` の Future が、ワールドの破棄やロードの破棄で **永遠に完了しない**。`/temptp` も同じ理由で何も言わずに止まる。前回は「callback を呼ばない」で受け入れたが、Future の API としては呼び出し側が待ち続ける | [AthenaServerImpl.java:138](PandaSpigot-Server/src/main/java/athena/server/AthenaServerImpl.java)、[TempTpCommand.java:93](PandaSpigot-Server/src/main/java/athena/server/TempTpCommand.java) | コード | ワールドの破棄時に、そのワールドの待ちを `complete(false)` で終える。callback の型が成功／失敗を持てないなら、`teleportAsync` だけは Athena 側で待ちを管理する |
| L2 | `PlayerConnection.sendPacket` の Guard フック（ノックバック・テレポートの記録）は、AthenaGuard の「すべて server thread」という前提で書かれているが、`sendPacket` はプラグインが非同期スレッドから呼べる。データ競合で猶予の値が壊れ得る | [PlayerConnection.java:1008-1022](PandaSpigot-Server/src/main/java/net/minecraft/server/PlayerConnection.java) | コード | server thread 以外からの呼び出しでは記録を server thread に回す（または無視する） |
| L3 | 1.7 クライアント向けの変換に失敗したパケットは **捨てて接続を続ける**。ログは接続ごとに最初の 1 回だけ。エンティティの生成やウィンドウのパケットが落ちると、クライアントの状態がずれたまま遊び続けることになる | [AthenaLegacy17Encoder.java:80-87](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaLegacy17Encoder.java) | コード | 失敗回数を数え、一定数を超えたら切断する。少なくとも失敗の総数をメトリクスに出す |
| L4 | section store の掃除が、**同じテンプレートを使う別のサーバー（別バージョンの jar やマップ）の store を消す**。消されたマップは次に開くとき作り直しになる。`OPEN` のマップは弱参照の空エントリを消さない | [AthenaSectionStore.java:187-216](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaSectionStore.java)、[:94](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaSectionStore.java) | コード | 古い store は「一定時間使われていない」ものだけ消す。`OPEN` は `get` で null を見たら消す |
| L5 | `AthenaTeleportQueue` と `teleportAsync` は、行き先のワールドが **もう破棄されていないか** を見ない。破棄済みの `World` を指す `Location` にテレポートすると、死んだワールドにプレイヤーが入る（Bukkit 一般の問題だが、gradual は遅延がある分起きやすい） | [AthenaTeleportQueue.java:79-81](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaTeleportQueue.java) | コード | `athenaUnloaded` を見て、その人を飛ばす |
| L6 | ログを非同期 appender に変えたので、**OOM や MemoryMax=400M による強制終了**のとき、最後の最大 2,048 件が書かれずに消える。原因を一番知りたい場面のログが残らない | `log4j2.xml`（0307） | コード | ERROR 以上だけは同期で書く（`AsyncAppender` の外に File を直接つなぐ Filter）か、`blocking` と `shutdownTimeout` を明示し、sg@.service に `OOMPolicy` を足す |
| L7 | `setDisguise` のログが常に「kept from before」と出る。プラグインが新しく変装させた場合も同じ文言で、調べるときに紛らわしい | [AthenaServerImpl.java:251](PandaSpigot-Server/src/main/java/athena/server/AthenaServerImpl.java) | コード | 呼び出し側が理由を渡せるようにするか、文言を「by a plugin」に統一する |
| L8 | `setChatColor` だけ `AsyncCatcher` と null 検査がない（他の API と揃っていない） | [AthenaServerImpl.java:194](PandaSpigot-Server/src/main/java/athena/server/AthenaServerImpl.java) | コード | 他と揃える |
| L9 | 変装の偽名は変装した時点の在室者とだけ重複を調べる。あとから同じ名前の本人が入ってくると、タブリストに同名が 2 つ並ぶ（チームや名札が混乱する、とコメント自身が書いている状態） | [DisguiseCommand.java:120-142](PandaSpigot-Server/src/main/java/athena/server/DisguiseCommand.java) | コード | ログイン時に、同名の変装をしている人の偽名を振り直す |
| L10 | 攻撃のたびに `athenaBoxAt` が過去の箱を `copyOfRange` で最大 21 個作る。ボスバーは毎 tick `double[3]` と `Vec3D` を作る（前回 REPORT にも記載） | [EntityPlayer.java:304-311](PandaSpigot-Server/src/main/java/net/minecraft/server/EntityPlayer.java)、[AthenaBossBar.java:92-99](PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaBossBar.java) | コード | 配列とオフセットを返す形にする。測ってから |

## 6. 保守

| # | 内容 | 案 |
|---|---|---|
| S1 | README の数字が古い。「320 MB、`-Xmx160M`」とあるが、今の本番は 128M・RSS 約 259 MB。`sg@.service` のコメントも「144M ヒープ」のまま | README と unit のコメントを `start-small.sh` の数字に合わせる。数字は 1 か所（`start-small.sh` の冒頭）にまとめ、他はそこを参照する |
| S2 | `AthenaWorldConfig` の `asyncChunkDecoration` のコメントは「順序は直列と同じ」と言い切っているが、F04（装飾どうしが重なると同期版と違う地形になり得る）は未解決のまま。既定は true | コメントを F04 の内容に合わせる。テンプレートのワールドでは生成しないので本番 SG には影響しないことも書く |
| S3 | パッチ列に「入れて戻した」組が 9 つ（0183/0184、0209/0210、0216/0217 など）と、`WIP` の付いた 0193 が残っている。上流への rebase のたびに解決する量が増える | 打ち消し合う組は両方消し、0193 は 0194 と統合する。歴史はルートの git に残る |
| S4 | リポジトリ直下に、`true/`（57 MB、誤った引数で作られたらしいサーバーディレクトリ）、`bench/`（19 GB）、`server/`（6.6 GB）、`cache/`、`logs/`、未追跡の監査資料がある。`.git/info/exclude` に頼っていて、別のクローンでは見えない | `true/` は中身を確かめて消す。計測データの置き場をリポジトリの外にするか、`.gitignore` に入れる。監査資料はコミットするか場所を決める |
| S5 | PR では Build と Check の 2 つのワークフローが両方 `./panda setup` を走らせ、Check の `gradlew build` はテストも重ねて実行する | Check を消して Build にまとめる。あるいは Check はパッチの適用だけ確かめる |
| S6 | テストのない機能: `/disguise`・`/mask`・`/chatcolor`、ボスバー、Guard の攻撃と timer（移動のテストはある）、hand-off キューの順序と close、1.7 デコーダへの壊れた入力 | M2・M3 を直すときに、その周りから足す。1.7 デコーダは短いファジングテストが向いている |

## 7. 前回から持ち越しの項目（今回は再調査していない）

| 項目 | 状態 | 前回の判断 |
|---|---|---|
| F04 非同期装飾が同期版と一致しない | 未対応、既定 true | ユーザーの判断待ち。テンプレートのワールドでは起きない |
| F06 tick の予算と flush | 一部のみ対応 | 測ってから |
| F09 threshold の変更とキャッシュ | 未対応 | 本番は固定 threshold |
| F14 store の fingerprint が内容を見ない | 未対応 | 方式の選択待ち（配布時に revision を付ける案が推奨） |
| F16 光の executor を閉じない | 未対応 | 本番は async lighting 無効 |
| F20 配列の共有の約束事 | 未対応 | 互換性の方針待ち |
| V03 遅い接続への送信に上限がない | 未対応 | 観測の仕組みを先に |
| V04 Future を取り消しても止まらない | 未対応 | API の約束事として文書化 |

## 8. 問題がなかったところ（今回確かめたもの）

- **BungeeGuard**: トークンは `MessageDigest.isEqual` で比べ、ハンドシェイクイベント経由の転送にも同じ検査がかかり、プロフィールから取り除いてからタブリストに流しています。トークン未設定での起動時警告もあります。本番の athena.yml にトークンが入っているかは、このリポジトリからは確かめられません（ArcanaServers 側で確認してください）。
- **1.7 デコーダ**: 長さの検査、途中で失敗したときの出力の解放、gzip の NBT への読み取り上限（0273）が入っています。
- **PacketDecompressor**: 宣言サイズで先に確保せず、ちょうどその長さに展開されることを検査しています。圧縮爆弾への備えとして十分です。
- **hand-off キュー**: tick の中で溜めて最後に一度だけ起こす方式は、`athenaEndBatch` が `finally` にあるので、tick が例外で抜けてもキューが止まりません。
- **設定の移行**: 検証→一時ファイル→原子的な rename→元ファイルの更新、の順になっていて、F01 の問題は解消しています。

## 9. M1〜M4 の対応結果

ブランチは、ルート・生成 Server・生成 API とも `fix/review-2026-11-01` です（`perf/java25-audit-2026-10-30` の `d2c89f8` / `fd8b5723c` から切りました）。push・PR・本番への反映はしていません。

| 項目 | 変更 | パッチ | ルートのコミット |
|---|---|---|---|
| M1 | `athena_sg_when_empty.sh` から、入れ替えの 25 秒後に止めて起動し直す処理を外しました。新しい jar で記録の回になったときは、その旨と手順（sgbench3 でボット 1 試合、またはその場で 1 試合流してから再起動）を出力します。`start-small.sh` は、共有キャッシュを **一番大きい record**（読み込んだクラスが一番多い回）から作ります | — | `f8cbe52` |
| M2 | API に `Athena.getNameSeenBy(viewer, player)` を足し、`Command#tabComplete` の既定とチャットの補完は、変装中の他人を偽名で照合・提示します。さらに `CraftServer.tabComplete` の最後（`TabCompleteEvent` のあと）で、変装中の他人の本名を候補から **取り除きます**（偽名に置き換えると、入力の頭文字と本名の対応が漏れるため） | api 0053、server 0384 | `df597e5` |
| M3 | `AthenaGuard.lagTicks(ping)` で ping を 1,000 ms（20 tick）に丸め、ノックバック・テレポート待ち・ポーション効果・reach の lookback の 4 か所で使います。reach の lookback の値は今までと同じです | server 0385 | `a9ae159` |
| M4 | `.bak` を、元ファイルと同じパーミッションの一時ファイル（作成時は 600）に書いてから rename します。既存の `.bak` が 644 でも置き換わります。`.bak` にトークンが含まれる旨をログに出します | server 0386 | `6565650` |

### テスト

`./gradlew :pandaspigot-server:test :pandaspigot-api:test --offline --console=plain` → 成功。API 995 件（スキップ 3）、Server 5,645 件（スキップ 1）、失敗・エラー 0。

足したテスト:

- `athena.api.AthenaNameSeenByTest`（3 件）: 見る人ごとの名前、Athena のサーバーがないとき、`Command#tabComplete` が偽名で照合して本名を返さないこと
- `org.bukkit.craftbukkit.AthenaTabCompleteDisguiseTest`（3 件）: 本名の除去（大文字小文字を無視、順序を保つ）、該当がなければ同じリストを返す、null の候補で落ちない
- `net.minecraft.server.AthenaGuardLagTest`（3 件）: 50 ms ごとに 1 tick、20,000 ms でも 20 tick、負の ping は 0
- `AthenaConfigSafetyTest.backupTakesTheSourcePermissionsNotTheUmask`: 元が 600 / 640 のとき `.bak` も同じになり、644 の古い `.bak` が置き換わり、一時ファイルが残らない

### 残っていること

- **M1 は本番に入っていません。** 本番が使うのは ArcanaServers の `common/start-small.sh` と `vps-b/root/athena_sg_when_empty.sh` です。ArcanaServers の `start-small.sh` は、もともとこのリポジトリの版より古く（`--enable-native-access` などがない）、今回の変更と合わせて揃える必要があります。
- **M2**: 変装した名前は `Bukkit.getPlayer(name)` では見つからないので、偽名を補完して `/tell` などに使っても「プレイヤーがいない」になります。本名が漏れないことを優先しました。プラグインが自前で作る補完は、最後の除去で本名が消えるだけで、偽名は出ません。
- **M3**: 本当に ping が 1 秒を超えるプレイヤーは、enforce を有効にすると、ノックバックのあとの移動で戻されることがあります。1 秒を超える ping で PvP は成り立たないと判断しました。
- 実サーバーでの動作確認（変装中の Tab 補完、移行時の `.bak`）はしていません。
