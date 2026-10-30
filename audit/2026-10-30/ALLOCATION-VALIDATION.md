# チャンク処理の実装前後比較 — 2026-10-30

## 結果と変更範囲

ブランチはルート・生成Serverとも `perf/chunk-allocation-2026-10-30`。開始時はルート `d127f6d`、Server `efd684c5e`（既存0367・0368を含む）でした。既存のVPSレビュー作業を保持した状態から分岐しています。APIは変更していません。

提案した4項目を独立したパッチとして実装しました。以下の比較はMacでの実装前後比較であり、本番ConoHaでの性能実測ではありません。

| patch | 内容 | Server commit | ルートcommit |
|---|---|---|---|
| 0369 | Compress chunk headers and payloads without an intermediate full copy | `f7b3b03d4` | `a139632` |
| 0370 | Start per-chunk tile and creature maps at minimal capacity | `0ab6fc306` | `43179b9` |
| 0371 | Take one owned section save snapshot and release it after packing | `9d9c14b26` | `4ea093e` |
| 0372 | Invalidate the Paper chunk map when any absent section becomes present | `c5ef865a1` | `0a6439c` |

## 実装

### 0369: 圧縮前の全量コピーを削除

AthenaChunkPacketCacheは、小さいヘッダだけを32byteのバッファにserializeします。ヘッダと既存ChunkMap.dataを順番にDeflaterへ渡し、間にflushを入れず、同じzlib streamとして圧縮します。payload全体をrawバッファへコピーする処理を削除しました。

thresholdの比較はヘッダとpayloadの合計長で行います。圧縮後のframe公開、map解放、Deflater.resetのfinally、同一cacheの同期を維持しています。圧縮出力用ByteBufもfinallyでreleaseするようにしました。

既存の1.7/1.8送信テストに加え、レベル1・6・9、長さ0/1/127/128/16383/16384/196864、zero/random、thresholdがraw長の前/同値/後という126条件を確認しました。圧縮対象では、変更前と同じ連続入力で生成した参照frameと圧縮byteまで一致しています。同一スレッドの再利用と同一cacheの並行初回読込も確認しています。

### 0370: 空チャンク内mapの初期容量を縮小

Chunk.entityCountとtileEntitiesを、同じfastutilの型のままexpected size=0で生成します。空状態の各内部配列が33要素から3要素になります。entry到着時は従来どおり拡張します。nullを使う遅延mapや、共有の可変mapへは変更していません。

TileEntityの追加・成長・削除・再利用と、creature tally・World censusを既定容量mapと比較する回帰テストを追加しました。空・疎・密の3条件を比較し、密な状態ではrehashの追加コストが出ることも確認しました（下表）。

### 0371: 保存用snapshotを1回で取得し、pack後に解放

ChunkSectionにpackage-privateのathenaSnapshotIdsを追加しました。mutableなplainはcloneし、uniformはidsSharedによるcopy-on-writeを前提に不変の共有配列をそのまま保持します。packed/mappedは新しく展開された配列をそのまま所有snapshotとして使用します。ChunkRegionLoaderは新しいsection用attachを呼ぶため、packed/mappedの展開直後にもう一度cloneしません。

DeferredSectionDataはpackのmonitor内で、packed配列の生成後にids参照をnullへ戻し、その後にvolatile blocksを公開します。以後のTag読込にはpacked配列を使います。既存のchar[]用attachは入力をコピーし、従来のsnapshot契約を維持します。

plain・uniform・packed・mappedの4ケースで、attach後に元配列とsectionを変更してもBlocks/Data/Addが変更前のsnapshotに一致することを確認しました。uniformではattachが共有配列と同じ参照を保持すること、attach後のsetTypeがcopy-on-writeを行ってsnapshotを変えないことも確認しています。pack後にids参照が解放され、繰り返し読込が可能なことも検証しています。既存の並行読込テストも通っています。

SGテンプレートは保存しないため、この項目の実運用上の主対象はhub等の通常保存ワールドです。今回の比較はsnapshot作成までで、NBT serialization・deflate・ディスク書込全体の時間は測っていません。

### 0372: 旧Paperキャッシュのbit判定

emptySectionBitsの判定を `== 1` から `!= 0` へ変更しました。修正前のテストでは、absent→present→absentを繰り返すケースでsection 0は成功し、section 1・7・15の3ケースが失敗しました。修正後は全4ケースで、通常のpacket抽出結果と全byte一致し、無変更時にcacheが再利用されることを確認しています。

本番設定の有効性を新たに確認したわけではありません。ローカルserver/paper.ymlではcache-chunk-maps=falseです。この修正は同設定が有効な場合の正確性改善であり、現在のSGの速度向上とは扱いません。任意のNMS配列直接変更を検出する修正でもありません。

## 計測条件

- Oracle GraalVM 25+37.1、macOS arm64。
- `-Xms144m -Xmx144m -XX:+UseParallelGC -XX:+UseCompactObjectHeaders`。ヒープとGCは小容量向け起動設定に合わせていますが、CPUやOS・JDK distributionは本番と異なります。MemoryMax=400MのLinux cgroupやsg1/sg2同居は再現していません。
- 変更前/後で同じruntime classpathを使用し、対象のclassディレクトリを先頭に置いて切り替えました。class SHA-256を保存しています。
- 各mode・variantで新規JVMを3つ使用。第2プロセス対だけvariant順を反転しています。
- ThreadMXBean.getThreadAllocatedBytesで測定スレッドのallocationを取得しました。時間には測定中に発生したGC等の待機も含まれます。
- 結果はvolatile sinkへ渡し、不要な処理として最適化で消されるのを防いでいます。

### 空チャンク生成

world=nullでChunkを1,200個生成して配列に保持する処理です。5巡warm-up後、各JVMで5巡、計15巡/variantを測定しました。map単体ではなくChunkのコンストラクタ全体の値です。

追加で、120チャンク（10個に1個）に8個のtile entry、12チャンク（100個に1個）に4種類のcreature tallyを持つ「疎」条件と、全1,200チャンクに64個のtile entryと4種類のtallyを持つ「密」条件を測定しました。map操作を分離するためTileEntityオブジェクトは1個を共有し、Entity/TileEntity本体の生成・登録・tickを再現するベンチではありません。両variantで同じ操作を行い、各条件15巡ずつ比較しました。

### 圧縮・保存snapshot

map_baysideの既存storeのkey順で最初の1,200チャンクをロードして保持しました。実際のSGの円形プレイ範囲を再現した選択ではありません。登録処理、store open、ロードは計測区間の外です。

圧縮はfull mask・sky lightありで事前抽出した同じpayloadを使用し、各巡で新しいcacheを生成してframeを圧縮します。1回のCRC32集計後に2巡warm-upし、各JVMで3巡、計9巡/variantを測定しました。cacheのテスト用コンストラクタによるchecksum計算も両variantで含みます。

保存snapshotは、非null sectionへDeferredSectionData.attachを呼んでNBTタグを作る処理です。両variantで同じreflection呼出しを使い、変更前はgetIdArrayで取得したchar[]、変更後はsectionを渡します。5巡warm-up後、各JVMで3巡、計9巡/variantを測定しました。pack・実ファイル保存は含みません。

## 測定結果

単位MiBは1,048,576byte。各表の時間とallocationは全巡をまとめた中央値です。

| 処理（1,200チャンク/巡） | 変更前 ms | 変更後 ms | 変更前 allocated bytes | 変更後 allocated bytes |
|---|---:|---:|---:|---:|
| 空チャンク生成 | 0.630 | 0.570 | 6,609,616 | 5,889,616 |
| チャンクcache圧縮 | 404.941 | 392.870 | 85,597,960 | 18,443,728 |
| 保存snapshot作成（レビュー対応後の再測定） | 19.487 | 17.943 | 86,953,848 | 39,749,640 |

| 処理 | 変更前時間の範囲 ms | 変更後時間の範囲 ms | 変更前/後 allocation中央値 MiB |
|---|---|---|---|
| 空チャンク生成 | 0.464〜5.059 | 0.520〜4.854 | 6.303 / 5.617 |
| チャンクcache圧縮 | 404.446〜415.078 | 382.070〜396.762 | 81.633 / 17.589 |
| 保存snapshot作成（レビュー対応後の再測定） | 18.971〜19.880 | 17.095〜18.521 | 82.926 / 37.908 |

圧縮allocationの中央値差は約64.04MiB、保存snapshotは約45.02MiB、空チャンク生成は720,000byteです。これらは累積割り当て量であり、そのままRSSや同時保持ヒープの削減量ではありません。

空チャンク生成ではGCを含む時間のばらつきが大きく、速度改善の主張は限定的です。圧縮・snapshotの時間はこの条件で短縮しましたが、試合全体のTPSやVPSのCPU使用率への改善率には換算しません。

### mapの成長コストの追加確認

| map内容（1,200チャンク/巡、中央値） | 変更前 ms | 変更後 ms | 変更前 allocated bytes | 変更後 allocated bytes |
|---|---:|---:|---:|---:|
| 疎: 10個に1個のチャンクに8 tile entry | 1.512 | 1.484 | 6,610,000 | 5,946,640 |
| 密: 全チャンクに64 tile entry | 3.477 | 4.403 | 9,508,816 | 10,036,816 |

疎な条件ではallocationが663,360byte減少しました。密な条件では逆に528,000byte増え、時間の中央値も増加しました。初期容量縮小は、要素数が多い場合のrehashを増やすトレードオフです。今回のSG向け変更として独立した0370へ残していますが、チェスト等が密集した通常ワールド全般での高速化とは主張しません。本番でtile entry分布を取っていないため、疎条件が本番全体を正確に再現するという主張もしません。

個別の全60測定と出力は [population.csv](allocation-bench/population.csv)、[population.log](allocation-bench/population.log) に保存しました。再現時はmeasure.pyへ `--modes maps-mixed maps-dense` を追加し、別のoutputを指定します。

変更前後の全圧縮frameを順に集計したCRC32は、全6プロセスで `db27b2b2` と一致しました。厳密なframeのbyte一致は回帰テストで確認しています。

初回の全66測定の個別値は [results.csv](allocation-bench/results.csv)、各プロセス出力は [results.log](allocation-bench/results.log) に保存しました。保存snapshot欄はレビュー対応後の18測定 [save-updated.csv](allocation-bench/save-updated.csv)・[ログ](allocation-bench/save-updated.log)へ更新しています。初回保存snapshotの46.597MiB/18.168msは旧結果としてresults.csvに残しています。

## 全テストとパッチ検証

```sh
./gradlew :pandaspigot-server:test :pandaspigot-api:test --rerun-tasks --offline --console=plain
```

| 対象 | tests | failures | errors | skipped |
|---|---:|---:|---:|---:|
| API | 988 | 0 | 0 | 3 |
| Server | 5,618 | 0 | 0 | 1 |
| 合計 | 6,606 | 0 | 0 | 4 |

今回追加したテストは12ケースです。skip条件は変更していません。

- AthenaChunkCompressionInputTest（2件）: splitInputsMatchTheOriginalStreamAtLevelsLengthsAndThresholds、concurrentFirstReadersShareTheSameCompleteFrame。
- AthenaSmallChunkMapsTest（2件）: tileEntriesSurviveGrowthRemovalAndReuse、creatureTalliesAndWorldCensusMatchDefaultCapacityMaps。
- AthenaSaveSnapshotTest（4件）: plainSnapshotSurvivesSubsequentWrites、uniformSnapshotSurvivesSubsequentWrites、packedSnapshotSurvivesSubsequentWrites、mappedSnapshotSurvivesSubsequentWrites。
- AthenaPaperChunkMapTransitionTest（4件）: absentPresentAndAbsentAgainRebuildTheEnabledPaperCache（section 0/1/7/15）。

0369〜0372を開始時Server `efd684c5e` の一時indexへ順次適用し、最終Server `c5ef865a1` のツリーと一致しました。旧パッチの変更はありません。

## 再現方法

[allocation-bench](allocation-bench/) にprobe、実行スクリプト、CSV、ログ、class hashを保存しました。

1. 各revisionでServerとtest classesをビルドする。
2. 変更前/後それぞれのChunk.class、ChunkSection.class、AthenaDeferredSectionData*.class、AthenaChunkPacketCache*.classを別ディレクトリへコピーする。
3. 同一のtest runtime classpathをファイルへ出力し、AllocationProbe.javaをコンパイルする。test fixtureを使用します。
4. measure.pyへJDK、classpathファイル、probe classes、両variant、既存storeを持つmap_bayside、出力先CSVの絶対パスを渡す。

```sh
python3 audit/2026-10-30/allocation-bench/measure.py \
  --jdk "$JDK25" --classpath "$RUNTIME_CP_FILE" --probe "$PROBE_CLASSES" \
  --baseline "$BASELINE_CLASSES" --fixed "$FIXED_CLASSES" \
  --map "$BAYSIDE_TEMPLATE" --output "$RESULTS_DIR/results.csv"
```

計測用のマップ・class・環境依存classpathはコミットしていません。paper-cache-before.logは、意図的に失敗させた変更前の再現テスト出力です。

## 配布物の検証

`./gradlew :paperclip:paperclipJar --offline --console=plain` は成功しました。ルート `0a6439c`・Server `c5ef865a1` を含むJARを、既存Mojang JARを使ってJava 25のpatch-onlyモードで展開し、出力SHA-256が埋め込みpatchedHashとServerビルド結果に一致しました。本番サーバーの起動は行っていません。

```text
paperclip SHA-256: 19f17c00f05a1b17716fe36ce990b592405505a11167653dad9443456a6aab59
patched SHA-256: 37a2381be23329f032539f63b926ad1577460c982dfe3ec94b3866414f9ee931
```

成果物はpaperclip/build/libs/paperclip-1.8.8-R0.1-SNAPSHOT.jarです。ルートの既存paperclip.jarは置き換えていません。[PR #29](https://github.com/HyogaFukuno/Athena/pull/29)へpush済みです。PR #28を土台にするマージ順序を維持し、マージ・本番反映は行っていません。

## PR #29レビュー対応

[修正依頼コメント](https://github.com/HyogaFukuno/Athena/pull/29#issuecomment-6514035794)に従い、0369〜0372の生成Serverコミットへ修正を取り込み、パッチを再生成しました。ルート履歴は書き換えず、上表の追加コミットで更新しています。

- 0371: uniformをcloneせず共有し、参照同一性とattach後のsetTypeによるcopy-on-writeをテスト。
- 0369/0370/全4テスト: 波括弧・空行・明示import・Javadocを既存スタイルへ統一し、fastutil移行のコメントを復元。
- paper-cache-before.log/results.logのホームディレクトリを`<home>`へ置換。追加ログも同じ置換を実施。
- 最終ソースでAPI/Server全テストを再実行し、6,606件、失敗0・エラー0・スキップ4。ケース数は維持し、uniformの既存ケースへ検証を追加。
- 更新した0369〜0372をefd684c5eへ順次適用し、Server c5ef865a1とツリー一致を確認。

### uniform共有だけの比較

条件・マップ範囲・warm-upは上記の保存snapshot測定と同じです。PR修正前のServer 47eeb14bfと、今回のServer c5ef865a1を比較しました。各variantで3新規JVM×3巡、計9巡ずつ測定しています。

| 指標（1,200チャンク/巡） | PR修正前 | uniform共有後 |
|---|---:|---:|
| allocated bytes中央値 | 48,860,520 | 39,749,640 |
| MiB換算 | 46.597 | 37.908 |
| 時間中央値 ms | 18.005 | 17.806 |
| 時間範囲 ms | 17.864〜18.540 | 17.411〜18.493 |

追加で9,110,880byte（約8.689MiB）の割り当てを削減しました。時間範囲は重なるため、uniform共有だけで一定割合高速化するとは主張しません。SGは保存しないテンプレート運用のため、主な対象は保存を行う通常ワールドです。

全18測定は [uniform-comparison.csv](allocation-bench/uniform-comparison.csv)・[ログ](allocation-bench/uniform-comparison.log)に、両variantのclass hashはreview-class-sha256.jsonに保存しました。比較時のbaselineはPR修正前、fixedはレビュー対応後です。save-updated.csvのbaselineは従来どおりefd684c5e、fixedはレビュー対応後です。
