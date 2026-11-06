# Athena コードレビュー（2026-10）

対象コミット: `cdd29d0`（server 0323 / api 0042 まで）

## この文書について

- `./panda setup` で全パッチを適用した `PandaSpigot-Server` のソースと、`patches/server/*.patch` を読んで判断した結果です。
- **テストは一度も実行できていません。** レビュー環境では `./gradlew :pandaspigot-server:test` が Maven Central の HTTP 429（`net.md-5:SpecialSource:1.11.6` の取得失敗）で止まり、コンパイルまで進みませんでした。以下の指摘はすべて「コードを読んだ推論」です。
- そのため各指摘に **ローカルでの確認方法** を付けました。確認して再現しなかったものは却下してください。
- 文中の `Foo.java:123` の行番号は、`cdd29d0` で `./panda setup` した後の生成ソース（`PandaSpigot-Server/src/main/java/net/minecraft/server/` など）の行番号です。パッチを編集すると行番号はずれます。
- テストコードは **スケッチ** です（コンパイルしていません）。クラス名・メソッド名は生成ソースに合わせて調整してください。

### 読んだ範囲 / 読んでいない範囲

- 重点的に読んだもの: ネットワーク（0178, 0250, 0258, 0265 の pipeline 部分, 0273, 0297, 0304, 0308, 0310, 0311, 0314, 0315, 0316）、スレッド（0190, 0193〜0197, 0252, 0275, 0307, 0321）、チャンク周り（0278, 0309, 0313）、テンプレートワールド（`AthenaSectionStore`）、AthenaGuard（最終形のみ）、Athena API、設定（0323）、CI。
- 読んでいないもの: 0137（MCP 名への置き換え 7.7 MB）、0138〜0249 の個々の最適化パッチの大半、0265 の 1.7 変換テーブル全体、AthenaGuard の移動判定の物理計算の詳細。これらについては「問題なし」とは言っていません。

---

## ローカルでの準備

```bash
# JDK 25 が必要（build-logic の toolchain が Java 25 固定、foojay なし）
java -version            # 25 であること
./panda setup            # Paper submodule 取得 → remap → decompile → パッチ適用
./gradlew :pandaspigot-server:test --continue
# 結果: PandaSpigot-Server/build/test-results/test/*.xml
```

既存テストが全部通ることをまず確認してください（0321 のコミットメッセージには「Server tests 5518/5518」とあります）。

---

## 🔴 重要

### 1. 装飾スレッドの escape 経路で `BiomeDecorator` を同時に使う競合

- **場所**: `ChunkDecorationExecutor.java:132`（`waitForIdleWorker`）と `:138` 以降（`complete`）。パッチ 0193 / 0194 / 0197。
- **前提**: `athena.yml` の `worlds.default.asyncChunkDecoration` は **デフォルト true**（0196 で false にしたものを 0197 で true に戻しています）。有効になるのは、プラグインのジェネレーターがない通常生成ワールドだけです。
- **内容**:
  1. ワーカーの `run()` で例外が出ると `job.buffer.escape()`（`:89`）され、ジョブはサーバースレッドで装飾をやり直します。
  2. やり直しの前に `waitForIdleWorker()` で `busy == false` を待ちますが、これは「ワーカーがジョブとジョブの間にいる瞬間」を見るだけです。ワーカーが次のジョブを取って `decorate` を始めることは止めません。
  3. 結果として、サーバースレッドとワーカーが同じバイオームの `BiomeDecorator`（シングルトン）に同時に入ることがあります。`BiomeDecorator.decorate` は `currentWorld != null` なら `RuntimeException("Already decorating")` を投げます（`BiomeDecorator.java:67`）。
  4. サーバースレッド側で投げられた場合、`complete()` はこの例外を捕まえないので `MinecraftServer` の tick ループまで伝わります。
- **起きる条件**: escape が発生し、かつワーカーの待ちキューに別のジョブがあるとき。escape は「サンドボックスが想定していない呼び出し」で起きるので、普段はまれです。
- **確認方法**:
  1. 再現しやすくするため、一時的に `ChunkDecorationExecutor.run()` の先頭で、N 回に 1 回わざと例外を投げます（例: `if (job.chunkX % 4 == 0) throw new IllegalStateException("test escape");`）。
  2. `asyncChunkDecoration: true` の新規ワールドで、未生成の地形を大量に生成させます（エリトラ相当の高速移動、`/tp` を連続実行するプラグイン、`Athena.preloadChunks` を広い範囲に、など）。
  3. 期待される結果（バグがある場合）: サーバーログに `Already decorating` を含むスタックトレースが出る。サーバースレッドで出ればクラッシュレポート／tick の例外になる。
  4. わざと投げるコードを外したあとも、通常運用で `[Athena] Chunk x, z will be decorated on the server thread instead` の警告が出ているか、本番ログを grep してください。出ていなければ実害が出る頻度は低いことになります。
- **修正案**: escape したジョブの再実行もワーカーに任せる（サーバースレッドで decorate しない）。または、装飾全体を 1 つのロックで相互排他にする。

### 2. `decorationPending` のチャンクがアンロードキューから消える

- **場所**: `ChunkProviderServer.java:795`（`unloadChunks` の `if (chunk.decorationPending) continue;`）、`:662`（フラグを立てる場所）。パッチ 0193 / 0194。
- **内容**: `unloadQueue.popFirst()` でキューから取り出したあと `continue` するので、そのチャンクはもうキューにありません。装飾が終わって `decorationPending = false` になっても、誰も再度キューに入れません。バニラのチャンク GC は 0085 で無効化されているので、プレイヤーが近づいて離れ直さない限り、そのチャンクはずっと読み込まれたままになります。
- **確認方法**:
  1. `asyncChunkDecoration: true` の通常ワールドで、新しい地形を高速に移動して生成させ、すぐ遠くへテレポートして離れます。
  2. しばらく待ってから、読み込み済みチャンク数を確認します。`world.getLoadedChunks().length` を表示する小さなプラグインか、診断キット（`diag/athena-diag.sh`）の `athena-diag.csv` の `chunks` 列で見られます。
  3. 期待される結果（バグがある場合）: 誰もいない場所に、装飾待ちだったチャンクが残り続け、チャンク数が元に戻らない。
  4. 確実に再現するなら、テスト用にリフレクションで `chunk.decorationPending = true` にしてから `world.unloadChunkRequest(x, z)` → 数 tick 待つ → `decorationPending = false` → さらに数 tick 待つ、で `isChunkLoaded(x, z)` が true のままかを見ます。
- **修正案**: `continue` する前に同じキーを再度キューに入れる（ただしその tick 内でもう一度取り出さないよう、別リストに溜めてループ後に戻す）。または、`completion` の Runnable で `queueUnload` を呼び直す。

### 3. アンロードしたワールドがシェイピング用の ThreadLocal に残る

- **場所**: `ChunkProviderServer.java:276`（`shapingCopies`）、`ChunkProviderGenerate.java:115`（`createShapingCopy` が `this.worldObj` を渡す）。パッチ 0190。
- **内容**: `ThreadLocal<ChunkProviderGenerate>` の値（シェイピング用のジェネレーターのコピー）が `World` を参照し、`World` → `ChunkProviderServer` → `ThreadLocal` とつながっています。ThreadLocal のエントリはキーへの弱参照ですが、値からキーが強参照で届くので、エントリは掃除されません。ワーカースレッド（「Chunk Shaping Thread-N」）は生き続けるので、**ワールドをアンロードしてもそのワールド全体が GC されません**。`syncChunkGen` 経由でメインスレッドにもコピーが作られるので、メインスレッドでも同じことが起きます。
- **起きる条件**: 通常生成のワールド（プラグインのジェネレーターなし）で、新規チャンクを 1 つでも生成したあと、そのワールドをアンロードしたとき。テンプレートワールドは新規生成しないので通常は対象外ですが、試合ごとに通常生成ワールドを作って消す運用だと効きます。
- **確認方法**:
  1. 通常生成ワールドを `WorldCreator` で作る → プレイヤーを入れて新規チャンクを生成させる → 全員出して `Bukkit.unloadWorld(world, false)`、を 5〜10 回繰り返します。
  2. `jcmd <pid> GC.run` のあと `jcmd <pid> GC.class_histogram | grep -E 'WorldServer|ChunkProviderGenerate'` を見ます。
  3. 期待される結果（バグがある場合）: 読み込み中のワールド数より `WorldServer` のインスタンス数が多く、アンロードのたびに増える。
  4. 決め手が欲しい場合は `jcmd <pid> GC.heap_dump` を取り、Eclipse MAT などでアンロード済み `WorldServer` の GC root への経路を見ます。`Thread.threadLocals` 経由なら確定です。
- **修正案**: ThreadLocal をやめ、ワールドごとのコピーのプール（`ConcurrentLinkedQueue` から借りて返す）にする。または、ワールドのアンロード時にコピーを破棄し、ワーカー側で `world.athenaUnloaded` を見て使わないようにする。

### 4. 小さな圧縮パケットで 2 MiB を確保させられる

- **場所**: `PacketDecompressor.java:51`（`byte[] abyte1 = new byte[i];`）。
- **内容**: `i` はクライアントが申告した非圧縮サイズで、上限は 2,097,152（`:43`）。実際に展開できたバイト数とは関係なく、申告どおりの配列を確保します。zlib で 1 バイトを圧縮した 10 バイト程度のデータに「2 MiB」と書けば、1 パケットごとに 2 MiB が確保されます。**これはバニラ 1.8 と同じ挙動** で、Athena が新しく入れたものではありません。ただ Athena は 128〜144 MB のヒープで運用する前提なので、GC 負荷による DoS の影響が大きくなります。
- **確認方法（JUnit スケッチ）**:

```java
// PandaSpigot-Server/src/test/java/net/minecraft/server/AthenaDecompressAllocationTest.java
public class AthenaDecompressAllocationTest {
    @Test
    public void aTinyPacketDeclaringTwoMegabytes() {
        Deflater deflater = new Deflater();
        deflater.setInput(new byte[] {0});
        deflater.finish();
        byte[] z = new byte[64];
        int n = deflater.deflate(z);

        ByteBuf frame = Unpooled.buffer();
        new PacketDataSerializer(frame).writeVarIntToBuffer(2097152); // 申告サイズ
        frame.writeBytes(z, 0, n);                                   // 実体は 1 バイト

        EmbeddedChannel ch = new EmbeddedChannel(new PacketDecompressor(256));
        ch.writeInbound(frame);
        ByteBuf out = ch.readInbound();

        System.out.println("wire bytes: " + (n + 4) + ", allocated: " + out.capacity());
        // バグ（増幅）がある場合: allocated == 2097152
    }
}
```

- **負荷での確認**: テスト用クライアント（Netty で自作、または既存の bot 基盤）で上のフレームを毎秒数千回送り、`-Xlog:gc` で GC の頻度と停止時間を見ます。
- **修正案**: 展開後に `inflater.getTotalOut() == i` と `inflater.finished()` を確認し、合わなければ切断する。さらに、プロトコル状態ごとにもっと小さい上限を設ける（Play のクライアント送信は 32 KB 程度で足ります）。上限を下げる場合は、1.8 クライアントが送る最大のパケット（本の編集、クリエイティブのアイテム）が収まるか確認してください。

### 5. `PlayerHandshakeEvent` 経由だと BungeeGuard の検証が飛ばされる

- **場所**: `HandshakeListener.java:108`（`if (!handledByEvent && proxyLogicEnabled) {`）。パッチ 0315。
- **内容**: BungeeGuard のトークン確認はこの `if` の中にあります。`PlayerHandshakeEvent` のリスナーがいてハンドシェイクを処理すると（`handledByEvent = true`）、トークンを確認しないままイベントが返した UUID とプロパティを信じます。そのイベントを使うプラグイン（独自の転送方式など）を入れると、`bungeeGuardTokens` を設定していても保護が効きません。
- **確認方法**:
  1. `PlayerHandshakeEvent` を受けて `setServerHostname`、`setUniqueId`、`setPropertiesJson("[]")` を設定し、`setCancelled(false)` だけする最小のプラグインを入れます。
  2. `athena.yml` に `bungeeGuardTokens` を設定し、spigot.yml で `bungeecord: true` にします。
  3. プロキシを通さず、トークンなしで直接接続します（BungeeCord 形式のハンドシェイクを送るテストクライアント）。
  4. 期待される結果（バグがある場合）: ログイン拒否のログ（`Refused a forwarded login`）が出ず、ログインできる。
- **修正案**: イベント経路の後にも `AthenaBungeeGuard.check` を適用する。少なくとも、トークンが設定されているのにイベントが処理した場合は警告ログを出す。

---

## 🟠 中

### 6. Athena API がスレッドを確認していない

- **場所**: `athena/server/AthenaServerImpl.java` の `pinChunks` / `addPinnedChunks` / `preloadChunks` / `unpinChunks` / `teleportGradually` / `teleportAsync`。
- **内容**: スレッドセーフでない `ArrayList`（`AthenaChunkPreloader.batches`、`AthenaTeleportQueue.batches`）やチャンクプロバイダーの状態を、呼び出したスレッドでそのまま変更します。`teleportAsync` は名前から非同期スレッドで呼ばれやすいのに、チャンクが読み込み済みだとコールバックが呼び出したスレッドで即実行され、そのスレッドで `teleport` します。
- **確認方法**: 非同期タスク（`runTaskAsynchronously`）から各 API を呼び、例外が出ないこと、つまり **何も止めてくれないこと** を確認します。`teleportGradually` を非同期から大量に呼びつつメインスレッドで tick させると `ConcurrentModificationException` が出る可能性があります。
- **修正案**: 各メソッドの先頭で `AsyncCatcher.catchOp("...")` を呼ぶか、メインスレッド以外からの呼び出しはスケジューラーでメインスレッドに回す。

### 7. AthenaGuard の許容幅がクライアントで操作できる ping に依存している

- **場所**: `AthenaGuard.java:192-193`（knockback の許容期間）、`:245`（teleport を待つ予算）、`:762`（reach の遡り幅。最大 20 tick）。
- **内容**: `player.ping` は KeepAlive の応答時間から計算されるので、クライアントが応答をわざと遅らせれば増やせます。ping が大きいほど、reach 判定は過去最大 1 秒ぶんの相手の位置を許容し、knockback の許容期間も延びます。
- **確認方法**: KeepAlive の応答だけを 800〜1000 ms 遅らせるテストクライアント（またはプロキシ）で接続し、相手が移動した後に以前の位置に向けて攻撃して、`reach` で弾かれないことを確認します。ログの `(ping N)` で ping が大きく見えていることも確認します。
- **修正案**: ping の代わりに Transaction（ConfirmTransaction）パケットの往復で遅延を測る。または遡り幅の上限をもっと小さくする。

### 8. AthenaGuard のデフォルトが「強制＋kick」

- **場所**: `athena/server/AthenaConfig.java:255-271`（`enabled`、`enforce`、`kick` がすべて true）。
- **内容**: 0282〜0300 で誤検知の修正が十数本続いており、まだ誤検知が出る可能性があります。デフォルトで kick するのは配布物として危険です。kick メッセージも日本語固定です。
- **確認方法**: 本番または bot の試合で `enforce: false`（ログだけ）にして数試合走らせ、`Athena/Guard` の `detected` ログ件数を数えます。正規プレイヤーで出ていればデフォルト強制は早すぎます。
- **修正案**: デフォルトを `enforce: false` にし、ログで誤検知がないことを確認した運営者が有効にする形にする。

### 9. `AthenaSectionStore.apply` が壊れたファイルで配列外参照になる

- **場所**: `AthenaSectionStore.java:263`（`paletteSize` は最大 255、`palette` は長さ 16）。
- **確認方法**: テンプレートの `athena-sections-*.bin` をコピーし、KIND_PACKED のエントリの bit 48-55 を 0xFF に書き換えたファイルで読み込むと `ArrayIndexOutOfBoundsException` になるはずです。
- **修正案**: `paletteSize > 16` ならそのセクションは `false` を返して NBT から読む。あわせて、mmap 中のファイルを `cp` で上書きすると SIGBUS で JVM ごと落ちる点（rsync なら安全）を、コード中のコメントだけでなく README にも書く。

### 10. イベントループ停止後に送信キューが溜まり続ける

- **場所**: `NetworkManager.java:410`（`athenaHandOff`）。パッチ 0308。
- **内容**: `eventLoop().execute()` が `RejectedExecutionException` を投げると `athenaDrainPosted` が true のまま残り、以後の送信はキューに積まれるだけになります。イベントループが止まるのはサーバー停止時くらいなので影響は小さいです。
- **確認方法**: サーバー停止中にプラグインがパケットを送り続けるケースでのみ問題になります。通常は確認不要です。
- **修正案**: `execute` を try/catch し、拒否されたら `athenaDrainPosted` を false に戻してキューを捨てる。

---

## 🟡 パフォーマンスの追加提案（計測して判断してください）

| 提案 | 場所 | 計測方法 |
|---|---|---|
| キャッシュしたチャンクのフレームが 2 回コピーされている（`PacketEncoder` で 1 回、`PacketCompressor` の pass-through でもう 1 回）。読み取り専用の `Unpooled.wrappedBuffer(frame).asReadOnly()` を prepender の先へ直接渡せば 1 回減らせる。読み取り専用バッファは暗号化でコピー経路に落ちるので安全。 | 0250 / 0310 | JFR で `PacketCompressor.encode` / `ByteBuf.writeBytes` の割合を、20 人 SG ベンチで前後比較 |
| `AthenaGuard.slipperiness` と `slimeBounce` が移動パケットごとに `new BlockPosition` を作っている | `AthenaGuard.java` | JFR の Allocation プロファイルで `BlockPosition` の割り当て元を確認 |
| `AthenaDiagnostics` がサーバースレッドで同期的にファイルを書いている（5 秒ごと） | `AthenaDiagnostics.java` | 影響は小さいはず。診断中の tick 最大値に差があるかだけ確認 |

---

## 🧹 リポジトリの整理

1. **パッチと、それを取り消す Revert パッチの組が 9 組** あります: 0183/0184、0209/0210、0216/0217、0221/0222、0224/0225、0228/0229、0230/0231、0235/0236、0238/0239。両方を消すと、パッチの適用時間、rebase の手間、レビュー量が減ります。計測の記録は別のドキュメントに移すとよいです。
   - 確認方法: 各組を両方削除して `./panda setup` が通り、生成ソースが変わらない（`git diff` が空）ことを確認する。
2. **`0193-WIP-run-chunk-decoration-off-the-server-thread.patch`** が WIP という名前のまま、デフォルト有効の機能の土台になっています。0194〜0198 とまとめることを勧めます。
3. **CI が壊れている可能性**: `.github/workflows/check.yml` と `build.yml` は JDK 17 をセットアップしますが、`build-logic/src/main/kotlin/pandaspigot.conventions.gradle.kts` は toolchain を Java 25 に固定しており、JDK を自動取得する foojay resolver もありません。
   - 確認方法: GitHub Actions の直近の実行結果を見る。toolchain の解決エラーで落ちていれば確定。
   - 修正案: `java-version: 25` にする。
4. **README が実態と合っていません**: PandaSpigot の README のままで、「JDK 17 以上」「Java 8 互換」と書かれています。実際は 0178 で Java 11+ の `Deflater.setInput(ByteBuffer)`、`AthenaSectionStore` で Java 22+ の FFM API を使っており、Java 25 が必須です。athena.yml、BungeeGuard、1.7.10 対応、Athena API の説明もありません。

---

## ✅ 読んで問題がないと判断したもの

テストで裏付けたものではありません。気になる場合は既存テスト（括弧内）が通ることを確認してください。

- **パケット送信キュー（0308）とプロトコル切替（0316）**: 書き込み順、flush の位置、drain の取りこぼし防止（空になったらフラグを下ろしてもう一度確認する）はいずれも正しい。
- **フレーム処理と暗号化（0310）**: パディング付き 3 バイト varint は 1.7/1.8 クライアントとも読める。1.7 接続には圧縮が入らないので、prepender を飛ばしても 1.7 の変換ハンドラーを飛ばすことはない（`AthenaFramingTest`）。暗号化をその場で行っても、チャンクのキャッシュはエンコーダーでコピーされるので共有バッファを壊さない。
- **Inflater/Deflater の共有（0258）**: `reset()` が finally にあり、壊れた zlib データが同じイベントループの他の接続に影響しない。
- **1.7 クライアントの NBT 読み込み上限（0273）**: 1.8 と同じ 50,000 の上限で読む（`AthenaLegacy17NbtLimitTest`）。
- **BungeeGuard 本体（0315）**: 定数時間比較、トークンが 1 つでない場合の拒否、プロフィールからのトークン除去（`AthenaBungeeGuardTest`）。指摘 5 のイベント経路を除く。
- **チャンク表のダイレクトマップ（0278）**: `isLoaded` と座標で検証し、アンロード時にスロットを消すので、古いチャンクを返さず、アンロード済みチャンクを保持しない。
- **チャンクパケットのキャッシュ（0250 / 0297 / 0304）**: `modCount` とセクションの同一性、バイオームで鍵を作っており、圧縮は 1 スレッドで 1 回だけ（`AthenaChunkPacketCacheTest`）。

---

## 判断用チェックリスト

| # | 指摘 | 確認の手間 | 再現した？ | 対応 |
|---|---|---|---|---|
| 1 | 装飾 escape の競合 | 中（一時コードで強制 escape） | | |
| 2 | decorationPending のチャンクが残る | 小 | | |
| 3 | ワールドが ThreadLocal に残る | 小（`jcmd` のヒストグラム） | | |
| 4 | 2 MiB の確保の増幅 | 小（JUnit） | | |
| 5 | BungeeGuard がイベント経路で飛ぶ | 中（テストプラグイン＋クライアント） | | |
| 6 | API のスレッド確認なし | 小 | | |
| 7 | Guard が ping に依存 | 大（遅延クライアント） | | |
| 8 | Guard のデフォルトが強制 | 運用判断 | | |
| 9 | セクションストアの配列外参照 | 小 | | |
| 10 | 停止時の送信キュー | 不要 | | |
| — | CI の JDK | 小（Actions の履歴） | | |
