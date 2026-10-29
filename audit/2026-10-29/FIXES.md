# 監査レビューに基づく修正記録 — 2026-10-29

作業ブランチはルート・生成Serverツリーとも `fix/audit-2026-10-29`。masterには直接コミットしていません。push済みで、[PR #26](https://github.com/HyogaFukuno/Athena/pull/26)を作成しています。マージ・本番反映は実施していません。API生成ツリーは変更していません。

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
| F15 | key tableの範囲・並び、entry kind、blob範囲、slim長をopen時に検証。packedのpalette indexはapply時にsection単位で検証し、成功結果を共有。slimは最大1MiB、超える通常チャンクは元regionを使用。欠落section/破損メタデータではチャンク全体をregionから読み直す |
| F17 | throttleの期限切れをtimestampではなく経過時間で判定 |
| F18 | 送信側とpreload側で圧縮キャッシュの利用条件を共通化 |
| F19 | pull_requestでもCIを実行し、setup後にAPI/Serverテストが成功してからJARをビルド |

`getChunkAtAsync` のWorld破棄時は、レビューで許容された「callbackを実行しない」扱いです。callbackに成功/失敗の型がないためnull通知は行いません。これを包むteleportAsyncのFutureへ取消しを通知する仕組みや、タスクをWorld単位で全廃棄する設計までは追加していません。

設定のatomic rename非対応のファイルシステムでは、安全でない上書きへfallbackせず起動エラーにします。移行元は削除されません。

## PR #26レビューへの追加対応

- R1: open時のpacked payload全走査を廃止。keyとメタデータは一括コピーを使って検証し、検証用scratchは256チャンク分に制限。packed indexは実際のapply時に検証し、成功したsectionのbitを保持します。破損時はfalseを返して元regionへfallbackします。
- R2: 既存athena.ymlとpandaspigot.ymlのPOSIX permissionを一時ファイルに引き継いでからatomic moveします。新規ファイルは600を維持します。640の移行・reloadと新規600をテストし、非POSIXではこれらのテストをskipします。
- R3: readingStoreを先に判定し、slim復元中のstore無効化やapply失敗を明示的なIllegalStateExceptionにします。無効状態の直接呼出しと復元途中の無効化からregion再読込までをテストしました。
- R4: push・PRの状態と今回の検証結果を更新しました。
- R5: 下記に装飾workerのError伝播の挙動を記録しました。

### 振る舞いの変更

0359では、装飾workerのErrorを従来のcatch(Throwable)によるメインスレッドでの再試行から、job.failureを介してメインスレッドで再throwする扱いへ変更しました。Error時はサーバーが停止します。「Errorを握り潰さない」というレビュー方針に沿う意図した変更です。

### R1の計測結果

GraalVM 25/macOS、OSのページキャッシュを温めた状態。提供されたStoreOpenProbeを使い、各測定を新規JVMで実行しました。最初の引数にはregionを持たない一時パスを指定してクラス初期化のみを済ませ、次の引数の既存storeを測定します。JITを十分に温めた定常状態の測定ではありません。3巡でvariantの実行順を入れ替え、全variantを同条件で比較しました。元コードはc02f1d3c2、PR修正前は0361まで、修正後は0363までの全テスト再実行で生成されたクラスです。

| マップ | 元コード 1/2/3回 (ms) | PR修正前 1/2/3回 (ms) | 修正後 1/2/3回 (ms) |
|---|---|---|---|
| map_bayside | 7.7 / 6.5 / 5.6 | 54.6 / 53.1 / 53.0 | 10.2 / 10.4 / 10.1 |
| map_construct | 6.7 / 6.5 / 6.2 | 46.8 / 40.2 / 38.7 | 10.2 / 9.6 / 10.6 |
| map_par_72_revamp | 8.7 / 7.9 / 8.3 | 77.0 / 85.2 / 82.8 | 13.3 / 13.6 / 13.3 |

openの大きな退行は解消しましたが、元コードとの差はメタデータ検証分として残っています。特にpar_72は約13〜14msで、全マップ10ms以内を達成したという結果ではありません。cold page cacheやConoHaでの値、試合全体の性能は未測定です。

再現用ファイル: [probe](StoreOpenProbe.java)、[測定スクリプト](measure-store-open.py)、[全27測定のCSV](store-open-pr26-review.csv)。runtime classpathを記したファイルと、元/PR修正前/修正後のAthenaSectionStoreおよびinner classをそれぞれ含むクラスディレクトリを用意し、スクリプトの--variantで指定します。

## 生成ツリーのコミット・パッチ

`./panda rb` で既存0354の後へ0355〜0363を追加しました。既存パッチは変更していません。

| patch | Server commit | 題名 |
|---|---|---|
| [0355](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0355-Wait-for-template-reads-before-close-and-discard-unl.patch) | `17f51e74c` | Wait for template reads before close and discard unloaded chunk results |
| [0356](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0356-Fix-audited-entity-throttle-NBT-limiter-and-cache-po.patch) | `5b48c3565` | Fix audited entity, throttle, NBT, limiter and cache policy regressions |
| [0357](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0357-Bound-preload-dimensions-and-charge-skipped-chunks-t.patch) | `2916e0749` | Bound preload dimensions and charge skipped chunks to the tick scan budget |
| [0358](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0358-Publish-migrated-settings-atomically-before-backing-.patch) | `78b62f0a6` | Publish migrated settings atomically before backing up and removing the source |
| [0359](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0359-Release-decoration-holds-and-restore-physics-state-w.patch) | `f9c7e9ac4` | Release decoration holds and restore physics state when population fails |
| [0360](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0360-Validate-mapped-section-stores-and-retry-corrupt-cac.patch) | `defb576ee` | Validate mapped section stores and retry corrupt cached chunks from region NBT |
| [0361](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0361-Publish-deferred-section-arrays-safely-to-concurrent.patch) | `b665297ac` | Publish deferred section arrays safely to concurrent NBT readers |
| [0362](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0362-Validate-packed-sections-lazily-and-reject-invalidat.patch) | `ae61453f4` | Validate packed sections lazily and reject invalidated slim restores |
| [0363](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/patches/server/0363-Preserve-existing-POSIX-permissions-when-atomically-.patch) | `fd21ca413` | Preserve existing POSIX permissions when atomically saving settings |

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
| `086bd5f` | Document audit fixes, verification and deferred design decisions |
| `1f473b2` | Validate packed sections lazily and reject invalidated slim restores (server 0362) |
| `f80ff45` | Preserve existing POSIX permissions when atomically saving settings (server 0363) |

この記録と再現用ファイルは独立した文書コミットとして保存します。

## 検証結果

実行コマンド:

```sh
./gradlew :pandaspigot-server:test :pandaspigot-api:test --offline --console=plain --rerun-tasks
```

Java 25 toolchainで全タスクを再実行し成功しました。

| 対象 | ケース数 | failure | error | skip |
|---|---:|---:|---:|---:|
| API | 988 | 0 | 0 | 3 |
| Server | 5563 | 0 | 0 | 1 |
| 合計 | 6551 | 0 | 0 | 4 |

追加回帰テストは **33件**（今回5件追加）。baselineのスキップ・テスト除外条件は変更していません。

| テストクラス | 件数 | テスト名 |
|---|---:|---|
| AthenaChunkLifecycleTest | 4 | asyncPinnedChunksHaveCoordinatesAndUnloadedWorldsDoNotCallback; anIoFailureDuringUnloadFinishesWithoutThrowing; vmErrorsAreNeverHiddenEvenAfterUnload; aLiveWorldIoFailureStillPropagates |
| AthenaTemplateCloseTest | 1 | closeWaitsUntilTheActiveReadFinishes |
| AthenaAuditRegressionTest | 6 | offlineLoginLeavesHelloBeforeSchedulingAuthentication; throttleCleanupKeepsRecentAndBoundaryEntries; deferredNbtHasSymmetricEqualityAndHashing; anUnregisteredOrDisplacedEntityIsNotAddedToItsChunk; cachePolicyRejectsDisabledAntiXrayAndUncompressedWorlds; packetLimitsSurviveSerializationIntoANewNode |
| AthenaPreloadBoundsTest | 5 | longStripsRemainNearestFirstAndMaxIntDoesNotLoop; aMissingOrDiscardedLoadDoesNotCountAsLoaded; rejectsOverflowAndReversedAndOversizedRangesBeforeAllocation; chunkSquaresRejectNonFiniteCoordinates; loadedAndMissingChunksConsumeTheScanBudget |
| AthenaConfigSafetyTest | 5 | existingPermissionsSurviveMigrationAndReload; newConfigurationsKeepPrivateDefaultPermissions; successfulMigrationBacksUpTheExactSourceBeforeRemoval; destinationWriteFailureLeavesTheSourceUntouched; invalidGlobalOrWorldSettingsLeaveTheOriginalSecretsIntact |
| AthenaDecorationCleanupTest | 4 | unloadingOneWorldDoesNotWaitForAnotherWorldsDecoration; generatorFailureStillRunsCleanupAndKeepsTheOriginalError; workerErrorsReachTheServerThreadAndReleaseCleanup; pluginFailureReleasesAllFourHoldsAndRestoresPhysics |
| AthenaSectionStoreSafetyTest | 7 | packedPayloadIsValidatedOnApplyAndFallsBackToOriginalNbt; anInvalidStoreExplicitlyRefusesSlimSectionFallback; invalidationDuringSlimAssemblyRetriesTheOriginalRegion; missingStoredSectionsRetryTheWholeChunkFromOriginalNbt; corruptCompressedMetadataRetriesTheWholeChunkFromOriginalNbt; badEntryKindsOffsetsAndTableOverflowAreRejected; anIntMaxSlimLengthInA441ByteFileFallsBackToTheRegion |
| AthenaDeferredNbtConcurrencyTest | 1 | concurrentReadersSeeAllThreePackedArrays |

- 0355〜0363を元Server HEAD `c02f1d3c2dd22ca672383376b50cdd91cb9afd1c` の一時indexへ順次適用し、最終ツリーが生成ツリーHEADと完全一致しました。元のcheckoutやindexをリセットしていません。
- 再現対象の読込とcloseの競合は、実regionを読むFileChannelをlatchで止め、closeが読み終わりを待つことをテストしています。
- BlockPopulator例外で4チャンクの保持が0へ戻ること、Errorが握り潰されないこと、別Worldを待たないflushをテストしています。
- testsのfixtureだけでUnsafe.allocateInstanceを使い、実サーバーの起動・認証・背景サービスを開始せずにWorld等を作成しています。本体へUnsafe利用を追加していません。任意指摘の置換は見送りました。ReflectionFactoryも内部APIで、通常コンストラクタへの変更は背景サービス等を起動するため、今回の範囲で安全に警告だけを除去できる変更とは判断していません。
- macOS上の検証です。Linuxのepoll/io_uring、Velocityとの実接続、長時間SG負荷、cgroupでのメモリ/CPU性能は未検証です。今回の追加修正のGitHub Actions結果はPR上で確認します。

## 配布物の検証

`./gradlew :paperclip:paperclipJar --offline --console=plain` が最終ソースで成功しました。成果物は [Paperclip JAR](/Users/glacier/Documents/Minecraft/developments/sandbox/Athena/paperclip/build/libs/paperclip-1.8.8-R0.1-SNAPSHOT.jar) です。ルートの既存 `paperclip.jar` は置き換えていません。

生成時のルートcommitは `f80ff45`、Server commitは `fd21ca413` です。既存のMojang JARを一時cacheへコピーし、Java25で `-Dpaperclip.patchonly=true` を実行しました。ネットワークからの取得や本番サーバー起動は行っていません。展開後のSHA-256が埋め込みpatchedHashおよびServerビルド結果と一致しました。

```text
paperclip SHA-256: 45d9191d5e511724a073c54bd4024162eb2a7858b9c261c655bbb67bc294a3d8
patched SHA-256: ba62e768a79bcef8bac8e06dbc82c7ed270521d3b9ceab684a4fae4e7becb96a
```

closeは実行中の読み込みを待つため、遅いIOはWorld破棄の所要時間に影響します。storeはmap時にメタデータを検証し、packed payloadの検証は利用時まで遅延します。openの手元計測は上記のとおりで、実運用でのCPU・メモリ・遅延への影響は未計測です。

## 今回変更していない項目

| 項目 | 理由・次の判断 |
|---|---|
| F04 | 非同期装飾の依存関係/地形同値性は設計判断が必要。デフォルトtrueを含めて維持。同期版とのseed固定比較を先に行う案 |
| F06の残り | globalなtick予算・batch間公平性・Thread.yield待機の全面変更は計測後に決める。今回のWorld指定flushは破棄時の正確性に限定 |
| F09 | 本番は固定threshold。拒否の追加もプラグインとの互換性を変えるため保留 |
| F14 | 下記の方式選択が必要。metadata fingerprintとstore形式は維持 |
| F16 | 本番はasync lighting無効。executorの終了と既存タスク排出の方式は別途決める |
| F20 | read-only配列共有は意図された設計。コピー/書き込みAPIへの変更は互換性方針の判断待ち |
| 性能改善案 | store openの退行を上記条件で測定・改善。他の速度向上率やRSS削減率は未測定 |

## F14: 内容hashの方式について

「サイズかmtimeが変わったときだけhashを再計算する」方式は、同じサイズ・mtimeへ戻した変更を検出できません。storeへCRC32CやxxHashを保存するだけでも解決しません。検出条件とコストの選択を明確にする必要があります。

1. **公開時に内容hashと不変revisionを作る方式（推奨案）**: マップのビルド/配布段階で全regionをhashし、revision付きの新ディレクトリ/manifestとして公開。サーバーはrevisionをstoreのキーに使う。各試合開始で全regionを読む必要はない。配布手順とstoreキーの仕様変更が必要。
2. **ロードごとに全regionをhashする方式**: 配布手順に依存せず同mtimeの変更も検出できる。そのWorldを開くたびにマップ全体を読むため、小さいVPSでのCPU・IOとcold page cacheへの影響を測る必要がある。
3. **metadata変更時だけhashする方式**: コストを抑えられるが、同mtime・同サイズの変更は検出できないという制約を受け入れる必要がある。

今回はレビューの「案を書き残す」指示に従い、方式を勝手に選んで保存形式や配布手順を変更していません。
