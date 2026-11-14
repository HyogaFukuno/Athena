# Athena の精査 — 2026-11-14

## 対象と方法

- **対象**：`origin/master` と同じ内容のワークツリー（`audit/deploy-check-2026-11-12`、server 0438・api 0069）。
- **実測**：VPS-B の JFR `run/vps-out/c-b5-jfr/sg.jfr`（2026-11-11、server 0433、8 部屋、ネットワーク越しのボット約 110 体、589 秒、C1 のみ、Shenandoah）。
  - CPU は実行サンプル 97,614 個に占める割合。確保はサンプルの重み 4,042 MB に占める割合。
  - 集計は `JfrAgg.java`（このフォルダ）。`java JfrAgg.java <jfr> cpu|alloc <スレッド|-> <含むフレーム|-> <件数>`。
  - この JFR は 0434〜0438 より前。0435（パイプラインの確認）と 0436（よく読むセクションのヒープの写し）が直した分は、下の表から除いた。
- **読んだ所**：ワールドのスレッド、tick の本体、送受信（`NetworkManager`、パイプライン）、トラッカー、チャンクのパケット、テンプレートのストア、イベントの呼び出し、起動フラグと本番の設定（ArcanaServers `7c739e4`）。
- **深く見ていない所**：`AthenaGuard`、1.7 の変換、ボット、SG 本体。ワールドのスレッドの所有権は 11-05・11-06 の精査が見ているので、static な状態の抜き取りだけ行った（新しい問題はなかった）。
- **効き目の数字**は JFR の割合から出した見積もりで、上限に近い。入れる前後の計測はしていない。

## まとめ

- クラッシュやデータ破損につながる新しい問題は見つからなかった。
- 確度が高く、すぐ直せるものが 4 つある（P1）。うち 2 つは 1 行で直る。
- 本番は C1 のみで動くので、エスケープ解析が効かない。バニラの小さな `new`（イテレータ、`BlockPosition`、結果のリスト）がそのまま確保になる。P3 の細かい項目はこの理由でまとめて効く。

## P1：確度が高く、すぐ直せる

### P1-1 ワールドボーダーのキャッシュ（0417）が本番で働いていない

- **何が起きているか**：`WorldBorder.athenaSides()` は `this.world` が null なら何もしない（`WorldBorder.java:94`）。その `world` フィールド（`WorldBorder.java:22`）に代入する行が、本体のソースのどこにもない。代入しているのは試験 `AthenaWorldBorderSidesTest` だけなので、試験は通る。
- **裏付け**：JFR では `minX` などが `getSize` を直接呼んでいる（286 サンプル）。キャッシュが効いていれば、tick に 1 回しか呼ばれない。`WorldBorder` 全体で 1,279 サンプル（**1.3%**）。0417 のコミットが見込んだ「約 1%」が、そのまま残っている。
- **直し方**：`World` のコンストラクタ（`World.java:257` の直後）で `this.worldBorder.world = (WorldServer) this;`。
- **一緒に直すこと**：`athenaSides` は `this.centerX` を直接読む。ネザーのボーダーは `getCenterX()` を上書きして 8 で割る（`WorldProviderHell.java:65`）ので、`world` を入れた途端にネザーで辺がずれる。`getCenterX()`・`getCenterZ()` を使う。試験も、本物の `World` から作ったボーダーで確かめる形にする。

### P1-2 `paper.explicit-flush` が既定で有効で、tick ごとに全接続へ flush のタスクを投げている

- **何が起きているか**：`Main.java:21` が、指定がなければ `paper.explicit-flush=true` にする。すると `NetworkManager.processReceivedPackets`（`NetworkManager.java:852`）が、接続ごと・tick ごとに `eventLoop().execute(() -> channel.flush())` を呼ぶ。Athena の受け渡しのキュー（drain が最後に 1 回 flush する）とは別の経路で、ラムダの確保とイベントループへの投入が毎回起きる。
- **裏付け**：Netty 側でこのラムダが 1,225 サンプル（Netty スレッドの 5.9%）、tick 側の `execute` と起床が約 130 サンプル。合わせて **約 1.4%**。
- **要るかどうか**：flush なしで書くのはトラッカーの区間だけで、区間の終わりに `enableAutomaticFlush` が flush を出す。その他の書き込みは flush の印をキューに入れる。読んだ範囲では、明示の flush に頼る経路はなかった。
- **直し方**：まず本番のフラグに `-Dpaper.explicit-flush=false` を足して、ネットワーク越しのボットで A/B する（パケットの遅れが出ないことを、ボット側の受信間隔で見る）。問題がなければ `Main` の既定を false にする。AOT キャッシュのキーが変わる点に注意。
- **同時に試せる**：`-DPaper.disableFlushConsolidate=true`。drain が flush をまとめているので、`FlushConsolidationHandler` は読みと書きの両方で 1 段増えるだけになっている見込み。

### P1-3 `/reload` がワールドのスレッドを止めずに走る（11-10 の F1、未修正）

- `CraftServer.reload()`（`CraftServer.java:716`）に、スレッドの確認も `runWithWorldsStopped` もないまま。内容は 11-10 の `REVIEW.md` のとおり。
- **直し方**：ワールドのスレッドが 1 つでも動いていれば断る（11-10 の案のまま）。
- 11-10 の F2（`handBack` をせずに終わる道）と F3（`readSectionTypeIds` の種類の番号）も、0438 の時点で残っている。どちらも小さい。

### P1-4 受信パケット 1 個につき、受け渡しだけで 7 個のオブジェクトを作る

- **何が起きているか**：Netty のスレッドから world のスレッドへ渡すとき、`PlayerConnectionUtils.postToMainThread`（`PlayerConnectionUtils.java:25`）の無名 `Runnable`、`WorldServer.postToMainThread`（`WorldServer.java:1587`）のラムダ・`ListenableFutureTask`・`ExecutionList`・`RunnableAdapter`、`AthenaWorldThread.post`（`AthenaWorldThread.java:261`）の `Task` と `ConcurrentLinkedQueue` のノードができる。パケットの呼び出し元は future を見ない。
- **裏付け**：この 7 種で 113 MB（確保の **2.8%**）。CPU は渡す側が 0.7%、受ける側は `FutureTask.run` などの包みが付く。
- **直し方**：パケット専用の入口を作る。world のスレッドに、パケットと接続を並べて入れる MPSC キュー（Netty の `PlatformDependent.newMpscQueue`、ノードを作らない）を持たせ、`runTasks` がそこから `processPacket` を呼ぶ。world が変わっていた場合の回し直しは、今の無名クラスの中身をそのまま移す。一般の `postToMainThread` は今のまま残す。

## P2：効き目は中くらい。設計を伴う

| # | 内容 | 裏付け | 案 |
|---|---|---|---|
| P2-1 | **受信側のデコードが 3 段**（`splitter` → `decompress` → `decoder`）。段ごとに `ByteToMessageDecoder` の出力リスト、プールした slice、`PacketDataSerializer`、`VarIntByteDecoder` を作る | `ByteToMessageDecoder.channelRead` の下が 4,881 サンプル（**5.0%**、Netty の 23.5%）。`VarIntByteDecoder` 28 MB、`PacketDataSerializer` 26 MB | 送信側（0425）と同じ考えで、サーバーの handler だけが並ぶ接続では 1 つの handler にまとめる。累積バッファの `writerIndex` を一時的にフレームの終わりへ置けば、slice なしで直接読める。半分減れば約 2% |
| P2-2 | **イベントの呼び出しが MethodHandle 経由**。api 0051 の隠しクラスは `invokeExact` を呼ぶが、C1 はこれを畳まない | `fireEvent` の下が 4,360 サンプル（4.5%）。うち `LambdaForm$DMH.invokeSpecial` などの配線だけで約 1,000（**1.0%**）、`RegisteredListener`・`TimedEventExecutor`・型の確認の包みを足すと約 1.7% | 隠しクラスを、リスナーのクラスの nestmate として定義し（`MethodHandles.privateLookupIn(...).defineHiddenClass(..., NESTMATE)`）、`checkcast` と `invokevirtual` で直接呼ぶ。型の確認もその中に入れ、Timings を止めた後は `TimedEventExecutor` の包みを外す |
| P2-3 | **圧縮済みのチャンクのフレームをヒープに持っている**。0424 で部屋どうしは共有するが、マップ 1 つにつき 8〜11 MB がヒープに載り、そのマップの試合がすべて終わると捨てて作り直す | 作り直しの `getExtractedData` が 129 MB（確保の 3.2%）。頭打ちはヒープ | フレームをテンプレートのストアのファイルに入れ、マップしたまま送る。ヒープからページキャッシュへ移り、JVM どうしでも共有される。試合開始時の展開と圧縮がなくなる。圧縮の水準と閾値をストアのキーに入れる |
| P2-4 | **C1 のインラインの上限が既定のまま**（`C1MaxInlineSize=35`、`C1MaxInlineLevel=9`、`C1InlineStackLimit`） | `MemorySegment` の読み出し、Netty の `ByteBuf`、小さな getter の連なりが、それぞれ独立したフレームとして上位に出ている | `-XX:C1MaxInlineSize=70 -XX:C1MaxInlineLevel=15` などを、コードキャッシュの量と合わせて A/B する。仮説であり、効くかは計ってみないと分からない |
| P2-5 | **受信したパケットは次の tick の頭まで処理されない**（バニラと同じ）。攻撃は平均 25 ms、最大 50 ms 待つ | `AthenaWorldThread.run` は tick の合間は `parkNanos` で眠り、`post` は起こさない | CPU ではなく、PvP の手応えの話。攻撃のパケットが来たら起こして、その接続のキューだけ先に処理する案がある。ノックバックの速度パケットは `attack` の中ですぐ送られるので、相手に届くのも早まる。`AthenaGuard` の「1 tick に 2 回」の数え方と合わせて決める。挙動が変わるので、入れるかどうかは別に判断する |

## P3：小さな無駄。まとめて入れる

確保の割合は全体（4,042 MB）に対するもの。

| # | 所 | 裏付け | 直し方 |
|---|---|---|---|
| P3-1 | `EntityLiving.updatePotionEffects`（`EntityLiving.java:479`）が、効果がなくても毎 tick イテレータを 2 つ作る | 88 MB（2.2%） | `effects` と `effectsToProcess` が空なら飛ばす |
| P3-2 | `World.getEntities` 系の結果のリストを呼ぶたびに作り、伸ばす（プレイヤーの拾う範囲、押し合い、ホッパー） | 148 MB（3.7%） | 結果が外へ出ない内部の呼び出しは、スレッドごとの使い回しのリストに入れる（入れ子の深さごとに 1 つ） |
| P3-3 | `Entity.move`（`Entity.java:827`）と `inBlock`（`Entity.java:1704`）の `BlockPosition` | 129 MB（3.2%） | 0412 の `getType(int, int, int)` を使う。`updateFallState` へ渡す分だけ残る |
| P3-4 | 移動のパケットごとに `MinecraftServer.getWorldServer(dimension)`（`PlayerConnection.java:244`）。`CopyOnWriteArrayList` を頭から探し、イテレータを作る | 43 MB（1.1%）、239 サンプル | `(WorldServer) this.player.world` を使う。ワールドをまたぐ途中の扱いだけ確かめる |
| P3-5 | 看板のテキストを、送る相手ごとに JSON へ直す（`PacketPlayOutUpdateSign`、Netty の上） | 596 サンプル（0.6%）、確保の約 2% | 看板ごとに JSON の文字列を控え、行のコンポーネントが同じ物なら使い回す |
| P3-6 | スプリントの切り替えのたびに、移動速度の属性を見ている全員へ送る（`EntityTrackerEntry.java:410`） | 属性パケットの書き出しが 496 サンプル（0.5%） | プレイヤーで、変わったのが移動速度だけなら本人にだけ送る。ほかのクライアントは他人の移動速度を使わない。体力表示の mod への影響を確かめる |
| P3-7 | `AthenaBossBar.tick`（`AthenaBossBar.java:69`）が毎 tick、視線・配列・チャンクの確認をやり直す | 696 サンプル（0.7%）、24 MB | 位置と向きが前回から大きく変わったときだけ、または数 tick に 1 回にする |
| P3-8 | flush の印を、トラッカーの外のパケット 1 個ごとにキューへ入れる（`NetworkManager.java:366`） | MPSC キューの poll が Netty の約 4.6%（Netty 自身のタスクキューの分を含む） | バッチの中では印を 1 つにまとめ、バッチの終わりに入れる。close の前には必ず入れる（キックの文が消えないように） |
| P3-9 | PLAY の間も、パケットごとに `EnumProtocol.getFromPacket` と `channel.attr` を引く（`NetworkManager.java:336`） | 235 サンプル | `this.protocol == PLAY` なら引かない |
| P3-10 | world のスレッドが始まっても `Timings.timingsEnabled` は true のまま。イベントのたびに `Bukkit.isPrimaryThread()` を呼ぶ | 83 サンプル | `athenaKeepDisabled` で false にする（P2-2 を入れれば不要） |
| P3-11 | `PlayerChunk.hasPlayerMatching` のイテレータ、`tickConnections` の `toArray`、`athenaBeginBatch` の `ArrayList` | 合わせて約 40 MB | 添字で回す、使い回す |
| P3-12 | `deploy/start-small.sh` が本番（ArcanaServers の `common/start-small.sh`：Shenandoah、256M）とずれている。こちらは ParallelGC・128M の説明のまま | — | どちらかに寄せる。少なくとも冒頭で本番の値を指す |

## 計ったが、案を出さなかった所

- **`Container.detectAndSendChanges`**（1.6%）：全スロットを毎 tick 比べる。`ItemStack` は直接書き換えられるので、比べずに済ませる安全な方法がない。
- **`ActivationRange`**（1.0%）：範囲を 0 にすれば消えるが、エンティティの挙動が変わる。SG のマップで試す価値はあるが、ここでは勧めない。
- **トラッカー**（world の tick の 5.5%）：すでに添字の配列と早期の抜けが入っていて、目立つ無駄は見つからなかった。
- **io_uring の `IoUringIoOps`**（確保の 2.4%）：Netty の中の確保で、こちらからは減らせない。
- **`getPacketId`**：以前 `ClassValue` で遅くなった記録（P07）があるので触れていない。

## 勧める順番

1. P1-1（1 行と試験）、P1-3（`/reload` を断る）。計測は要らない。
2. P1-2 をフラグで A/B する。同じ回で `Paper.disableFlushConsolidate` と P2-4 も試せる。
3. P1-4 と P3-1〜P3-4 をまとめて入れ、確保量と GC の回数で確かめる。
4. P2-1、P2-2。どちらも試験を厚くしてから。
5. P2-3 はメモリの頭打ちに効くので、部屋数を増やす段で。

## 調べ直した結果と修正（2026-11-14、`fix/review-2026-11-14`）

指摘ごとにコードを読み直した。直すべきものは直し、直さないものは理由を書いた。試験は API とサーバーの全件（5,725 件）が通る。VPS での計測はしていない。

### 直したもの

| 指摘 | パッチ | 調べて分かったこと | 直したこと | 試験 |
|---|---|---|---|---|
| P1-1 | server 0439 | 指摘のとおり、`WorldBorder.world` は本体で一度も設定されない。ネザーで辺がずれる件も、`world` を入れると起きることを確かめた | `World` のコンストラクタで設定する。止まっているボーダーは、中心・大きさ・限界が変わるまで辺を持ち続ける（ワールドなしでも）。動いているボーダーだけ tick ごとに計算し直す。中心は getter から読む | `AthenaWorldBorderSidesTest` にネザーと、ワールドなしで動くボーダーの 2 件を追加 |
| P1-3（F1） | server 0440、api 0070 | 未修正のまま | ワールドのスレッドが 1 つでも動いていれば `CraftServer.reload()` が `IllegalStateException` で断る。`/reload` は「Reload failed: …」と返す | なし（コマンドの経路だけの変更） |
| F2 | server 0440 | 未修正のまま | 最後のワールドが抜けて終わるスレッドも `handBack()` を呼ぶ | なし |
| F3 | server 0440 | 登録表は配列なので、引くのは 1 回の読み出しで済む。11-10 の案の 128 KB の表は要らない | 登録のない状態番号は 0（空気）にする | `AthenaSectionTypeIdsTest` に 1 件追加 |
| P1-2 | server 0441 | **明示の flush は、本当に 1 つの隙間を埋めていた**。別のスレッド（非同期チャットなど）の書き込みが、トラッカーが flush を止める直前に数を数え、止めた直後に `canFlush` を読むと、どちらも flush しない | flush を止めたスレッド（トラッカーを回す world のスレッド）だけが flush を `enableAutomaticFlush` に任せる。それ以外のスレッドは、自分の書き込みを自分で flush する。そのうえで `paper.explicit-flush` の既定を false にした（`-Dpaper.explicit-flush=true` で元に戻る） | `AthenaAutoFlushTest` |
| P1-4 | server 0442 | 受け取る側は future を見ない。ただし、サーバーのスレッドのキュー（`processQueue`）は例外を捕まえないので、future を外すだけでは、処理が投げた例外で tick が落ちる | `athenaRunOnTickThread` で渡す（7 個 → 3 個）。投げた例外は、future と同じく記録して続ける | 既存の全件 |
| P3-1 | server 0443 | **CraftBukkit の不具合を見つけた**。`effectsToProcess` を一度も空にしていない。効果の歩きの中で足したり外したりした効果（毒や再生の tick が呼ぶイベントのリスナーからなど）を、その後も毎 tick 足し直し、外し直していた。外す予約が一度入ると、その種類の効果はその体からずっと外れたままになる | 適用したあとで空にする。効果がなければ歩き自体を飛ばす | なし（エンティティを試験で作れないため、コードを読んで確かめただけ） |
| P3-3 | server 0443 | `inBlock` の位置は外へ出ない。`Entity.move` の位置は `updateFallState` に渡すので残す | `inBlock` を `getType(int, int, int)` で読む（中身は `BlockPosition` 版と同じことを確かめた） | 既存の全件 |
| P3-4 | server 0443 | 次元の番号はワールドごとに違う。プレイヤーのワールドの次元が `player.dimension` と同じなら、`getWorldServer` も同じワールドを返す | 同じならそのワールドを使う。違うとき（ワールドをまたぐ途中）だけ、今までどおり探す | 既存の全件 |
| P3-5 | server 0444 | パケットの行は、パケットのプラグインが差し替えることがある | 看板が、行の物ごとに JSON を控える。パケットの行が作ったときと違えば、その行は直す | `AthenaSignJsonTest`（バイト列が同じこと、差し替えた行） |
| P3-7 | server 0443 | 目標の位置を毎 tick 配列で返していた | 欄に書く。頻度を下げる案は、見え方が変わるので入れていない | 既存の全件 |
| P3-11 の一部 | server 0443 | — | `hasPlayerMatching` を添字で回す | 既存の全件 |

### 直さなかったもの

| 指摘 | 理由 |
|---|---|
| P3-10 | **指摘が誤り**。`beforeFirstStart` が `SpigotTimings.stopServer()` を呼び、`timingsEnabled` は false になる。本番の `spigot.yml` も `timings.enabled: false`。JFR に出た 83 サンプルが何だったかは確かめていない（ワールドのスレッドが始まる前の分と見ている） |
| P2-2 | 0051 の記録のとおり、プラグインのクラスは別のモジュールにあるので、そのクラスの仲間として隠しクラスを作ることはできない（`defineHiddenClass` には完全な権限が要る）。残りは包みの数だけで、効き目は小さい |
| P3-9 | PLAY の間に別のプロトコルのパケットを書くと、今はチャンネルの状態が切り替わる。省くとこの動きが変わる。効き目は 0.2% |
| P3-6 | 他人の移動速度を送らないと、表示に使う mod の見え方が変わる。入れるかどうかは判断が要る |
| P3-8 | 印をまとめると、別のスレッドからの close との順番を保つ仕組みが要る。P1-2 のあとで計り直してから決める |
| P3-2、P3-12、P2-1、P2-3〜P2-5 | 設計を伴うか、計測が要る。残りの作業として下に書く |

### 確かめていないこと

- VPS-B での A/B（P1-2 と P1-4 の効き目、P1-2 で送信が遅れないこと）。flush の経路は、本物のクライアントかネットワーク越しのボットで見る必要がある。
- `./panda jar` での jar の作成。試験のコンパイルが通ることまでは確かめた。
- P3-1 の修正の試験。
