# Athena 全体精査 — Java 25 と性能の観点（2026-10-30）

## 1. 前提と範囲

- 対象: ルート `ca40498`、Server 生成ツリー `6d92cec11`（パッチ server 0374 / api 0050 まで）。
- 10/29〜10/30 の監査（REPORT / NEXT-STEPS / REVIEW-CLAUDE / ALLOCATION-VALIDATION）で扱った F01〜F20、N01〜N04、V01〜V08 は**繰り返しません**。未解決のまま残っているものだけ §5 に並べます。
- 今回の観点は「Java 25 で動く、最高性能の 1.8.8 サーバー」です。tick ループ、ネットワーク、チャンク送信、イベント発火、スレッド構成、JVM 起動設定、同梱ライブラリを読みました。全 1,401 ファイルを一行ずつ確かめたわけではありません。
- テスト: `./gradlew :pandaspigot-server:test :pandaspigot-api:test --offline` は成功（Server 5,630 件、API 988 件、失敗 0。スキップは 1 件と 3 件）。入力が変わっていないので UP-TO-DATE の結果を使いました。
- 本番 VPS の設定（`spigot.yml` の `netty-threads` など）は見ていません。手元の `server/` で確かめた値には「手元」と書きます。
- コードは変えていません。追加したのはこの文書だけです。

## 2. 結論

これまでのパッチで、割り当て、衝突判定、チャンク圧縮、ネットワークの flush、テンプレートワールドはかなり詰められています。残っている伸びしろは細かい計算の工夫ではなく、次の 3 つです。

1. **tick の時刻源が壁時計**（`System.currentTimeMillis`）。時計が飛ぶと、サーバーが止まるか、まとめて早送りされます（P1）。
2. **テンプレートマップのチャンク圧縮結果が、毎回の起動でヒープに作り直されている**。マップを作るときに一度だけ圧縮して section store に入れておけば、CPU とヒープの両方が減り、同じ VPS 上のサーバーでページキャッシュを共有できます（最大の性能改善候補）。
3. **Java 25 の警告が毎回の起動で出ている**（Netty の `sun.misc.Unsafe` と JNI）。今は動きますが、将来の JDK では止まる経路です。io_uring の incubator も更新が止まっているので、Netty 4.2 への移行を計画に入れるべきです。

## 3. 問題点（正確性・将来の互換性）

### J01 — P1: tick の予定を壁時計で決めている

- 場所: `MinecraftServer.run()`（`MinecraftServer.java:603-678`）。`az()` は `System.currentTimeMillis()`（`:1602`）。
- 内容: 次の tick の予定 `this.ab` を `az()` と比べて `parkNanos` しています。壁時計は NTP のステップ、VM の一時停止と再開、手での時刻変更で前後に飛びます。
  - **時計が戻ったとき**: `this.ab - az()` がその分だけ大きくなり、**戻った秒数のあいだ tick が止まります**。5 秒以上でも止まり続けます（遅れの補正は進んだ場合しか働かないため）。
  - **時計が進んだとき**: 5 秒未満なら、その分の tick を待たずに連続で回します（4 秒なら最大 80 tick の早送り）。PvP 中は、プレイヤーには一瞬のワープや連続被弾に見えます。
- TPS の計算（`curTime`）は `nanoTime` なので、TPS の表示には出ません。気づきにくい不具合です。
- 修正案: 予定の計算をすべて `System.nanoTime()` にします（`ab`・`R` をナノ秒にし、`parkNanos(remaining)` をそのまま渡す）。待ち時間がミリ秒単位でなくなるため、tick 間隔のぶれも小さくなります。`az()` を使っているほかの箇所（ping の時刻など）は壁時計のままで構いません。
- 確認: 時刻源を差し替えられるようにし、時計を ±2 秒飛ばした回帰テストで、止まりも早送りもしないことを確かめます。

### J02 — P2: Java 25 の実行時警告（Unsafe と native access）

- `bench/` と `logs/` のコンソール出力に、毎回の起動で次の警告が出ています。
  - `sun.misc.Unsafe::allocateMemory has been called by io.netty.util.internal.PlatformDependent0$2`（JEP 471 / 498）
  - `java.lang.System::loadLibrary has been called by io.netty.util.internal.NativeLibraryUtil`（JEP 472。epoll / io_uring / jline-jni）
- 今の JDK 25 では動きます。ただし、どちらも「将来のリリースで既定で拒否する」と予告されています。
- 対処:
  1. 短期: `start-small.sh` の `FLAGS` に `--enable-native-access=ALL-UNNAMED` を追加し、`--sun-misc-unsafe-memory-access=allow` で今の挙動をはっきり固定します。フラグを変えると AOT キャッシュのキーが変わり、もう一度学習（record）の起動が必要になります（V01 の二重 JVM に注意）。
  2. 中期: Netty 4.2 へ移行します（J03）。JDK を上げる前に、`--sun-misc-unsafe-memory-access=deny` で起動とベンチを回し、どこが壊れるかを先に知っておきます。

### J03 — P2: io_uring が incubator 版のまま

- `netty-incubator-transport-native-io_uring:0.0.26.Final`。io_uring は Netty 4.2 で本体（`netty-transport-native-io_uring`）に入りました。incubator 版は今後の修正を受けられない前提で考えるべきです。
- Netty 4.2 では、ほかに次の利点があります。ただし、自分たちの負荷で測ってから決めてください。
  - 既定のアロケータが adaptive になり、小さいヒープや少ない接続数では常駐メモリが減る見込みがあります（`maxOrder=7` の調整は不要になるかもしれません）。
  - IO の処理が `IoHandler` 方式に整理され、epoll / io_uring / NIO を同じ形で扱えます。
- 影響: `ServerConnection` の event loop の生成、`FastThreadLocalThread` の factory、ハンドオフキュー（`PlatformDependent.newMpscQueue`）、`netty-all` の分割（J10）。プラグイン（ProtocolLib 系や Apollo）が Netty の内部 API に触れていないかの確認も要ります。

### J04 — P2: パケット書き込み中の例外を握りつぶし、壊れたパケットを送る

- `PacketEncoder.a()`（`PacketEncoder.java` の `packet.writePacketData` の try/catch）は、`Throwable` をログに出すだけで処理を続けます。その時点で ByteBuf には packet id と途中までの中身が入っており、`MessageToByteEncoder` はそれをそのまま送ります。
- クライアント側では長さと中身が合わない壊れたパケットになり、意味のわからないエラーで切断されます。どのパケットが原因だったかは、サーバーのログを突き合わせないとわかりません。
- vanilla も同じ動きです。それでも、プラグインが作る不正な NBT・アイテム・スコアボードの文字列で起き得ます。直すなら、例外のときは `bytebuf.clear()` で何も送らず、パケットのクラス名を出して切断（または破棄）します。

### J05 — P2: 同梱ライブラリが古く、宣言と実体も違う

`PandaSpigot-Server/build/libs/*-unmapped.jar` の中身で確かめた、実際の版です。

| ライブラリ | build に書いた版 | jar の中の実体 | 備考 |
|---|---|---|---|
| Gson | 2.2.4 | **2.8.0** | adventure-gson に引き上げられている。2.8.9 未満は不正な入力で DoS になり得る（CVE-2022-25647） |
| Guava | 17.0 | **18.0** | 依存の解決で引き上げ |
| SnakeYAML | 1.15 | 1.15 | 1.x 系には信頼できない YAML での任意コード実行などの既知の問題（CVE-2022-1471 ほか）。読むのが自分の設定ファイルだけなら影響は小さい |
| commons-io / commons-lang3 | 2.4 / 3.3.2 | 同じ | 古いが hot path では使っていない |
| ebean | 2.8.1 | 同じ | 約 4 MB。Bukkit の Database API を使うプラグインがなければ使われない |

- API の利用者に公開している版（宣言の 17 / 2.2.4）と、実行時に入っている版（18 / 2.8.0）が違います。プラグインのコンパイル時と実行時で API が食い違う原因になります。少なくとも宣言を実体に合わせます。
- 上げるときは、Bukkit プラグインとのバイナリ互換を確かめながら段階的に進めます。Guava 21 以降では `Objects.toStringHelper` などが消えており、古いプラグインが壊れます。Gson は 2.10 系までなら、ほぼ互換のまま上げられます。

### J06 — P3: 起動まわりの細かい点

- `sg@.service` には `Environment=JAVA=` がありません（`mc-small@.service` にはあります）。`start-small.sh` は `java` を PATH から取るので、OS の Java が更新されると AOT のキーが変わります。そうなると、気づかないうちに patch と学習の起動（RSS の山）が走ります。JDK のパスを固定してください。
- `start-small.sh` は「新しい順に 3 つより古いキーのディレクトリを消す」処理を、ロックを外したあとの `exec` より前に行います。4 種類以上の jar・JDK・フラグの組み合わせが同時に起動すると、起動の直前にほかのサーバーの jar を消し得ます。今の運用（同じ paperclip.jar）では起きません。
- `DedicatedServer` の「Server Infinisleeper」スレッドは、Windows のタイマー精度を上げるための仕掛けです。Linux では意味がなく、スレッドが 1 本残るだけです。
- bStats（`com.hpfxd.pandaspigot.Metrics`）は、手元で `enabled: true` になっています。Athena のサーバーが PandaSpigot（service 15154）として外部へ送信されます。本番で不要なら止めます。

## 4. 性能の改善候補

すべて「測ってから採否を決める」提案です。効果の数字は見込みであり、測った値ではありません。

### P01 — 高: テンプレートマップの圧縮済みチャンクを store に事前に入れる

- 今の動き: `AthenaChunkPacketCache` は、チャンクごとに `volatile byte[] frame`（zlib の圧縮済みフレーム）を**ヒープに**持ちます。試合の前に preload したマップ（約 1,200 チャンク）では、起動するたびに全チャンクを `Athena chunk packet compressor` スレッドで圧縮し直しています（`AHEAD_LIMIT=64` で待ちながら）。
- 提案: テンプレートマップの `athena-sections2-<hash>.bin` を作るときに、各チャンクの初期状態の 1.8 フレーム（と、必要なら 1.7 フレーム）も一度だけ圧縮して格納します。実行時は mmap した領域から `ByteBuf`（`Unpooled.wrappedBuffer(MemorySegment.asByteBuffer())`）として直接書き出します。
  - 起動と preload で、圧縮の CPU がほぼゼロになります。
  - 圧縮済みフレームがヒープから出て、ファイルを元にしたページになります。同じマップを読む sg1 と sg2 が同じページを共有します。1 フレーム数 KB〜十数 KB × 約 1,200 チャンクなので、**サーバー 1 台あたり数 MB〜十数 MB のヒープ**が見込めます（144M ヒープでは大きい）。
  - ブロックが変わったチャンクだけ、今の仕組み（modCount で無効にし、ヒープで作り直す）に戻ります。
- 注意: フレームは packet id・圧縮の閾値・ブロック・光・biome で決まります。store のヘッダーに閾値と形式の版を記録し、合わなければ使わないようにします（F09 の考え方を延長）。mapped light の上に anti-xray を使う場合は、今と同じく対象外にします。

### P02 — 高: zlib を libdeflate に置き換える

- チャンクの圧縮は「サーバー全体の CPU の 85%」だった（`AthenaChunkPacketCache` のコメント）とあります。キャッシュを入れたあとも、通常のパケット圧縮（`PacketCompressor`）、展開（`PacketDecompressor`）、1.7 用の再圧縮が残っています。
- Velocity と同じように libdeflate の JNI（または FFM）実装を使うと、同じ圧縮率で圧縮が 2〜3 倍速くなることが多い、とされています。ネイティブライブラリを同梱し、読み込めないときは `java.util.zip` に戻します。P01 を入れると効果は小さくなるので、**P01 のあとに測って決めます**。

### P03 — 中: プラグインのイベント呼び出しを生成コードにする

- `JavaPluginLoader.createRegisteredListeners`（API 側 `:294-305`）は、呼び出しのたびに `eventClass.isAssignableFrom(event.getClass())` と `Method.invoke` を通します。JDK 18 以降の `Method.invoke` は MethodHandle で実装されていますが、全リスナーが同じ呼び出し箇所を通るので megamorphic になり、インライン化されません。
- `PlayerMoveEvent`（20 人 × 20 回/秒）、`EntityDamageByEntityEvent`、`ServerTickStart/EndEvent` など、回数が多いイベントほど効きます。
- 提案: `MethodHandles.privateLookupIn(listenerClass, ...)` と `LambdaMetafactory`（または `defineHiddenClass`）で、リスナーのメソッドごとに `EventExecutor` を作ります（Paper の ASM / MethodHandle 版の executor と同じ考え方）。作れなければ、今の reflection に戻します。
- 確認: リスナーが private・static・別のクラスローダーの場合のテスト。JFR で `Method.invoke` 系のサンプルが減ること。

### P04 — 中: Netty のスレッド数の既定値

- `SpigotConfig.nettyThreads()` の既定値は 4 です（手元の `spigot.yml` は 1）。2 vCPU の VPS にサーバーを 2 台置くと、既定値のままでは event loop が 8 本になります。Netty の pooled allocator はスレッドごとにキャッシュを持ち、arena の数もそれに引っ張られるので、RSS が増えます。
- 20 人規模なら event loop は 1〜2 本で足ります。本番の `spigot.yml` を 1（または 2）にそろえ、`start-small.sh` 側でも `-Dio.netty.eventLoopThreads` を明示すると、設定漏れに強くなります。arena の数（`io.netty.allocator.numDirectArenas` / `numHeapArenas`）も合わせて 1〜2 にした場合の RSS を測ります。

### P05 — 中: GC の比較を一度やり直す

- 今は ParallelGC で full GC が 77〜107 ms（2〜4 tick）です。JDK 25 では generational Shenandoah が正式機能（JEP 521）になりました。小さいヒープでも停止を数 ms に抑えられる可能性があります。
- その代わり、並行 GC は CPU とヒープの余裕を多く使います。0.13 vCPU と 144M という制約の中で、`-XX:+UseShenandoahGC -XX:ShenandoahGCMode=generational` と、今の Parallel とを、同じマップ・bot・試合時間で比べます（tick の p99.9・最大値、cgroup の throttling、RSS）。勝てなければ Parallel のままで構いません。
- Parallel のままにする場合は、`-Xmn` で若い世代を固定すると、試合中に old へ上がる量が減るかどうかを見ます。

### P06 — 中: 時間の長い処理のスレッド池を virtual thread に

- `CraftScheduler` の非同期の池（`newCachedThreadPool`）、チャットの池、認証の池は、どれも待ちが中心の処理です。JDK 24 以降は `synchronized` による pinning も解消されたので（JEP 491）、virtual thread にするとスレッド数とスタックの予約が減ります。
- ただし、プラグインの非同期タスクは、JDBC や ThreadLocal を前提にしていることが多いです。**互換性の確認が要る**ため、まずチャットと認証の池から始めます。

### P07 — 低: 小さな無駄（まとめて直せるもの）

| 場所 | 内容 | 案 |
|---|---|---|
| `TileEntityEnchantTable.c()` | 毎 tick、近くのプレイヤーを探して本のアニメーションを計算している。値はクライアントに送られない（クライアント側で描いている） | Paper がチェストでしたように `IUpdatePlayerListBox` を外す。SG マップのエンチャント台の数だけ毎 tick の処理が減る |
| `World.tickEntities` / `World.removeEntity` | `ArrayList.remove(index)` と `indexOf` で、1 体を除くたびに O(n)。最終戦で死亡ドロップや矢がまとめて消える tick に重なる | tick の最後に詰め直す方式か、index を持つリストにする。まず JFR で出るかを確認 |
| `PacketEncoder` → `EnumProtocol.getPacketId` | 送信ごとに `EnumMap` と `HashBiMap.inverse().get(Class)` を引き、`Integer` で返す | `ClassValue` か、パケットのクラスごとの int の表 |
| `MinecraftServer.B()` | ワールドごと・tick ごとに `new ArrayList<>(players)`（flush を止める接続の一覧） | フィールドで使い回す。プレイヤーが 0 人なら作らない |
| `ChunkIOExecutor.adjustPoolSize` | `Math.ceil(players / PLAYERS_PER_THREAD)` が整数の割り算 | `(players + 49) / 50`。害はない |
| `netty-all` | kqueue・macOS DNS・不要な codec など約 0.8 MB と 3,000 余りのクラス | 使うモジュールだけ並べる（J03 と一緒に） |

## 5. 前回までに見つかり、まだ未解決のもの

| 項目 | 状態 |
|---|---|
| F04 非同期の装飾が同期版と一致しない | `asyncChunkDecoration` の既定値は **true のまま**。既定値をどうするかは、まだ決まっていない |
| F06 tick の予算と flush | 未着手（測ってから） |
| F09 閾値の変更とキャッシュ | 未着手。P01 で store にフレームを入れる場合は、ここを先に決める必要がある |
| F16 光の executor | `lightingExecutor.shutdown()` はまだない（`use-async-lighting=false` なら起きない） |
| F20 配列の共有の規約 | 方針の決定待ち |

F07（ログインの重複投入）は、`LoginListener` で投入の前に `AUTHENTICATING` へ移すよう直っています。

## 6. 良い状態にあるもの（変えなくてよい）

- Java 25 で削除・非推奨になった API（`finalize`、`SecurityManager`、`Thread.stop`、`sun.misc` の直接利用、`setAccessible` での JDK 内部への侵入）は、サーバー本体に**ありません**。
- 起動設定: Compact Object Headers（JEP 519）、AOT キャッシュ（JEP 483/515）を `ArchiveRelocationMode=0` で共有、`TrimNativeHeapInterval`、`MALLOC_ARENA_MAX`、コードキャッシュの調整は、どれも理由が記録されていて妥当です。
- tick の待ちを `parkNanos` 一回にしたこと（0252）、接続ごとのハンドオフキューと tick ごとの flush 一回（0308・0334）、ログの非同期化（0307）、timings を無効にしていること。
- テスト 6,600 件余りが Java 25 で通っています。

## 7. 進める順番の提案

1. **J01**（数十行と回帰テスト。リスクが小さく効果がはっきりしている）
2. **J02 の短期対応**と **J06** の `sg@.service` の JDK 固定（設定だけ。AOT の学習を 1 回やり直す）
3. **P04**（設定だけ。本番の `netty-threads` を確かめる）
4. **P07** の中のエンチャント台と `PacketEncoder`（**J04**）
5. **P01**（設計が要る。F09 の方針と合わせて決める）→ そのあと P02 を測る
6. **P03**
7. **J03 / J05**（Netty 4.2 と依存の更新。プラグインの互換を確かめながら、別ブランチで）
8. **P05 / P06**（比較の実験）
