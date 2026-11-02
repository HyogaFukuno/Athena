# ワールドごとに独立した tick ループ — 書き換えの洗い出しと計画（2026-11-02）

対象は Athena 単体です（SurvivalGames は対象外）。ブランチは `feature/world-threads` で、`fix/review-2026-11-01`（server 0386 まで）から切りました。この文書はソースを読んだ結果で、まだ何も書き換えていません。行番号は `PandaSpigot-Server` の 0386 時点のものです。

## 1. 目標と方針

- 選んだワールド 1 つにつき専用のスレッドを 1 本持たせ、そのスレッドが自分の 50 ms ループで tick する。
- ワールドのスレッドが止まっても、他のワールドのスレッドと全体のスレッドは動き続ける。
- **既定では今と同じ動作にする。** 専用のスレッドを持つのは、設定または API で指定したワールドだけ。指定しなければ、全ワールドが今と同じく `Server thread` で tick する。これで、本番の動作を変えずに少しずつ移せる。
- 防げないもの：GC の停止（全スレッドが止まる）、メモリ不足、JVM のクラッシュ。

## 2. スレッドの役割

| スレッド | 受け持つもの |
|---|---|
| 全体のスレッド（今の `Server thread`） | 接続の受け付けとログイン、`PlayerList` への出入り、コンソールのコマンド、Bukkit のスケジューラ、ワールドの作成と破棄、オートセーブの指示、サーバーの ping、`ServerTickStart/EndEvent`、専用のスレッドを持たないワールドの tick |
| ワールドのスレッド（ワールドごとに 1 本） | そのワールドの `doTick`・`tickEntities`・tracker、そのワールドにいるプレイヤーのパケットの処理と `PlayerConnection.c()`、そのワールドのチャンクの読み込みの完了処理、preload と段階的なテレポートのうちそのワールドの分、そのワールド用のタスクの列 |

「スレッドの持ち主」の判定を 1 か所にまとめます：`WorldServer.isOwnedByCurrentThread()` と、`Athena` のスレッド判定ヘルパー（全体のスレッドかどうか、指定したワールドのスレッドかどうか）。

## 3. 書き換える箇所の一覧

難しさは、S（機械的）・M（設計が要る）・L（新しい仕組みが要る）です。

### 3.1 tick のループ

| 箇所 | 今 | 書き換え | 難しさ |
|---|---|---|---|
| `MinecraftServer.run()`（597〜） | 1 本のスレッドが 50 ms ごとに `A()` を呼ぶ。遅れの取り戻し（catch-up）と TPS の計算もここ | 同じ形のループを `WorldTickThread` として切り出し、全体のスレッドとワールドのスレッドで共有する。TPS はワールドごとに持つ | M |
| `MinecraftServer.B()`（903〜1000） | 全ワールドを順に `doTick`・`tickEntities`・tracker・`explosionDensityCache.clear` | 専用のスレッドを持つワールドを飛ばす。飛ばしたワールドの同じ処理を `WorldTickThread` で行う | M |
| 同（911〜912） | ワールドごとに `hasEntityMoveEvent`・`hasPhysicsEvent` を毎 tick 設定 | ワールドのスレッドでも同じく設定する | S |
| 同（892〜897） | 全ワールドの時刻を送る `sendWorldTimeUpdate` | ワールドのスレッドで送る | S |
| 同（990〜1000） | `AthenaIndexAudit`（static な計数） | ワールドごとの計数にするか、専用のスレッドがあるときは無効にする | S |
| `MinecraftServer.A()`（792〜797） | `NetworkManager.athenaBeginBatch/EndBatch` で、その tick の送信をまとめて Netty に渡す | ワールドのスレッドの tick も同じく包む。`athenaBatchThread` と `athenaToPost` を ThreadLocal にする | M |
| 同（812〜827） | オートセーブで全ワールドを保存する | 保存はそのワールドのスレッドに頼む（全体のスレッドから直接触らない） | M |
| `WatchdogThread` | `primaryThread` 1 本だけを見る。止まるとサーバー全体を止める | スレッドごとに最後の tick の時刻を見る。ワールドのスレッドが止まったら、スタックを出してそのワールドを「止まった」扱いにする（試合側で終わらせる）。全体のスレッドが止まったときだけ今と同じく止める | M |
| `CrashReport` の扱い（922〜951） | ワールドの tick で例外が出るとサーバー全体を落とす | ワールドのスレッドでは、そのワールドだけを止めてイベントで知らせる | M |

### 3.2 パケットの処理

| 箇所 | 今 | 書き換え | 難しさ |
|---|---|---|---|
| `PlayerConnection` の 20 の処理（例：217・227・672） | `PlayerConnectionUtils.postToMainThread(packet, this, player.getServerForPlayer())` で、ワールドを `IAsyncTaskHandler` として渡している | 渡す先は今のままでよい。`WorldServer.isMainThread()`・`postToMainThread()`（1496〜1502）を、そのワールドを持つスレッドの判定と列に変える | S |
| 同上の実行時 | 列に入れてから実行するまでに、プレイヤーが別のワールドに移っていることがある | 実行の直前に、プレイヤーが今いるワールドを確かめる。違えば、移った先のワールドの列に入れ直す | M |
| `ServerConnection.networkTick()`（207〜） | 全接続の `processReceivedPackets()`（送信の列の処理と `PlayerConnection.c()`）を 1 本のスレッドで回す | ログイン前の接続は全体のスレッドで回す。ゲーム中の接続は、プレイヤーのいるワールドのスレッドで回す（ワールドごとに接続の一覧を持つ） | M |
| `PlayerConnection.c()`（141〜） | キープアライブ、チャットの回数制限、放置のキック | ワールドのスレッドで実行する。中の `disconnect` は §3.3 の受け渡しに従う | S |
| `NetworkManager.sendPacket`（247）と `m()`（557） | `MinecraftServer.isMainThread()` で、すぐ書くか列に入れるかを決める | 「そのプレイヤーを持つスレッドか」で判定する | M |
| `PlayerConnection` の 1219、`CraftServer` の 680 | `Bukkit.isPrimaryThread()` でコマンドの実行スレッドを決める | プレイヤーのコマンドは、そのプレイヤーのいるワールドのスレッドで実行する。コンソールのコマンドは全体のスレッドで実行する | M |
| 非同期のチャット（`AsyncPlayerChatEvent`） | 非同期で処理し、`PlayerList.sendMessage` で全員に送る | 送信自体はスレッドに関係なく安全（`players` は `CopyOnWriteArrayList`）。今のままでよい | なし |

### 3.3 ログイン・退出・ワールド間の移動（いちばん難しいところ）

| 箇所 | 今 | 書き換え | 難しさ |
|---|---|---|---|
| `PlayerList.attemptLogin`・`processLogin`（423・493）と `a(NetworkManager, EntityPlayer)` | 全体のスレッドで、`players` への追加と、ワールドへの追加を続けて行う | 全体のスレッドは `players` への登録だけを行う。ワールドへの追加（`addEntity`・`PlayerChunkMap`・tracker・`PlayerJoinEvent` の送信）は、行き先のワールドのスレッドに頼む | L |
| `PlayerList.disconnect` | 全体のスレッドで、ワールドからの削除と `players` からの削除を行う | ワールドのスレッドで、ワールドから外す。そのあと、全体のスレッドで `players` から外す | L |
| `PlayerList.moveToWorld`（538〜） | 1 つの関数の中で、元のワールドから外し（tracker・`PlayerChunkMap`・`removeEntity`）、`PlayerRespawnEvent` を出し、行き先のワールドに入れる | 2 段階に分ける。(1) 元のワールドのスレッドで外し、プレイヤーを「移動中」にする。(2) 行き先のワールドのスレッドで入れる。移動中に届いたパケットは、行き先のスレッドで処理するまで待たせる | L |
| `CraftPlayer.teleport`（623〜671） | 別のワールドへの移動は `moveToWorld` を呼ぶ | 上の 2 段階の受け渡しを使う。呼んだ側には「受け付けた」をすぐ返す（Folia の `teleportAsync` と同じ考え方） | L |
| `Entity.teleportTo`（2195〜2232）、`changeDimension`・`changeWorld`・`repositionEntity`（`PlayerList` 669〜825） | ポータルなどで、プレイヤー以外のエンティティも別のワールドに移す | 専用のスレッドを持つワールドどうしの間では、ポータルによる移動を禁止する（SG では使わない）。プレイヤー以外のエンティティを別のワールドに移す処理も同じく禁止する | M |
| `AthenaTeleportQueue`（`INSTANCE`、static な `batches`） | 全体で 1 つの列で、段階的なテレポートを毎 tick 少しずつ進める | ワールドごとの列にして、行き先のワールドのスレッドで進める。別のワールドへの移動は、上の受け渡しを使う | M |

### 3.4 ワールドの作成と破棄

| 箇所 | 今 | 書き換え | 難しさ |
|---|---|---|---|
| `CraftServer.createWorld`（875〜）・`unloadWorld`、`AthenaServerImpl.loadTemplateWorld`（46〜52） | 全体のスレッドで `MinecraftServer.worlds`（`ArrayList`）と `CraftServer.worlds` に足す・外す | 作成と破棄は全体のスレッドに限る。作ったあとに専用のスレッドを始め、破棄の前に止める。`worlds` を読むほかのスレッドのために、複製して入れ替える（copy-on-write）一覧にする | M |
| `MinecraftServer.worlds` を読む箇所（11 ファイル・50 か所） | 1 本のスレッドが前提 | 上の一覧の変更で、読むだけの箇所は安全になる。書く箇所は全体のスレッドに限る | S |
| ワールドの保存（`CraftWorld.save`） | 全体のスレッドで行う | そのワールドのスレッドで行う | S |

### 3.5 全体で 1 つのもの（共有の状態）

| 箇所 | 今 | 書き換え | 難しさ |
|---|---|---|---|
| `SimplePluginManager.callEvent`（API 548） | 同期イベントは、すべて `synchronized (this)` の中で呼ぶ。**全スレッドのイベントが 1 つのロックで直列になり、1 つのワールドが固まると全部が待つ** | 同期イベントのロックを外す。`HandlerList` の登録は今も `synchronized` なので、呼ぶ側は配列を読むだけになる。プラグインの側で、スレッドの安全を守ってもらう前提になる | M |
| `CraftScheduler`（`mainThreadHeartbeat` 338） | 全体で 1 つ。タスクは全体のスレッドで実行する | 今のまま全体のスレッドで実行する。ワールドのスレッドで実行したいときのために、Athena API に `runOnWorld(World, Runnable)` と、ワールドごとの遅延・繰り返しのタスクを足す | M |
| `MinecraftServer.currentTick`（static、15 ファイル・54 か所。`ActivationRange` 11、`PlayerConnection` 9、`AthenaGuard` 8、`EntityItem` 5 など） | 全体の tick 数 | 時間を測る用途（クールダウン・拾うまでの待ち時間など）では、ワールドの tick 数を使う。全体の tick 数は残す | M |
| `MinecraftServer.ticks`、`ServerTickStart/EndEvent` | 全体の tick | 全体のスレッドのものとして残す。ワールドの tick 用のイベントを足す | S |
| `Entity.entityCount`（`Entity` 64・191） | `static int` に `++` | `AtomicInteger` にする | S |
| `World.blockLocation`・`haveWeSilencedAPhysicsCrash`、`BlockFalling.instaFall`、`BlockSapling.treeType`、`BlockDispenser.eventFired`、`CraftEventFactory.blockDamage`・`entityDamage` | tick の途中で書き換える static | ThreadLocal にするか、引数で渡す | S |
| `ActivationRange` の範囲（static な double 24 個） | tick ごとに計算して static に置く | ワールドごとか、ThreadLocal にする | S |
| `EnchantmentManager` の 4 つのダメージ計算用オブジェクトと `enchantmentRand` | static なオブジェクトに途中の結果を書く | ThreadLocal にするか、呼ぶたびに作る | S |
| `Item.itemRand`、`InventoryUtils.RANDOM` などの共有の `Random` | 複数のスレッドから呼んでも壊れはしないが、取り合いになる | ワールドの `random` を使うか、ThreadLocal にする | S |
| `PlayerList.sendPacketNearby`（1072〜）、`WorldManager` の音とエフェクト（29〜57）、`WorldServer` の天気（1392〜1407） | 全プレイヤーを見て、`dimension` で絞る。天気の一部は `sendAll` で全員に送る | そのワールドの `players` だけを見る。`sendAll` は、そのワールドのプレイヤーに送るよう直す | S |
| `ScoreboardServer`（バニラのスコアボード、160・193・224） | 全体で 1 つ。全プレイヤーに送る | 変更をロックで守る。SG は FastBoard（パケットで直接送る）なので、ほとんど使わない | M |
| `CraftScoreboardManager`（47・60） | `AsyncCatcher` で全体のスレッドに限っている | ロックで守り、どのスレッドからも呼べるようにする | M |
| Timings（`co.aikar.timings`、`SpigotTimings`） | 1 本のスレッドが前提のスタック | 専用のスレッドがあるときは無効にする（本番は使っていない） | S |
| `AthenaDiagnostics`（static な `out`・`sumNanos`） | 1 本のスレッドの tick の時間を測る | ワールドごとの記録にする | S |
| `ChunkIOExecutor`・`ChunkGenExecutor`・`ChunkDecorationExecutor`（static。`inFlightByWorld` は `IdentityHashMap`） | 裏のスレッドで読み込み、完了処理を全体のスレッドで毎 tick 行う | 完了処理を、そのチャンクのワールドのスレッドで行う。ワールドごとに完了の列を持つ | M |
| `AthenaChunkPreloader`（`INSTANCE`） | 全体で 1 つの列 | ワールドごとの列にする | S |
| `AthenaGuard` の設定（static volatile） | 読むだけ | 今のままでよい | なし |
| `AthenaChunkPacketCache`、`AthenaSectionStore` | ワールド（テンプレート）ごとの持ち物 | テンプレートを複数のワールドで共有する部分にロックや不変性があるかを確かめる | M（確認） |
| 登録表（`Block`・`Item`・`EntityTypes`・`MobEffectList`・`StatisticList` など） | 起動時に作り、あとは読むだけ | 今のままでよい | なし |

### 3.6 スレッドの判定（`AsyncCatcher` など 36 か所）

| 箇所 | 書き換え |
|---|---|
| `AsyncCatcher.catchOp`（`AsyncCatcher` 13） | 「全体のスレッドか」を「対象のワールドを持つスレッドか」に変える。呼ぶ側に対象のワールドを渡す形を足す |
| `World`（1330・1455・3604・3816）、`Block`（380・384）、`EntityLiving`（588）、`EntityTracker`（135・187）、`EntityTrackerEntry`（459・689）、`CraftWorld`（202・213・297） | 対象のワールドを渡して判定する |
| `CraftPlayer.kickPlayer`（404・413） | どのスレッドからでも呼べるようにし、実際の切断はプレイヤーのいるワールドのスレッドに頼む |
| `AthenaServerImpl`（17 か所） | 対象がワールドのもの（pin・preload・テレポート・タイルエンティティ）は、ワールドを持つスレッドで判定する。ワールドの読み込みは全体のスレッドに限る。マスク・変装・ボスバーは、プレイヤーを持つスレッドで判定する |
| `CraftServer.isPrimaryThread`（1701） | 全体のスレッドのときだけ true のまま残す（プラグインとの互換のため）。ワールドのスレッドかどうかは、新しい API で判定する |

### 3.7 Athena API に足すもの

- `World` ごとのスレッドを有効にする：`Athena.setWorldThreaded(World, boolean)`。`athena.yml` の、ワールド名のパターンでの指定もできるようにする。
- `Athena.isWorldThread(World)`：今のスレッドが、そのワールドを持っているか。
- `Athena.runOnWorld(World, Runnable)`、遅延・繰り返しの版：ワールドのスレッドで実行する。
- `Athena.teleportAsync(Player, Location)`：ワールドをまたぐ移動。終わったら通知する。
- ワールドが止まった・tick で例外が出たことを知らせるイベント。

## 4. 段階の計画

各段階の終わりで、既定（専用のスレッドなし）の動作が今と変わらないことを、既存の試験とベンチで確かめます。

| 段階 | 中身 | 確かめ方 | 目安 |
|---|---|---|---|
| 0 | 試験の道具を作る。複数のワールドを作り、ボットをワールド間で移動させ、指定したワールドのスレッドを意図的に止められる試験用プラグイン | 道具が動くこと | 3〜4 日 |
| 1 | 共有の状態を直す（§3.5 の S の項目、§3.6 の判定のヘルパー）。動作はまだ 1 本のスレッドのまま | 既存の試験、ベンチで差がないこと | 1〜1.5 週間 |
| 2 | `WorldTickThread` と、ワールドの tick の切り出し（§3.1）。プレイヤーがいないワールドだけを専用のスレッドで回す | 空のワールドを複数、並列に回せる。1 つを止めても他が回る | 1.5〜2 週間 |
| 3 | パケットと接続の振り分け（§3.2）、`callEvent` のロック（§3.5）。プレイヤーがいるワールドも専用のスレッドで回す（ワールド間の移動はまだ禁止で、ログイン時に直接入るだけ） | 1 つのワールドでボットが遊べる。別のワールドを止めても影響しない | 2〜3 週間 |
| 4 | ログイン・退出・ワールド間の移動の受け渡し（§3.3）、`AthenaTeleportQueue`・preload のワールドごとの列 | ロビー→試合→ロビーの移動を繰り返す。アイテムの増殖や、表示のずれがない | 3〜4 週間 |
| 5 | ウォッチドッグとクラッシュの切り離し（§3.1）、チャンクの読み込みの完了処理（§3.5）、残りの `AsyncCatcher`、API の仕上げ | 止まったワールドと例外を出したワールドだけが止まる | 1.5〜2 週間 |
| 6 | 競合の試験とベンチ。複数のワールドを並列に動かしたときの CPU とメモリ。スレッドの競合を見つける道具（`-XX:+UseThreadSanitizer` は使えないので、判定のヘルパーで不正なスレッドからの呼び出しをすべて記録する） | 長時間の試験で、不正な呼び出しが 0 | 2〜3 週間 |

合計で約 3〜4 か月です。

## 5. 危ういところ

- **プラグインの前提**：プラグインは「イベントはすべて 1 本のスレッドで来る」と考えて書かれている。§3.5 でイベントのロックを外すと、SurvivalGames や FastBoard などで、共有のデータに複数のスレッドから触る箇所が問題になる。Athena 単体の段階では、試験用のプラグインだけで確かめる。
- **ワールドをまたぐ処理**：受け渡しの途中で切断された、移動中に死んだ、移動中にもう一度移動を頼まれた、などの組み合わせが多い。段階 4 で、試験の場面として全部を作る。
- **1 本のスレッドの前提で書かれた Athena の最適化**：送信のまとめ（`athenaBatchThread`）、パケットのキャッシュ、テンプレートの共有。どれも、どのスレッドから触るかを確かめ直す。
- **CPU の数**：本番の箱は 2 vCPU。ワールドのスレッドが増えても、実際に並列に動くのは 2 本まで。それでも、1 つのワールドが固まったときに他が影響を受けないという効果は残る。
