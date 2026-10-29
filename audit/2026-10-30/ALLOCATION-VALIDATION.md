# チャンク処理の実装前後比較 — 2026-10-30

## 結果と変更範囲

ブランチはルート・生成Serverとも `perf/chunk-allocation-2026-10-30`。開始時はルート `d127f6d`、Server `efd684c5e`（既存0367・0368を含む）でした。既存のVPSレビュー作業を保持した状態から分岐しています。APIは変更していません。

提案した4項目を独立したパッチとして実装しました。以下の比較はMacでの実装前後比較であり、本番ConoHaでの性能実測ではありません。

| patch | 内容 | Server commit | ルートcommit |
|---|---|---|---|
| 0369 | Compress chunk headers and payloads without an intermediate full copy | `adb77a80e` | `901d1d6` |
| 0370 | Start per-chunk tile and creature maps at minimal capacity | `130f1908f` | `90e18bf` |
| 0371 | Take one owned section save snapshot and release it after packing | `be23b74a5` | `9d4e93b` |
| 0372 | Invalidate the Paper chunk map when any absent section becomes present | `47eeb14bf` | `191a8bf` |

## 実装

### 0369: 圧縮前の全量コピーを削除

AthenaChunkPacketCacheは、小さいヘッダだけを32byteのバッファにserializeします。ヘッダと既存ChunkMap.dataを順番にDeflaterへ渡し、間にflushを入れず、同じzlib streamとして圧縮します。payload全体をrawバッファへコピーする処理を削除しました。

thresholdの比較はヘッダとpayloadの合計長で行います。圧縮後のframe公開、map解放、Deflater.resetのfinally、同一cacheの同期を維持しています。圧縮出力用ByteBufもfinallyでreleaseするようにしました。

既存の1.7/1.8送信テストに加え、レベル1・6・9、長さ0/1/127/128/16383/16384/196864、zero/random、thresholdがraw長の前/同値/後という126条件を確認しました。圧縮対象では、変更前と同じ連続入力で生成した参照frameと圧縮byteまで一致しています。同一スレッドの再利用と同一cacheの並行初回読込も確認しています。

### 0370: 空チャンク内mapの初期容量を縮小

Chunk.entityCountとtileEntitiesを、同じfastutilの型のままexpected size=0で生成します。空状態の各内部配列が33要素から3要素になります。entry到着時は従来どおり拡張します。nullを使う遅延mapや、共有の可変mapへは変更していません。

TileEntityの追加・成長・削除・再利用と、creature tally・World censusを既定容量mapと比較する回帰テストを追加しました。空・疎・密の3条件を比較し、密な状態ではrehashの追加コストが出ることも確認しました（下表）。

### 0371: 保存用snapshotを1回で取得し、pack後に解放

ChunkSectionにpackage-privateのathenaSnapshotIdsを追加しました。plain/uniformはclone、packed/mappedは新しく展開された配列をそのまま所有snapshotとして使用します。ChunkRegionLoaderは新しいsection用attachを呼ぶため、packed/mappedの展開直後にもう一度cloneしません。

DeferredSectionDataはpackのmonitor内で、packed配列の生成後にids参照をnullへ戻し、その後にvolatile blocksを公開します。以後のTag読込にはpacked配列を使います。既存のchar[]用attachは入力をコピーし、従来のsnapshot契約を維持します。

plain・uniform・packed・mappedの4ケースで、attach後に元配列とsectionを変更してもBlocks/Data/Addが変更前のsnapshotに一致することを確認しました。pack後にids参照が解放され、繰り返し読込が可能なことも検証しています。既存の並行読込テストも通っています。

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
| 保存snapshot作成 | 19.586 | 18.168 | 86,953,848 | 48,860,520 |

| 処理 | 変更前時間の範囲 ms | 変更後時間の範囲 ms | 変更前/後 allocation中央値 MiB |
|---|---|---|---|
| 空チャンク生成 | 0.464〜5.059 | 0.520〜4.854 | 6.303 / 5.617 |
| チャンクcache圧縮 | 404.446〜415.078 | 382.070〜396.762 | 81.633 / 17.589 |
| 保存snapshot作成 | 19.255〜19.794 | 17.631〜18.556 | 82.926 / 46.597 |

圧縮allocationの中央値差は約64.04MiB、保存snapshotは約36.33MiB、空チャンク生成は720,000byteです。これらは累積割り当て量であり、そのままRSSや同時保持ヒープの削減量ではありません。

空チャンク生成ではGCを含む時間のばらつきが大きく、速度改善の主張は限定的です。圧縮・snapshotの時間はこの条件で短縮しましたが、試合全体のTPSやVPSのCPU使用率への改善率には換算しません。

### mapの成長コストの追加確認

| map内容（1,200チャンク/巡、中央値） | 変更前 ms | 変更後 ms | 変更前 allocated bytes | 変更後 allocated bytes |
|---|---:|---:|---:|---:|
| 疎: 10個に1個のチャンクに8 tile entry | 1.512 | 1.484 | 6,610,000 | 5,946,640 |
| 密: 全チャンクに64 tile entry | 3.477 | 4.403 | 9,508,816 | 10,036,816 |

疎な条件ではallocationが663,360byte減少しました。密な条件では逆に528,000byte増え、時間の中央値も増加しました。初期容量縮小は、要素数が多い場合のrehashを増やすトレードオフです。今回のSG向け変更として独立した0370へ残していますが、チェスト等が密集した通常ワールド全般での高速化とは主張しません。本番でtile entry分布を取っていないため、疎条件が本番全体を正確に再現するという主張もしません。

個別の全60測定と出力は [population.csv](allocation-bench/population.csv)、[population.log](allocation-bench/population.log) に保存しました。再現時はmeasure.pyへ `--modes maps-mixed maps-dense` を追加し、別のoutputを指定します。

変更前後の全圧縮frameを順に集計したCRC32は、全6プロセスで `db27b2b2` と一致しました。厳密なframeのbyte一致は回帰テストで確認しています。

全66測定の個別値は [results.csv](allocation-bench/results.csv)、各プロセス出力は [results.log](allocation-bench/results.log) に保存しました。

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

0369〜0372を開始時Server `efd684c5e` の一時indexへ順次適用し、最終Server `47eeb14bf` のツリーと一致しました。旧パッチの変更はありません。

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

`./gradlew :paperclip:paperclipJar --offline --console=plain` は成功しました。ルート `191a8bf`・Server `47eeb14bf` を含むJARを、既存Mojang JARを使ってJava 25のpatch-onlyモードで展開し、出力SHA-256が埋め込みpatchedHashとServerビルド結果に一致しました。本番サーバーの起動は行っていません。

```text
paperclip SHA-256: 06b59399d27be2944e953be3babdaa5046f52e1a81c446e92569c3f88c94dfa2
patched SHA-256: 38cd4ab53093938b85e7ec2d1d93d298f81b4aead1f99075210656b98c124251
```

成果物はpaperclip/build/libs/paperclip-1.8.8-R0.1-SNAPSHOT.jarです。ルートの既存paperclip.jarは置き換えていません。今回の作業はローカルのコミットまでで、push・PR作成・マージ・本番反映は行っていません。
