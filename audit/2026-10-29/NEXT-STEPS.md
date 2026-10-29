# Athenaの追加調査と修正予定 — 2026-10-29

## 対象と結論

PR #26のマージ報告後、次に着手できる項目として、前回監査の保留事項と関連する現行コードを再確認しました。参照したローカルのルートcommitは `24ece42`、生成Server commitは `fd21ca413` です。リモートのマージcommit自体の検証ではありません。

今回の対象は下記4項目の確認です。新たに全ソースを監査した結果ではなく、SurvivalGamesの全ソース監査もまだ実施していません。正確性の問題1件と、性能改善候補3件を分けて扱います。

| 順序 | 項目 | 分類 | 確認状況 |
|---|---|---|---|
| 1 | region初期化失敗時のFileChannelの後始末 | 異常系の不具合 | コード確認とエラー注入でFD増加を確認 |
| 2 | mapped光データの二重コピー | allocation削減候補 | コピー経路を確認。速度・GCへの効果は未測定 |
| 3 | slim storeが利用可能な場合のregionアクセス省略 | IO・FD削減候補 | chunkExistsの経路を確認。削減量は未測定 |
| 4 | RegistryIDの配列拡張方式 | 起動時の改善候補 | 拡張ごとの配列コピーを確認。実起動への効果は未測定 |

この文書は調査と修正予定の記録です。本体の修正はまだ行っていません。

## N01 — region初期化失敗時にチャネルを閉じない

### 調査結果

対象: [AthenaTemplateRegion.java:36](../../PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaTemplateRegion.java#L36)、同ファイルの `open()`（50〜54行）。

`open()` は `FileChannel.open()` の結果を直接コンストラクタへ渡します。コンストラクタの `channel.size()` やヘッダ読込が例外で終了すると、生成したオブジェクトを呼出元は受け取れず、チャネルを明示的に閉じる処理もありません。

正常に開いたregionにはclose処理があります。問題になるのはチャネル取得後、regionの初期化完了前に失敗する経路です。GCによる後始末を待つ間にFDが増える可能性があります。恒久的に回収されないとまでは断定していません。

### 再現方法と結果

GraalVM 25/macOSで、既存のServerクラスを呼ぶ一時プローブを実行しました。実際のマップは変更していません。

1. 一時ディレクトリを作成する。
2. そのパスの `File` を継承し、`isFile()` のみtrueを返すようにして、通常は通らないディレクトリのopen経路へ進める。
3. macOSでディレクトリのチャネル読込がIOExceptionになることを利用し、初期読込の失敗を注入する。
4. 5回の準備呼出し後、100回 `AthenaTemplateRegion.open()` を呼び、IOExceptionを捕捉する。
5. `UnixOperatingSystemMXBean.getOpenFileDescriptorCount()` で前後を比較する。

```text
injectedReadFailures=100 fdBefore=11 fdAfter=111 fdDelta=100
```

これは例外経路の後始末を検証するための故障注入です。通常のregionファイルで実際にIO障害が発生した事例や、本番のFD枯渇を再現したものではありません。また、短いファイルのEOFは現行実装が空ヘッダとして扱うため、それだけで同じ失敗になるわけではありません。

### 修正予定

- `open()` でチャネルをローカル変数に保持し、初期化成功時だけregionへ所有権を移す。
- 初期化失敗時はチャネルを必ず閉じ、元の例外を維持する。closeも失敗した場合は元の例外へsuppressedとして付ける。
- IOExceptionに加えてRuntimeException・Errorで初期化が終了する場合もリソースを閉じる。Errorは後始末後に再throwし、握り潰さない。
- 正常なregionの読込・close待機・欠落ファイルの扱いを維持する。

### 合格条件

- 読込またはsize取得で失敗するチャネルを使う回帰テストで、closeが呼ばれることを確認する。必要ならチャネルを受け取る内部ヘルパーを設ける。
- 初期化失敗とclose失敗が重なっても、元の例外とsuppressedを保持する。
- 正常時はopen直後に閉じず、regionのcloseで閉じる。
- FD実測は補助検証とし、GCやOSに依存するFD数だけをJUnitの合否条件にしない。

## N02 — mapped光データをパケットへ二重コピーしている

### 調査結果

対象: [NibbleArray.java:145](../../PandaSpigot-Server/src/main/java/net/minecraft/server/NibbleArray.java#L145)、[PacketPlayOutMapChunk.java:144](../../PandaSpigot-Server/src/main/java/net/minecraft/server/PacketPlayOutMapChunk.java#L144)。

mapped状態の `NibbleArray.getData()` は、mapped領域を新しい2,048byte配列へコピーします。パケット抽出処理は、その配列をさらにパケットの出力配列へ `System.arraycopy()` します。

この経路では光配列ごとに中間配列1個とコピー1回を省ける可能性があります。16sectionのblock light・sky lightがすべてmappedで送信対象なら、中間配列のデータ部分は合計64KiBです。これは条件付きの理論値であり、実マップの割り当て量ではありません。uniformや通常のheap配列では同じ中間配列は作られません。

SGのpreload時やチャンク送信用キャッシュの構築・再構築に関係します。キャッシュが再利用される送信のたびに、必ずこの処理が走るという意味ではありません。

### 修正予定

- NibbleArrayへ、指定したbyte配列のoffsetへ2,048byteを直接コピーする内部メソッドを追加する。
- heap配列はarraycopy、mapped領域はMemorySegmentの一括コピーを使う。
- まずパケット抽出経路を置き換える。既存 `getData()` の公開挙動やcopy-on-write規約は変更しない。
- 保存・snapshot経路への展開は、所有権と不変性を確認して別途判断する。

### 合格条件

- heap・共有uniform・mapped・初回書込後の各状態で、旧実装とパケットの全byteが一致する。
- sky lightの有無、section mask、空sectionの組合せで既存の出力を維持する。
- 同じマップと抽出条件でallocationと処理時間を比較する。初期化・warm-upを測定対象と区別し、速度向上率を未測定のまま主張しない。

## N03 — slim storeがあっても存在判定でregionを開く

### 調査結果

対象: [AthenaTemplateChunkLoader.java:89](../../PandaSpigot-Server/src/main/java/net/minecraft/server/AthenaTemplateChunkLoader.java#L89)。

`chunkExistsOpen()` はregionを取得してから `region.has()` を呼びます。該当regionをそのloaderで初めて参照すると、ファイルを開いてヘッダを読みます。一方、チャンク本体は有効なslim storeだけで復元できる場合があります。

regionはloader内でキャッシュされるため、chunkExistsの呼出しごとにファイルを開き直す問題ではありません。試合ごとに生成するWorldのloaderで、不要な初回open・ヘッダread・FD保持を省ける可能性があります。

### 修正予定

- storeが有効で、対象座標のslimデータが利用可能だと判定できる場合は、regionを開かず存在すると返す経路を検討する。
- slimを持たないチャンク、store無効化後、監査モードなどでは従来のregion判定を使う。
- 「storeにslimがない」ことから「元regionにもチャンクがない」とは判断しない。storeに全チャンクのslimがあるとは限らない。
- slimが破損していた場合のロード時の無効化・元region再読込と、World破棄時のlock保護を維持する。

### 合格条件

- 有効slimがある場合、存在判定でregionを開かない。
- slimがないチャンクと無効storeでは元regionを確認し、読み込めるチャンクを欠落扱いしない。
- 存在判定後のstore無効化・破損・closeでも、既存のfallbackと破棄処理が成立する。
- 同一のpreload範囲で、ファイルopen/read回数、FD数、preload所要時間を変更前後で比較する。

## N04 — RegistryIDの拡張が必要サイズちょうどになっている

### 調査結果

対象: [RegistryID.java:22](../../PandaSpigot-Server/src/main/java/net/minecraft/server/RegistryID.java#L22)。

IDが現在の配列容量を超えるたびに `Arrays.copyOf(objects, i + 1)` を実行します。例えば連番でN件登録すると、コピーする要素数の合計はO(N²)になります。

実際のblock state登録はIDの順序や重複の影響を受けるため、実起動が必ずこの最悪条件に一致するとは限りません。主な対象は起動時のCPU・allocationで、定常tickの高速化を主張する項目ではありません。

### 修正予定

- 必要容量と幾何成長した容量の大きい方へ拡張する。容量計算のoverflowを考慮する。
- `getByValue()` は範囲外や未登録の位置で引き続きnullを返す。
- identityによる逆引き、疎なID、上書き、iteratorの順序とnull除外を維持する。
- 余剰容量による保持メモリとiteratorの走査量も比較して採否を決める。

### 合格条件

- 連番・疎なID・上書きで、検索とiterationの結果が一致する。
- 合成した連番登録でallocation削減を確認する。
- 実サーバーのbootstrapで時間とallocationを比較し、小ヒープでの余剰容量も確認する。

## 実装の進め方

1. N01を独立した修正として実装・回帰テストする。
2. N02を独立した変更として実装し、byte一致とallocationを確認する。
3. N03をfallbackの回帰テストとIO測定付きで進める。
4. N04は起動時の比較結果を踏まえて採用する。

実装時は新しい作業ブランチを作成し、生成Serverで話題別にコミットしてから `./panda rb` でパッチを生成し、ルートにもコミットします。既存0355〜0363は書き換えず、追加番号は作業開始時の最新パッチを確認して決めます。

共通の確認として、パッチ再適用と生成ツリーの一致、Java 25でのAPI・Server全テスト、配布JARの生成・patch-only展開を行います。性能測定は同条件で複数回行い、機能の正確性とは分けて記録します。

## 今回の修正予定に含めない事項

- F14の内容hash・revision方式は、マップのビルド・配布方式と合わせて判断する。N03ではfingerprintの保証範囲を広げない。
- 非同期装飾の同値性・tick予算、async lightingの終了、NMS配列の公開規約などの設計課題は、前回の [FIXES.md](FIXES.md) の保留事項を継続する。
- SurvivalGamesの全ソース監査は別の調査範囲。今回の4項目が存在するため、この文書ではAthenaの次の対応を整理した。
- 本番のFD枯渇、TPS改善、RSS削減率などは今回測定しておらず、実績として扱わない。
