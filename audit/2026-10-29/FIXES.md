# 監査レビューに基づく修正記録 — 2026-10-29

作業ブランチはルート・生成Serverツリーとも `fix/audit-2026-10-29`。masterには直接コミットしていません。push・PR作成・本番反映は実施していません。API生成ツリーは変更していません。

## 修正した範囲

| 項目 | 対応 |
|---|---|
| F01 | 設定全体とworld設定を先に型検証。移行先を一時ファイルから原子的に公開後、元YAMLを `.bak` にバックアップして元キーを除去。移行先保存失敗時は元を保持。既存athena.ymlを優先する挙動は維持 |
| F02 | IO/生成完了が破棄済みWorldへチャンクを再登録しないよう防御。Bukkit非同期callbackからの同期再ロードを停止。preloadは破棄された結果を成功に数えない |
| F03 | loader/regionのreadとcloseをread-write lockで保護。workerから見えるunloadedフラグをvolatile化。破棄済みWorldのIOExceptionだけを無視し、live WorldのIOExceptionとErrorは伝播 |
| F05 | 装飾の例外時もcompletionとholds解放を実行。physicsフラグをfinallyで復元。worker Errorはserver threadへ伝播。save=falseでも当該Worldの装飾をflush |
| F06 | unload用のWorld指定flushのみ追加。全体のtick予算やbusy waitは変更せず |
| F07 | offline/proxy経路で、非同期認証の投入より前にAUTHENTICATINGへ遷移。poolの上限は変更せず |
| F08 | チャンク所属判定のZ一致をAND条件に修正 |
| F10 | 有限座標・整数範囲の検証。最大65,536チャンク。longで総数計算、indexを32bitマスク。skipもperTick×16の走査上限へ算入。長い矩形の距離キーとINT_MAXのループ終端も修正 |
| F11 | pinned範囲外の非同期APIでも座標付きCraftChunkを返す。破棄済みWorldではcallbackを通知しない |
| F12 | 新しいノードへのパケット別制限の数値保存を修正 |
| F13 | 通常/遅延NBT配列のequalsとhashCodeをgetterに統一。blocksをvolatileにしてdata/addも安全に公開 |
| F15 | key tableの範囲・並び、entry kind、blob範囲、palette index、slim長を検証。slimは最大1MiB、超える通常チャンクは元regionを使用。欠落section/破損メタデータではチャンク全体をregionから読み直す |
| F17 | throttleの期限切れをtimestampではなく経過時間で判定 |
| F18 | 送信側とpreload側で圧縮キャッシュの利用条件を共通化 |
| F19 | pull_requestでもCIを実行し、setup後にAPI/Serverテストが成功してからJARをビルド |

`getChunkAtAsync` のWorld破棄時は、レビューで許容された「callbackを実行しない」扱いです。callbackに成功/失敗の型がないためnull通知は行いません。これを包むteleportAsyncのFutureへ取消しを通知する仕組みや、タスクをWorld単位で全廃棄する設計までは追加していません。

設定のatomic rename非対応のファイルシステムでは、安全でない上書きへfallbackせず起動エラーにします。移行元は削除されません。

## 生成ツリーのコミット・パッチ

`./panda rb` で既存0354の後へ0355〜0361を追加しました。既存パッチは変更していません。

| patch | Server commit | 題名 |
|---|---|---|
| [0355](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0355-Wait-for-template-reads-before-close-and-discard-unl.patch) | `17f51e74c` | Wait for template reads before close and discard unloaded chunk results |
| [0356](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0356-Fix-audited-entity-throttle-NBT-limiter-and-cache-po.patch) | `5b48c3565` | Fix audited entity, throttle, NBT, limiter and cache policy regressions |
| [0357](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0357-Bound-preload-dimensions-and-charge-skipped-chunks-t.patch) | `2916e0749` | Bound preload dimensions and charge skipped chunks to the tick scan budget |
| [0358](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0358-Publish-migrated-settings-atomically-before-backing-.patch) | `78b62f0a6` | Publish migrated settings atomically before backing up and removing the source |
| [0359](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0359-Release-decoration-holds-and-restore-physics-state-w.patch) | `f9c7e9ac4` | Release decoration holds and restore physics state when population fails |
| [0360](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0360-Validate-mapped-section-stores-and-retry-corrupt-cac.patch) | `defb576ee` | Validate mapped section stores and retry corrupt cached chunks from region NBT |
| [0361](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0361-Publish-deferred-section-arrays-safely-to-concurrent.patch) | `b665297ac` | Publish deferred section arrays safely to concurrent NBT readers |

## ルートのコミット

| commit | 題名 |
|---|---|
| `a7f6bea` | Wait for template reads before close and discard unloaded chunk results (server 0355) |
| `8f6584e` | Fix audited entity, throttle, NBT, limiter and cache policy regressions (server 0356) |
| `0eda21c` | Bound preload dimensions and charge skipped chunks to the tick scan budget (server 0357) |
| `2df560a` | Publish migrated settings atomically before backing up and removing the source (server 0358) |
| `f1b54aa` | Release decoration holds and restore physics state when population fails (server 0359) |
| `6250389` | Validate mapped section stores and retry corrupt cached chunks from region NBT (server 0360) |
| `3e9f1e7` | Publish deferred section arrays safely to concurrent NBT readers (server 0361) |
| `eaac162` | Run API and server tests before building pull requests |

この記録ファイルは、上記の実装修正・CIの後に独立した文書コミットとして保存します。

## 検証結果

実行コマンド:

```sh
./gradlew :pandaspigot-server:test :pandaspigot-api:test --offline --console=plain --rerun-tasks
```

Java 25 toolchainで全タスクを再実行し成功しました。

| 対象 | ケース数 | failure | error | skip |
|---|---:|---:|---:|---:|
| API | 988 | 0 | 0 | 3 |
| Server | 5558 | 0 | 0 | 1 |
| 合計 | 6546 | 0 | 0 | 4 |

追加回帰テストは **28件**。baselineのスキップ・テスト除外条件は変更していません。

| テストクラス | 件数 | テスト名 |
|---|---:|---|
| AthenaChunkLifecycleTest | 4 | asyncPinnedChunksHaveCoordinatesAndUnloadedWorldsDoNotCallback; anIoFailureDuringUnloadFinishesWithoutThrowing; vmErrorsAreNeverHiddenEvenAfterUnload; aLiveWorldIoFailureStillPropagates |
| AthenaTemplateCloseTest | 1 | closeWaitsUntilTheActiveReadFinishes |
| AthenaAuditRegressionTest | 6 | offlineLoginLeavesHelloBeforeSchedulingAuthentication; throttleCleanupKeepsRecentAndBoundaryEntries; deferredNbtHasSymmetricEqualityAndHashing; anUnregisteredOrDisplacedEntityIsNotAddedToItsChunk; cachePolicyRejectsDisabledAntiXrayAndUncompressedWorlds; packetLimitsSurviveSerializationIntoANewNode |
| AthenaPreloadBoundsTest | 5 | longStripsRemainNearestFirstAndMaxIntDoesNotLoop; aMissingOrDiscardedLoadDoesNotCountAsLoaded; rejectsOverflowAndReversedAndOversizedRangesBeforeAllocation; chunkSquaresRejectNonFiniteCoordinates; loadedAndMissingChunksConsumeTheScanBudget |
| AthenaConfigSafetyTest | 3 | successfulMigrationBacksUpTheExactSourceBeforeRemoval; destinationWriteFailureLeavesTheSourceUntouched; invalidGlobalOrWorldSettingsLeaveTheOriginalSecretsIntact |
| AthenaDecorationCleanupTest | 4 | unloadingOneWorldDoesNotWaitForAnotherWorldsDecoration; generatorFailureStillRunsCleanupAndKeepsTheOriginalError; workerErrorsReachTheServerThreadAndReleaseCleanup; pluginFailureReleasesAllFourHoldsAndRestoresPhysics |
| AthenaSectionStoreSafetyTest | 4 | missingStoredSectionsRetryTheWholeChunkFromOriginalNbt; corruptCompressedMetadataRetriesTheWholeChunkFromOriginalNbt; badEntryKindsOffsetsAndTableOverflowAreRejected; anIntMaxSlimLengthInA441ByteFileFallsBackToTheRegion |
| AthenaDeferredNbtConcurrencyTest | 1 | concurrentReadersSeeAllThreePackedArrays |

- 0355〜0361を元Server HEAD `c02f1d3c2dd22ca672383376b50cdd91cb9afd1c` の一時indexへ順次適用し、最終ツリーが生成ツリーHEADと完全一致しました。元のcheckoutやindexをリセットしていません。
- 再現対象の読込とcloseの競合は、実regionを読むFileChannelをlatchで止め、closeが読み終わりを待つことをテストしています。
- BlockPopulator例外で4チャンクの保持が0へ戻ること、Errorが握り潰されないこと、別Worldを待たないflushをテストしています。
- testsのfixtureだけでUnsafe.allocateInstanceを使い、実サーバーの起動・認証・背景サービスを開始せずにWorld等を作成しています。本体へUnsafe利用を追加していません。
- macOS上の検証です。Linuxのepoll/io_uring、Velocityとの実接続、長時間SG負荷、cgroupでのメモリ/CPU性能、GitHub Actionsのリモート実行は未実施です。

## 配布物の検証

`./gradlew :paperclip:paperclipJar --offline --console=plain` が最終ソースで成功しました。成果物は [Paperclip JAR](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/paperclip/build/libs/paperclip-1.8.8-R0.1-SNAPSHOT.jar) です。ルートの既存 `paperclip.jar` は置き換えていません。

生成時のルートcommitは `eaac162`、Server commitは `b665297ac` です。既存のMojang JARを一時cacheへコピーし、Java25で `-Dpaperclip.patchonly=true` を実行しました。ネットワークからの取得や本番サーバー起動は行っていません。展開後のSHA-256が埋め込みpatchedHashおよびServerビルド結果と一致しました。

```text
paperclip SHA-256: a173ccda9a814a3eee843e2ddb2fa32e31ebc8f0bfc5fcb0144597d9d5ba34b8
patched SHA-256: a59920a3bc14df1733e5c74790fdefdcf359a61cd85aed8b81bd6ff5ac61a748
```

closeは実行中の読み込みを待つため、遅いIOはWorld破棄の所要時間に影響します。またstoreのmap時検証はpalette indexの走査を含むため、初回mapのコストが増えます。これらの実運用での遅延・CPU/メモリ影響は未計測です。

## 今回変更していない項目

| 項目 | 理由・次の判断 |
|---|---|
| F04 | 非同期装飾の依存関係/地形同値性は設計判断が必要。デフォルトtrueを含めて維持。同期版とのseed固定比較を先に行う案 |
| F06の残り | globalなtick予算・batch間公平性・Thread.yield待機の全面変更は計測後に決める。今回のWorld指定flushは破棄時の正確性に限定 |
| F09 | 本番は固定threshold。拒否の追加もプラグインとの互換性を変えるため保留 |
| F14 | 下記の方式選択が必要。metadata fingerprintとstore形式は維持 |
| F16 | 本番はasync lighting無効。executorの終了と既存タスク排出の方式は別途決める |
| F20 | read-only配列共有は意図された設計。コピー/書き込みAPIへの変更は互換性方針の判断待ち |
| 性能改善案 | 今回は機能の正確性を修正。速度向上率やRSS削減率は測っておらず主張しない |

## F14: 内容hashの方式について

「サイズかmtimeが変わったときだけhashを再計算する」方式は、同じサイズ・mtimeへ戻した変更を検出できません。storeへCRC32CやxxHashを保存するだけでも解決しません。検出条件とコストの選択を明確にする必要があります。

1. **公開時に内容hashと不変revisionを作る方式（推奨案）**: マップのビルド/配布段階で全regionをhashし、revision付きの新ディレクトリ/manifestとして公開。サーバーはrevisionをstoreのキーに使う。各試合開始で全regionを読む必要はない。配布手順とstoreキーの仕様変更が必要。
2. **ロードごとに全regionをhashする方式**: 配布手順に依存せず同mtimeの変更も検出できる。そのWorldを開くたびにマップ全体を読むため、小さいVPSでのCPU・IOとcold page cacheへの影響を測る必要がある。
3. **metadata変更時だけhashする方式**: コストを抑えられるが、同mtime・同サイズの変更は検出できないという制約を受け入れる必要がある。

今回はレビューの「案を書き残す」指示に従い、方式を勝手に選んで保存形式や配布手順を変更していません。
