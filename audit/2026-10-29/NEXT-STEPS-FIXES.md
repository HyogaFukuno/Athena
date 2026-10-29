# NEXT-STEPSレビューに基づく修正記録 — 2026-10-29

## 対応範囲

master `341552d` を起点に、ルートと生成Serverで `fix/next-steps-2026-10-29` ブランチを作成しました。生成Serverのmasterは、PR #26で採用されたソースの `fd21ca413` までfast-forwardしてから分岐しています。APIは変更していません。

レビューの指定に従ってN01 → N04 → N02の順に実装し、各項目を独立したServerコミットとパッチにしました。N03は見送りです。[NEXT-STEPS.md](NEXT-STEPS.md)にも影響の補足と見送り理由を反映しました。

| 項目 | 結果 | Server commit | patch | ルートcommit |
|---|---|---|---|---|
| N01 | region初期化失敗時にチャネルを閉じる | `ec866cf7c` | 0364: Close template region channels when initialization fails | `ae28bbb` |
| N04 | RegistryIDの幾何成長を復元 | `cb611235f` | 0365: Restore geometric registry growth without changing lookups | `8b79696` |
| N02 | mapped光データをパケットへ直接コピー | `5da903ab7` | 0366: Copy mapped light directly into chunk packet output | `cd5faf6` |

調査文書の初回追加コミットはルート `b76e03b` です。本記録と計測用ファイルは別の文書コミットへまとめます。マージ・本番反映は行っていません。

## N01: region初期化失敗時の後始末

`open(File)` から、package-privateの `open(FileChannel)` へチャネルを渡します。コンストラクタが成功するまではこのヘルパーが所有し、IOException・RuntimeException・Errorで失敗したときはチャネルを閉じて元の例外を再throwします。closeも失敗した場合はsuppressedへ追加し、元の失敗を維持します。

正常時はregionへ所有権を渡し、regionのcloseまでチャネルを保持します。欠落ファイルのnull、read/closeのlock、close待機の挙動は維持しています。

失敗したregionはloaderにキャッシュされないので、同じregion内のチャンクの存在判定でもopenが繰り返されます。最大1,024チャンクを持つregionのpreloadでは、GC回収までのFD増加が繰り返され得ます。今回の修正はその失敗ごとに明示的に閉じます。

補助の故障注入プローブでは、100回の初期読込失敗に対し、以前はFDが100増加し、修正後は `fdBefore=6, fdAfter=6, fdDelta=0` でした。ディレクトリをFile.isFile判定だけ通すmacOS向けプローブであり、正常なregionで本番のIO障害やFD枯渇を再現したという結果ではありません。JUnitはFD数に依存せず、スタブチャネルのcloseと例外を検証します。

**残存課題:** chunkExistsOpenはregionのIOExceptionをfalseへ変換し、呼出元では「保存されていない」扱いとなって生成経路へ進みます。レビューの指示に従い、この既存のエラー扱いは変更していません。

## N04: RegistryIDの容量拡張

Athenaの配列化で、元のArrayListが持っていた幾何成長が失われていました。必要容量と従来容量の1.5倍の大きい方へ拡張し、計算はlongで行って `Integer.MAX_VALUE - 8` を上限にします。必要容量が上限を超える場合は、overflowや巨大な負配列長になる前にOutOfMemoryErrorを投げます。

getByValueの実装は変更していません。余剰スロットはnullなので、範囲外・未登録IDの検索とnullを除外するiteratorの結果を維持します。負IDで配列アクセスが失敗する前に逆引きmapが更新される既存挙動も、今回変更していません。

### 実bootstrapの比較

Oracle GraalVM 25+37.1/macOS arm64、各測定を新規JVMで実行。`-Xms512m -Xmx1g`、同じruntime classpathを使い、変更前・後のクラスディレクトリを先頭に置いて比較しました。変更前はServer `fd21ca413`、変更後は `5da903ab7` のビルドクラスです。第2巡だけ実行順を逆にしています。

ThreadMXBeanを初期化してから、`DispenserRegistry.register()` の実行時間と呼出スレッドのallocated bytesを測定しました。bootstrapにはwarm-upを入れていません。class loadingやブロック・アイテム・統計等の初期化を含み、RegistryIDだけの時間ではありません。バックグラウンドスレッドを含むプロセス全体のallocationでもありません。

| 新規JVM | 変更前 ms | 変更後 ms | 変更前 allocated bytes | 変更後 allocated bytes |
|---|---:|---:|---:|---:|
| 1 | 742.744 | 547.279 | 66,653,896 | 62,596,928 |
| 2 | 537.583 | 513.410 | 68,291,656 | 62,397,448 |
| 3 | 519.036 | 515.492 | 67,471,312 | 62,120,840 |
| 中央値 | 537.583 | 515.492 | 67,471,312 | 62,397,448 |

割り当て量の中央値は約64.346MiBから約59.507MiBへ減りました。時間にはばらつきがあり、実サーバーの起動時間が一定割合で短縮するとまでは結論しません。登録されたblock stateは両方とも1,394件です。

別の容量確認では、BLOCK_STATE_IDSの最高IDは両方3,163、配列長は3,164から4,711へ増加しました。余剰の1,547参照分だけ保持メモリとiteratorの走査範囲は増えます。圧縮参照4byteなら参照領域の差は6,188byteです。iterator単体の性能やRSSは測定していません。

## N02: 光データの直接コピー

package-privateの `NibbleArray.copyTo(byte[], int)` を追加しました。heap配列はSystem.arraycopy、mappedはMemorySegment.copyで2,048byteを指定位置へ書き、次のoffsetを返します。PacketPlayOutMapChunkのblock light・sky lightの2ループだけを切り替えました。

公開getData、copy-on-write、保存・snapshotの経路は変更していません。uniform配列は従来もコピー用の中間配列を作らないため、主な削減対象はKIND_LIGHTの非一様なmapped配列です。キャッシュ再利用時の送信全般が速くなる変更ではなく、preload warmやキャッシュ作成・再構築時の抽出が対象です。

### bayside全チャンク抽出の比較

Oracle GraalVM 25+37.1/macOS arm64、同じJVMメモリ設定・classpath・variantを使用。`map_bayside` の既存storeから全4,114チャンクをロードし、保持したChunkに対してfull mask・sky lightありで `getExtractedData()` を実行しました。マップファイルは変更していません。

- storeのopen、チャンクロード、登録処理は測定区間の外です。
- 初回のCRC32集計後、全チャンク抽出を5巡warm-upし、その後3巡を測定しました。
- 各variantを3つの新規JVMで実行し、計9巡ずつ測定しました。第2プロセス対ではvariant順を逆にしました。
- ThreadMXBeanで抽出スレッドのallocated bytesを取得しました。各ChunkMapをvolatile sinkへ渡し、抽出結果が不要として消されないようにしています。
- mapped光配列は9,853個、そのうちfull packetに含まれる非空sectionの光配列は9,234個でした。
- 全パケットのmaskとdataを順に集計したCRC32は、全プロセス・両variantで `73c19ad0` でした。厳密な全byte一致は後述の30ケースの回帰テストで確認しています。

| 新規JVM | 変更前 1/2/3巡 ms | 変更後 1/2/3巡 ms |
|---|---|---|
| 1 | 53.071 / 60.241 / 53.642 | 51.431 / 55.421 / 50.635 |
| 2 | 52.315 / 53.024 / 54.449 | 52.072 / 52.541 / 56.840 |
| 3 | 52.911 / 52.195 / 58.920 | 49.339 / 50.455 / 49.291 |

| 指標（4,114チャンク1巡あたり） | 変更前 | 変更後 |
|---|---:|---:|
| 時間の中央値 ms | 53.071 | 51.431 |
| allocated bytesの範囲 | 237,669,568〜237,669,624 | 217,681,728 |
| allocated bytesの中央値 | 237,669,568 | 217,681,728 |
| 中央値のMiB換算 | 226.659 | 207.597 |

この条件で1巡の割り当て量が約19.062MiB減りました。これは測定区間全体の差であり、中間配列のデータ部分だけを数えた値ではありません。時間の中央値は短縮しましたが範囲は重なるため、一般的な速度向上率として扱いません。実際の試合はpreload範囲やキャッシュ再構築回数が異なり、1試合の削減量をそのまま19MiBとすることもできません。

## テストと配布物

```sh
./gradlew :pandaspigot-server:test :pandaspigot-api:test --rerun-tasks --offline --console=plain
./gradlew :paperclip:paperclipJar --offline --console=plain
```

両方成功しました。

| 対象 | tests | failures | errors | skipped |
|---|---:|---:|---:|---:|
| API | 988 | 0 | 0 | 3 |
| Server | 5,603 | 0 | 0 | 1 |
| 合計 | 6,591 | 0 | 0 | 4 |

今回の追加は40ケースです。既存のskip条件は変更していません。

| クラス | 追加数 | テスト名 |
|---|---:|---|
| AthenaTemplateOpenTest | 7 | sizeFailureClosesTheChannelAndPreservesTheException; readFailureClosesTheChannelAndPreservesTheException; closeFailureIsSuppressedOnTheOriginalFailure; runtimeReadFailureAlsoClosesTheChannel; readErrorIsRethrownAfterClosingTheChannel; successfulOpenTransfersOwnershipUntilRegionClose; aMissingRegionStillReturnsNull |
| AthenaRegistryGrowthTest | 3 | sequentialIdsKeepLookupAndIterationIdentical; sparseIdsAndOverwritesKeepIdentityAndIterationOrder; negativeIdsAndNullEntriesRetainTheirExistingBehavior |
| AthenaPacketLightCopyTest | 30 | packetBytesMatchPreviousExtractionForEveryLightStorage（heap/zero/full/mapped/mappedWritten × sky有無 × mask全/一部/空） |

N04のテストは変更前の配列実装をテスト内へ保存し、同じ操作列の例外型、identity逆引き、getByValue、iteratorの順序とnull除外を比較します。N02は各ケースでfull packetとpartial updateを比較し、空section、非ゼロoffsetへのcopy、周辺byteの不変性、mapped状態の維持も確認しています。

0364〜0366をServer `fd21ca413` の一時indexへ適用し、結果が生成Server HEAD `5da903ab7` のツリーと一致しました。既存のパッチは変更していません。

配布物は `paperclip/build/libs/paperclip-1.8.8-R0.1-SNAPSHOT.jar`。生成時のルートcommitは `cd5faf6`、Server commitは `5da903ab7` です。既存Mojang JARを一時cacheへコピーし、Java 25でpatch-only実行しました。展開後のSHA-256は埋め込みpatchedHashとServer JARの両方へ一致しました。本番サーバーは起動していません。

```text
paperclip SHA-256: 118dbc8098043fe1c61cf527af2e5b864e7188410fc15557dda09d2f3bdf3d70
patched SHA-256: 4683615679d2badfaf37080fbbbd99219f7e6669c65128b33407a7a22adf607f
```

## 再現用ファイル

[next-steps-bench](next-steps-bench/) に以下を保存しました。

- NextStepsProbe.java: bootstrapと実マップ抽出の計測。
- measure.py: variant順を入れ替えながら各variantを3新規JVMで実行。
- bootstrap.csv / packet.csv、同名.log: 全測定値と出力。
- RegistryCapacityProbe.java / registry-capacity.log: bootstrap後の容量確認。
- RegionOpenFailureProbe.java / region-open-fixed.log: macOSでの故障注入によるFD補助確認。

例（変数は各環境の絶対パスへ設定）:

```sh
"$JDK25/bin/javac" -cp "$(cat "$RUNTIME_CP_FILE")" -d "$PROBE_CLASSES" audit/2026-10-29/next-steps-bench/NextStepsProbe.java
python3 audit/2026-10-29/next-steps-bench/measure.py \
  --jdk "$JDK25" --classpath "$RUNTIME_CP_FILE" --probe "$PROBE_CLASSES" \
  --baseline "$BASELINE_CLASSES" --fixed "$FIXED_CLASSES" \
  --map "$BAYSIDE_TEMPLATE" --mode packet --output "$RESULTS_DIR/packet.csv"
```

bootstrapは `--mode bootstrap` と別のoutputを指定します。baseline/fixedは各revisionでビルドしたRegistryID、NibbleArray、PacketPlayOutMapChunkとそのinner classを含むディレクトリです。runtime classpathは同じServerのtest runtime（テストfixtureを含む）を使います。出力先親ディレクトリは事前に作成してください。計測用classpathやマップ、ビルド済みclass自体は環境依存なのでコミットしていません。

## 見送りと測定の限界

- **N03:** 省けるのは1試合・1regionのopen 1回、ヘッダ4KiBのread 1回、少数のFDです。store破損時のfallbackとcloseのlockに分岐を増やす利点が小さいため、指示どおり実装・IO比較を見送りました。
- N01でregionのIOExceptionを「未保存」と扱う既存動作は維持しました。
- 保存・snapshotのコピー、F14の内容hash、非同期装飾・lighting・NMS配列の公開規約は今回の対象外です。
- macOSでの手元比較です。Linux/ConoHa、1GBのcgroup、cold page cache、実クライアント接続、一試合全体のTPS・RSS・GC pause・iterator単独性能は測定していません。これらの改善は主張しません。
- SurvivalGamesの全ソース監査は今回実施していません。
