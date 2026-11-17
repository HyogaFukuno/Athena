# Athena の精査 — 2026-11-17

## 対象と方法

- **対象**：`origin/master` `1eb6999`（server 0455・api 0070）。
- **実測**：VPS-B の JFR `run/vps-out/r-b-1/sg.jfr`（2026-11-15、server 0452、8 部屋、ネットワーク越しのボット約 112 体と SG のボット）。0453〜0455 は重い経路を変えていない。
- **数字**：サンプルの割合（全 63,091 個）と確保の重み（2,882 MB）。効き目は見積もりで、前後の計測はしていない（A1・A3 は `RESULTS.md` で確かめた）。
- **読んだ所**：`AthenaWorldThread`、`WorldServer` のスレッド周り、`NetworkManager` の送信、受け渡し、0455 の差分、static な共有状態、本番設定（ArcanaServers `7c739e4`）、SG のボットの経路探索。

## まとめ

- クラッシュやデータ破損につながる新しい問題はない。
- CPU は world スレッドが 71.2%（うち SG のボットの AI 25.2%、移動パケットの処理 12.1%、ボットの体の移動 7.2%）、Netty 18.7%、サーバースレッド 7.1%。

## 高

| # | 内容 | 裏付け | 直し方 |
|---|---|---|---|
| A1 | メインワールド `lobby` のスポーン周辺 625 チャンクがヒープに常駐する（`paper.yml` の `keep-spawn-loaded: true`） | 起動時の読み込みがサーバースレッドの 29%、`NBTTagByteArray.load` が確保の 7.2% | `keep-spawn-loaded: false`（**確かめた**：`RESULTS.md`） |
| A2 | SG のボットの経路探索が、1 tick 12 ms の予算を使い切っている（`Navigator.PLAN_NANOS_PER_TICK`） | `Navigator.plan` が 20.3%、`Terrain.flags` の自分の時間 12.4% | `-Dsgbots.planNanosPerTick` を 4〜6 ms にして A/B。根本は D1 |
| A3 | `CraftScore.getScore` が呼ぶたびに全エントリ名を写して線形に探す | `Scoreboard.getPlayers` 経由の確保 92 MB（3.2%） | server 0456（**直した**） |

## 中

| # | 内容 | 裏付け | 案 |
|---|---|---|---|
| B1 | 同じパケットを見ている人数ぶんエンコードする（`NetworkManager.athenaFrame`） | 全体の 4.8%（Netty の 25.6%） | 最初のエンコード結果をパケットに控えて写す。送ったあとで変わるパケットと 1.7 の接続は除く |
| B2 | io_uring の受信が読みのたびにバッファと `IoUringIoOps` を作る | バッファの確保が Netty の 9.7%、`IoUringIoOps` が確保の 3.35% | Netty 4.2 の buffer ring を試す（API とカーネルの要件は未確認） |
| B3 | イベントの呼び出しが `MethodHandle.invokeExact` 経由で、C1 が畳まない | `LambdaForm$DMH.invokeSpecial` の自分の時間 1.25% | public なハンドラは、プラグインのクラスローダーの子に作ったクラスから `invokevirtual` で呼ぶ |
| B4 | 移動パケット 1 個で当たり判定を 4 回（`onUpdateEntity` の物理、`hasCubes` ×2、本物の移動） | `processPlayer` が 12.1% | 物理の移動の `motY` がノックバックに効くので、安全に省く条件は難しい。D3 |
| B5 | マップしたセクションの読み出しが C1 で重い | `checkValidStateRaw` 0.8%、`mappedStateIdAt` 系で world の約 4% | `MappedByteBuffer` の絶対読みで A/B（仮説） |
| B6 | `-DPaper.disableFlushConsolidate=true` の A/B が未実施（11-14 の宿題） | `FlushConsolidationHandler` の flush が Netty の 4.6% | フラグで試す |

## 低

| # | 内容 | 条件 | 直し方 |
|---|---|---|---|
| C1 | `UserCache` に同期がない。`getOfflinePlayer(名前)` を world スレッドから呼ぶと競合し、知らない名前なら tick スレッドで Mojang に問い合わせる | SG は呼んでいない | 公開メソッドを `synchronized` に |
| C2 | 終わりかけの world スレッドにワールドが加わると、誰も tick しない（`runLeaves` と `athenaJoinThread` の間） | `leave` の時間切れの後だけ。読んだだけ | 加える側と止める側を同じロックで守る |
| C3 | `NextTickListEntry.nextTickEntryID` が同期なしの static | 同時刻・同優先度のブロック tick の順番が入れ替わるだけ | ワールドごとのカウンタに |
| C4 | 取り消し済みのパッチ 9 組と WIP 1 つ（0183/0184 ほか、0193） | 害はない | 対を畳む |
| C5 | CI は単体試験だけ | — | テンプレートのワールドと少数のボットで 1 分回す試験を足す |

## 処理を大きく変える案

- **D1 ボットの経路をマップごとに焼く（SG）**：テンプレートは変わらないので、立てる場所の表と、よく向かう先への距離の場をマップごとに作り、部屋どうしで共有する。全体の 20% の塊で、最大の伸びしろ。
- **D2 送信のエンコードを world スレッドへ（B1 の先）**：トラッカーが送る時点で 1 回だけバイト列にし、各接続には同じバイト列を渡す。
- **D3 プレイヤーの tick の作り直し（B4 の先）**：足もとのセクションの版が同じ間は物理の移動の答えを使い回す。手応えが変わる危険に対し、見込みは 1〜2%。
- **D4 メインワールドを通らないログイン（A1 の先）**：A1 を設定で片づけたので急がない。
