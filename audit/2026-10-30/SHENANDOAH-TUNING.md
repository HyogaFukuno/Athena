# 世代別 Shenandoah の CPU と RSS を抑える調査（2026-10-30）

[RESULTS-JAVA25-PERF.md](RESULTS-JAVA25-PERF.md) の P05 の続きです。世代別 Shenandoah は GC の停止を最大 27.9 ms から 0.36 ms に減らしました。その一方で、RSS のピークは 314 → 377 MB、CPU は +44% に増えていました。この増加を、フラグとコードの両方で抑えられるかを調べました。

## 1. 結論

**フラグの調整で、RSS は Parallel と同じ水準まで下がり、CPU の増加は +44% から +13% まで縮みました。GC の停止は約 1 ms のままです。**

| | Parallel（3 試合） | 世代別の既定（1） | **調整版 genfinalnp（2）** |
|---|---|---|---|
| CPU（プロセスの合計、試合 1 回） | 50.7 秒 | 71.1 秒（+40%） | **58.5 秒（+15%）** |
| CPU（コア、試合中の平均） | 0.120 | 0.173（+44%） | **0.136（+13%）** |
| うち GC スレッド | 0.4 秒 | 13.7 秒 | **2.0 秒** |
| うち JIT | 28.5 秒 | 32.5 秒 | 32.7 秒 |
| RSS のピーク | 307〜312 MB | 373 MB | **308〜331 MB** |
| GC の停止の最大 | 25〜31 ms | 0.5 ms | **1.1〜1.3 ms** |
| 50 ms を超えた tick（試合全体） | 2〜4 回 | 2 回 | 2〜4 回 |
| tick の平均 | 1.10 ms | 1.34 ms | 1.19 ms |
| Degenerated GC・Full GC | — | 0 | 0 |

調整版のフラグは次のとおりです（`start-small.sh` の `-XX:+UseParallelGC` と `-Xms/-Xmx` の置き換え）。

```
-Xms48M -Xmx112M
-XX:+UseShenandoahGC
-XX:ShenandoahGCMode=generational
-XX:+UnlockExperimentalVMOptions
-XX:ShenandoahControlIntervalMin=50
-XX:ShenandoahControlIntervalMax=999
-XX:SoftMaxHeapSize=96M
-XX:ShenandoahUncommitDelay=5000
-XX:-ShenandoahPacing
```

**本番の `start-small.sh` は変えていません**（Parallel のままです）。採用するかどうかは、§5 の注意点を踏まえて決めてください。

コードの側では、試合中の割り当ての 49% を占めていた送信まわりの無駄を取り除きました（server 0382、§4）。この修正は、どちらの GC にも効きます。

## 2. 計測の方法

- 条件：VPS の sgbench3、Return_To_Moonbase_9、ボット 20 体、1 試合 296 秒、本番と同じ athena.yml（io_uring、圧縮レベル 1）。
- ベンチは sgbench3 専用の AOT キャッシュで動かし、本番の共有キャッシュには触れていません。終わったあと、sgbench3 は元のファイルに戻して停止しました。
- 今回から次の 2 つを記録しました。
  - **スレッドごとの CPU**：`/proc/<pid>/task/*/stat` を 5 秒ごとに読みました（`j25-threads.sh`）。
  - **ヒープの領域の RSS**：Parallel のときだけ取れました。`-Xms` と `-Xmx` が違うと、ヒープの領域が複数に分かれて、測り方が合わなくなるためです。
- 試合は全部で 21 回です。変種ごとの回数は 1〜3 回と少なく、試合ごとのばらつきもあります。1 回だけの変種の数値は、目安として見てください。

## 3. 何が増えていたのか

### 3.1 CPU：GC の回収ではなく、制御スレッドの空回り

世代別の既定では、GC 関係のスレッドが 1 試合で 13.7 秒の CPU を使っていました。内訳は次のとおりです。

| スレッド | CPU |
|---|---|
| Shenandoah Control（GC の開始を判断する） | 6.7 秒 |
| Shenandoah Regulator（世代別で、若い世代と古い世代を振り分ける） | 5.9 秒 |
| GC ワーカー（実際の回収） | 1.1 秒 |

- GC は、試合全体で 16 回しか動いていません。それなのに、制御の 2 本のスレッドは `ShenandoahControlIntervalMin/Max`（既定は 1 ms と 10 ms）の間隔で起き続けていました。stime が多いのは、待機と起床を何度も繰り返しているためです。
- 割り当ては試合中で数 MB/秒なので、空きが 40 MB あれば 10 秒もちます。判断の間隔は 1 秒でも十分です。
- 間隔を延ばしたときの、GC スレッドの CPU：

| 制御の間隔 | GC スレッドの CPU |
|---|---|
| 1〜10 ms（既定） | 13.7 秒 |
| 10〜100 ms | 3.6〜4.1 秒 |
| 50〜999 ms | 1.6〜2.1 秒 |

  - 999 ms は指定できる上限です。1000 では JVM が起動しませんでした。
  - 間隔を延ばしても、Degenerated GC（並行の回収が間に合わずに止まって回収するもの）は 1 回も起きていません。
- 世代別をやめた satb モードでも、Control スレッドだけで 7.8 秒使っていました。合計の CPU は、世代別と変わりませんでした（70.0 秒）。

### 3.2 残る差：JIT

- 調整後に残る CPU の差（+7.8 秒）は、ほとんどが JIT です。JIT は Parallel で 26.9〜30.7 秒、Shenandoah で 28.4〜35.6 秒でした。
- Shenandoah のバリアが入ったコードは、コンパイルの量が増えます。フラグで抑える手段は見つかりませんでした。
- なお、Parallel でも JIT がプロセスの CPU の 55〜57% を占めています。GC とは別の課題として残しておきます（JDK 25 の AOT キャッシュは、コンパイル済みのコードまでは持てません）。
- AOT キャッシュは、どちらの GC でも同じように使われていました（起動 1.18〜1.31 秒、警告も同じ）。

### 3.3 RSS：ヒープを使い切ってから回収し、返さない

- 既定の動きはこうでした。
  - 「空きが 10%（14.7 MB）を切ったら回収を始める」ので、ヒープ 144 MB をほぼ使い切ってから回収していた。
  - `-Xms` と `-Xmx` が同じ値なので、uncommit（空いた領域を OS に返す機能）が無効になっていた（GC のログに「Min heap equals to max heap, disabling ShenandoahUncommit」）。
- その結果、ヒープの領域の RSS は 142 MB で、Parallel の 101〜103 MB よりはるかに大きくなっていました。
- 打った手と結果は次のとおりです。

| 設定 | RSS のピーク |
|---|---|
| 既定 | 373 MB |
| `SoftMaxHeapSize=96M`、`-Xms48M`、`ShenandoahUncommitDelay=5000`（空いた領域を 5 秒で返す） | 321 MB（genmem） |
| 上に加え、制御の間隔を延ばしたもの | 337〜356 MB |

- 間隔を延ばすと RSS が高くなったのは、ピークが試合の終わり（マップを捨てて全員をロビーへ戻すとき）に出ていたためです。割り当てが一気に増え、ヒープが 136〜139 MB まで広がっていました。`SoftMaxHeapSize` は、この急な増加の間は上限として効きません。
- そこで、上限そのものを `-Xmx112M` にしました。結果は RSS **308〜326 MB** です。GC のあとに生きている量は約 50 MB なので、112 MB で足りていました（Degenerated GC と Full GC は 0）。

### 3.4 pacing と tick の遅れ

- 上限を 112 MB にした genfinal では、50 ms を超えた tick が 3 回と 9 回でした（Parallel は 2〜4 回）。
- 出ていたのは、試合前のマップの preload と、テレポートの最中です。ヒープが小さくなった分、Shenandoah の pacing（GC が追いつかないときに、割り当てたスレッドを少し待たせる仕組み）が、サーバースレッドに効いていると判断しました。
- `-XX:-ShenandoahPacing` にした genfinalnp では、2 回と 4 回で、Parallel と同じ水準に戻りました。tick の平均も 1.27 → 1.19 ms になっています。
- ただし、各 2 回の比較で、ばらつきの範囲が重なっています。pacing が原因だと言い切れるほどの回数ではありません。

## 4. コードの側：送信のたびの無駄（server 0382）

試合中の割り当てを Mac で JFR に記録したところ、上位の 3 つが送信まわりで、合わせて 49% を占めていました。

| 割り当て | 割合 | 原因 |
|---|---|---|
| `DefaultChannelPromise` | 21% | Netty へ書き込むたびに作られ、`FIRE_EXCEPTION_ON_FAILURE` のリスナーが付いていた |
| `NetworkManager$AthenaPendingWrite` | 14% | 下の配列のせいで、リスナーのないパケットまで包まれていた |
| `GenericFutureListener[]` | 13% | `handle()` が `sendPacket(packet, null, (GenericFutureListener) null)` と呼んでいた |

- 3 つ目は、可変長引数に要素の型でキャストした null を渡しているのが原因です。中身が null だけの配列が毎回作られ、`ArrayUtils.add` でもう 1 つ作られていました。
- 直したこと：
  - `handle()` は配列を渡さないようにしました。
  - リスナーの配列は、null を除いて作るようにしました。
  - リスナーのないパケットは、チャネルの void promise で書くようにしました。失敗したときの扱い（パイプラインに伝える）は、これまでと同じです。
- 結果：
  - 送信 1 回あたりの割り当て（実際のパイプラインで、エンコードを含む）：**202 → 90 バイト**
  - 試合中の Netty スレッドの割り当て：405〜453 → 176〜179 KB/秒
- 直す途中で、別の不具合も見つかりました。`sendPacket(packet, null, listener)` の形で呼ぶと、`[null, listener]` という配列になり、Netty の `addListeners` が先頭の null で処理をやめるので、**リスナーが呼ばれていませんでした**。null を除くことで、これも直っています。`AthenaSendPathTest` は、修正前のクラスでは失敗します。
- void promise は、プラグインがパイプラインに入れたハンドラーが promise にリスナーを付けると例外になります。そのため、`ChannelInitializeListener` が登録されているときと、`-Dathena.disableVoidWritePromises=true` のときは、これまでどおり普通の promise を使います。

## 5. 本番に入れる前の注意

- **CPU は、まだ Parallel より 13〜15% 多い**です。0.12 コアが 0.136 コアになる程度ですが、2 台が同じ 2 vCPU を使っている VPS-B では、その分だけ余裕が減ります。
- 調整版は実験的なフラグ（`UnlockExperimentalVMOptions`）を使います。JDK を更新するときは、フラグがまだ使えるか、範囲が変わっていないかを確かめる必要があります。
- `-Xmx112M` と pacing なしは、**Moonbase_9 でしか確かめていません**。ヒープを多く使うマップ（Lobby_Games など）で、GC が追いつかずに Degenerated GC（止まって回収する）が起きないかを、先に確かめてください。起きると、一度に長く止まります。
- 試合の回数が少ないので、本番に入れるなら、sg2 など一方のサーバーで、数日分の試合を GC のログとあわせて見るのが確実です。

## 6. 資料

- 生の結果：[java25-bench/results/vps-shen/](java25-bench/results/vps-shen/)
  - 試合ごとの JSON・athena-diag.csv・gc.log・スレッドの CPU（`*.threads`）
  - `summary.csv`
  - 使った起動スクリプト（`scripts/start-small.*.sh`）
- スレッドの集計：`threads_vps.py`。試合の集計：`analyze_vps.py`。
- 送信の割り当ての計測：`probes/SendAllocProbe.java`、`results/send-alloc.txt`。
