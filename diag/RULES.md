# 診断キットの判断基準

`athena_diag_report.py` の `rules()` が使っている基準です。数字の出どころは、Athena の開発中の計測（ConoHa 1GB / 2vCPU の VPS、20人の SurvivalGames の試合、bot 20体）です。

| 区分 | 意味 |
|---|---|
| 要対応 | 今の設定で、プレイヤーに見える問題が出ている |
| 推奨 | 計測の結果から、変えた方がよい |
| 要判断 | ゲームの内容によって決める（運営者が決める） |
| 問題なし | 今のままでよい |

## ヒープ（-Xms / -Xmx）

- **生きているヒープ**＝GC後に残ったヒープの最大（フルGC・ヤングGCの両方）。安全側の値：試合前のフルGCはマップを読み込む前に起きることがあり、その値だけでは小さすぎる（実測：フルGC後 40 MB、その後のGC後 91 MB）。
- **推奨値**＝生きているヒープ × 1.45 を 16 MB 単位で切り上げ。最低 128M、フルGCがなく生きているヒープが上限しか分からないときは最低 144M。
  - 根拠：生きているヒープ約100 MBで、144M（1.44倍）なら15分の試合でフルGCが0回、128M（1.28倍）では3回（120〜165 ms）。
- **要対応**：メモリ不足によるフルGC（原因が CodeCache や System.gc ではないもの）が起きていて、推奨値が今の値より大きい。推奨値は生きているヒープ × 1.6。
- **推奨（減らせる）**：今の値が推奨値の1.5倍より大きい。空くメモリ＝今の値 − 推奨値。計測が1時間未満か、フルGCがなかったときは「要判断」にして、長い計測で確かめてから変える。
- Xms は Xmx と同じにする（起動後にヒープの大きさが変わらないように）。

## GC の種類

- G1 で、ヒープ（減らす提案があれば提案後の値）が 1 GB 以下 → **推奨** Parallel。G1 は Parallel / Serial より約115 MB多く使った。
- Serial で、CPU が2個以上 → **推奨** Parallel。フルGCの停止が 125〜185 ms から 77〜107 ms に（メモリと CPU は同じ）。

## JVM のフラグ

| 条件 | 区分 | 変更 | 根拠 |
|---|---|---|---|
| 「CodeCache GC Threshold」のフルGC がある | 推奨 | `-XX:ReservedCodeCacheSize=48m -XX:SweeperThreshold=50` | 初期値では試合開始2分で50〜200 msのフルGCが5〜6回、この設定で0回 |
| Java 25 以上で、CompactObjectHeaders がない | 推奨 | `-XX:+UseCompactObjectHeaders` | 飽和負荷で TPS +0.57% |
| MALLOC_ARENA_MAX がない | 推奨 | `MALLOC_ARENA_MAX=2` と `-XX:TrimNativeHeapInterval=5000` | 329 MB → 273 MB |
| AOT キャッシュがない | 推奨 | `deploy/start-small.sh` で起動 | ネイティブメモリ約35 MB減、起動直後の JIT が軽い |
| AOT キャッシュはあるが ArchiveRelocationMode が 0 でない | 推奨 | `-XX:+UnlockDiagnosticVMOptions -XX:ArchiveRelocationMode=0` | これがないと AOT の節約がほぼ消えた |

## サーバーの設定

| 条件 | 区分 | 変更 | 根拠 |
|---|---|---|---|
| netty-threads が2以上で、CPU が2個以下 | 推奨 | spigot.yml `settings.netty-threads: 1` | Netty の CPU時間 13.1 s → 11.6 s |
| chunkUnloadMargin が 0 | 要判断 | pandaspigot.yml `chunkUnloadMargin: 2` | Netty −22%、通信量 −83%（境界を往復する最悪のケース）。チャンク1つ約11 KB増える |
| アンチXray が有効 | 要判断 | spigot.yml `world-settings.default.anti-xray.enabled: false` | チャンクのキャッシュが効き、CPU 0.475 → 0.14 コア。鉱石の位置が勝負に関係しないマップなら |

## マシン

| 条件 | 区分 |
|---|---|
| マシンの空きメモリの最小が全体の10%未満 | 要対応 |
| CPU の steal の平均が5%超 | 要対応（設定では直せない。プランやホストの変更） |
| 50 msを超えた tick があり、5秒間の最低 TPS が 19.5 未満 | 要対応（プロファイルで原因を調べる） |
| 1回の tick が 250 ms 以上 | 要対応（時刻と、その時のプレイヤー数・チャンク数・ワールド数を表示。平均の TPS には表れない） |
