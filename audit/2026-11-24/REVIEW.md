# Athena と SurvivalGames の精査 — 2026-11-24

## 対象と方法

- **Athena**：`origin/master` `e21c849`。コードは本番の sg2 の server 0456 と同じ。違うのは `audit/` の下だけ。
- **SurvivalGames**：`v2` `3257ec1`（#54 まで）。本番の sg2 と同じ jar（`0e4c23a8`）で、11-24 09:11 に入れた。
- **AthenaHub**：`main` `69b5ca7`（#7 まで）。VPS-A の Hub と同じ jar（`7466ceb5`）。
- **読んだ所**：11-23 の精査（`audit/2026-11-23/REVIEW.md`）が「読んでいない所」に挙げたものを中心にした。
  - SG：`LobbyState`、`Arenas`、`DeathmatchVenue`、`CombatTracker`、`Gear`、`BotRoster`、`SgBot` の後半（戦い方・逃げ方・チェスト・歩き回り）、`ChatColors`、`SurvivalGamesCommand`、`MapEditor`。
  - SG（見直し）：#54 で変えた `QueuedStatsRepository`、`StatsService`、`PlayerRanks`、`Disguises`、`Scoreboards`。
  - 部屋ごとのリスナーが、退出（`PlayerQuitEvent`）とワールドの破棄（`WorldUnloadEvent`）をどう受けるか（`CountdownListener`、`GameEventListener`、`CombatTracker`、`SurvivalGamesCommand`）。
  - Athena：退出とボットのログインが、どのスレッドでどのイベントを通るか（パッチ 0389・0399）。
  - Velocity：`velocity.toml`（秘密の行を除く）、プラグインの一覧、ユニット、ログ。
  - 本番：両方の VPS の自動更新（`unattended-upgrades`、`needrestart`）の記録、サービスの再起動の記録、メモリ、ディスク、エラー。
- **確かめ方**：
  - 自動更新のあとの再起動（M1）と Velocity の止まり方（L1）は、本番のログで確かめた。
  - L2 と L3 は、コードを読んだ結果で、再現はしていない。

## まとめ

- **高**はない。
- **中**は運用に 1 件（M1）。自動更新の `needrestart` が、sg2・Hub・Velocity・MariaDB を予告なしで再起動している。sg2 は、この 2 週間で 7 回あった。
- **低**は 3 件（L1〜L3）。
- Athena のコードに、新しい問題は見つからなかった。#54 の変更にも、新しい問題は見つからなかった。

## 中（運用）

### M1 自動更新のあと、`needrestart` が sg2・Hub・Velocity・MariaDB を予告なしで再起動する（**直した**：ArcanaServers #9、案 1）

- **何が起きるか**：
  - 両方の VPS で `unattended-upgrades` が毎朝 6〜7 時にセキュリティ更新を入れる。そのあとに `needrestart` が、更新したライブラリを使っているサービスを自動で再起動する（Ubuntu 24.04 の既定）。
  - Java（`openjdk-25-jre-headless`）など、使っているライブラリが更新されるたびに、その場で再起動される。say での予告も、30 秒の待ちもない。
- **本番の記録**（`/var/log/unattended-upgrades/unattended-upgrades-dpkg.log*` の「Restarting services」と、`journalctl`）：

  | 日 | VPS-B（sg2） | VPS-A |
  |---|---|---|
  | 10-22 | | `hub`、`velocity`、`mariadb` |
  | 10-25 | | `hub`、`velocity`、`mariadb` |
  | 11-04 | | `mariadb` |
  | 11-10 | | `hub`、`velocity` |
  | 11-11 | `sg@sg2`（2 回、06:49・06:50） | `hub` |
  | 11-14 | `sg@sg2` | `hub`、`velocity`、`mariadb` |
  | 11-17 | `sg@sg2` | |
  | 11-18 | `sg@sg2` | `hub`、`velocity` |
  | 11-20 | `sg@sg2`（2 回、06:17・06:18） | `hub`、`velocity` |
  | 11-22 | | `hub` |

  - どれも 6〜7 時。sg2 は、この 2 週間に 5 日で 7 回、再起動された。
  - Java（`openjdk-25-jre-headless`）は、11-11・11-18・11-20 に更新された。

- **影響**：
  - sg2 の再起動では、試合の途中の人が全員切れ、試合は止まる。戦績は止めるときに保存する（`saveAll`）ので、捨てはしない。
  - Velocity の再起動では、ネットワークの全員が切れる。L1 のとおり、止まるまでに毎回 30 秒かかる。
  - MariaDB の再起動は、11-23 の M1 の場面（試合の途中で DB が止まる）と同じ。#54 からは、書けなかった戦績を持って書き直す。
  - 11-17 から人のログインがないので、まだ実害はない。
  - 「sg2 は予告してから止める」という決めごと（say のあと 30 秒待つ）からも外れている。
- **直し方の案**（どれにするかはユーザーが決める）：
  1. `needrestart` に、ゲームのサービスを自動で再起動しないよう書く（`/etc/needrestart/conf.d/` で `$nrconf{override_rc}` に `qr(^sg@)`・`qr(^hub)`・`qr(^velocity)`・`qr(^mariadb)` を 0 で足す）。再起動が要ることだけは一覧に出るので、人のいない時間に、予告してから手で再起動する。
     - 気を付けること：再起動しないと、JVM は入れ替わる前の JDK のまま動き続ける。あとから読み込むネイティブのライブラリがあると、新しいファイルと混ざって落ちることがある。だから、遅くともその日のうちに再起動する。
     - **これにした**（2026-11-24）。両方の箱の `/etc/needrestart/conf.d/arcana-games.conf` に置いた。
       - `needrestart -r l -b -vv` が設定をエラーなく読んだ。
       - 箱の上の設定全体を needrestart と同じやり方で読むと、`sg@sg2`・`hub`・`velocity`・`mariadb` だけが「defer」、`ssh` などは「restart」のままだった。
       - 更新の場面ではまだ通っていない。
  2. 1 のうえで、「再起動が要るなら、say で予告して 30 秒待ってから再起動する」タイマーを VPS に置く。自動のまま、予告も入る。
  3. sg2 のユニットの `ExecStop` で、say のあと 30 秒待ってから stop を送るようにする。どの止め方でも予告が入るが、手で止めるときも 30 秒待つことになる。

## 低

### L1 Velocity が `end` で止まらず、止めるたびに 30 秒待ってから止められる（運用）

- `velocity.service` は `ExecStop=tmux send-keys -t velocity end Enter` で止める。
- 10-20 からの 8 回の停止は、8 回とも「Stopping timed out. Terminating.」で終わった（`journalctl -u velocity`）。
- Velocity のログには `end` を受けた跡がない。30 秒たって tmux が止められ、コンソールの読み込みが「Input/output error」で落ちてから、「Shutting down the proxy...」と出る。
- tmux のセッションは velocity のユーザーから見えていて、画面も読める（確かめた）。なぜ `end` が効かないのかは分かっていない。Velocity を止めないと試せないので、試していない。
- **影響**：止めるのが毎回 30 秒遅れる。止まり方は、シャットダウンの処理が動くので同じ。
- **直し方の案**：人のいない時間に、`systemctl stop velocity` で `end` が届くかを見る。届かなければ、`ExecStop` をやめて `KillSignal=SIGTERM`（Velocity のシャットダウンの処理が動く）にする。

### L2 部屋ごとのリスナーが、ほかの部屋のスレッドから、自分の部屋の表を触る（SG）

- リスナーの多くは部屋ごとに登録していて、どの部屋の人のイベントも、全部の部屋のリスナーが受ける（`SurvivalGames.java:224-231`）。
  - ほとんどのリスナーは、まず `room.has(player)` や `arenas.isMatchWorld(world)` で自分の部屋かを確かめる。
  - 次の 4 つは確かめずに、自分の部屋の表（スレッドで守っていない fastutil の表）を触る。
    - `CountdownListener.onQuit`・`onWorldUnload`（`anchors`）
    - `GameEventListener.onQuit`（`Eliminations.forget`、`lastSpectatorTeleport`）
    - `CombatTracker.onQuit`（`tracks`、`fleeing`、`effect`）
    - `SurvivalGamesCommand.onQuit`（`MapEditor.sessions`）
- 退出は、抜ける人のワールドのスレッドで来る（Athena 0389：「quits run there」）。ワールドの破棄はサーバースレッドで来る。そのため、試合中の部屋の表に、その部屋のスレッドの外から触る。
- **ほとんどは読むだけ**：
  - 抜ける人は、ほかの部屋の表に載っていない。部屋を移るには、入り直すしかないからだ（`RoomJoins`）。
  - そのため、`remove` は探すだけで書き換えない。
  - 探している間に、その部屋のスレッドが表を広げると、まれに例外になる。例外はイベントのログに出るだけで、ほかの処理は続く。
- **書き換えるのは 1 か所**：トーナメント式のデスマッチで、会場を捨てるとき（本番はトーナメント式で、会場を使っている）。
  - 回戦が進んで会場を減らすと、空にした会場をサーバースレッドで捨てる（`Arenas.retire` → `unloadOrKeep`）。
  - すると、全部の部屋の `CountdownListener.onWorldUnload` が、サーバースレッドで `anchors.values().removeIf(...)` を回す。
  - 会場の持ち主の部屋の `anchors` には、その会場の位置が残っている。デスマッチの間は状態が変わらないので、`anchors` は回戦をまたいで残る。
  - 同じころ、その部屋のスレッドは次の回戦のカウントダウンで、移ってきた人の位置を `anchors` に入れている（`onTeleport` → `anchors().put`、`CountdownListener.java:89`）。
  - 2 つのスレッドが同じ表を同時に書き換えると、表が壊れることがある。壊れると、カウントダウン中に動ける人が出るか、例外になる。
- **直し方の案**：
  - `onWorldUnload` の片付けは、`room.execute` でその部屋のスレッドに回す。
  - 退出の 4 つは、まず自分の部屋の人か（`room.has(player)`）を確かめる。`MapEditor` の分は、サーバースレッドに回す。

### L3 サーバーを止めると、待っている部屋がそれぞれマップを読み込んでから捨てる（SG）

- 止めるとき（`onDisable`）は、部屋ごとに `game.stop()` を呼ぶ（`SurvivalGames.java:294`）。`stop` は、今の状態の `onExit` を呼ぶ（`Game.java:51`）。
- ロビーの `onExit` は、まだマップを決めていなければ、ここで選んで読み込み、台座へ移し始める（`LobbyState.java:178-223`）。誰も投票していなくても、候補からマップを 1 つ選ぶ。
  - コメント（`LobbyState.java:184`）は「ここに来るのは stop() のときだけ」と書いているが、それは選べるマップがない場合だけの話になっている。
- 人のいない部屋は、ロビーで待ったまま、マップを決めずにいる。そのため、止めるたびに、部屋の数（本番は 12）だけテンプレートのワールドを読み込む。読み込んだワールドは、すぐあとに捨てている（`SurvivalGames.java:312`）。
- **影響**：止めるのが少し遅くなる。11-24 の停止は、SurvivalGames を止め始めてから 1 秒で終わったので、今は大きくない。
- 読み込みはログに出ないので、確かめていない。
- **直し方の案**：
  - `Game.stop` で、`onExit` を呼ぶ前に「止めている」という印を立てる。
  - `LobbyState.onExit` は、その印が立っていればマップを読み込まずに戻る。

## 小さいもの

- **`ChatColors.onChange`**：色を変えるたびに、別々の非同期のタスクで保存する。続けて 2 回変えると、保存の順が入れ替わり、古い色が残ることがある。
- **`SgBot.huntPoint`**：残りが 3 人以下で敵を探すとき、試合を待っている人（`benched`）も相手に選ぶ。トーナメント式のデスマッチでは、待っている陣営のほうへ歩くことがある。`perceive` は待っている人を除いている。

## 問題がなかった所

- **ロビー（`LobbyState`）**：
  - 投票は残り 0 秒で締め切る。マップを開いたら、台座のまわりを読み終えるまで 0 秒で止める（上限 3 秒）。
  - 人が足りなくなったら、決めたマップを捨てて数え直す。
- **会場（`Arenas`・`DeathmatchVenue`）**：
  - 部屋のスレッドから頼むと、サーバースレッドで読み込み、試合のマップのスレッドに入れてから渡す。
  - 届くまでに試合が終わっていたら捨てる（`venueGeneration`）。
  - 捨てる会場は、残っている人を移してからサーバースレッドで捨てる。捨てられなければ、次に捨てるときにもう一度試す。
  - ゲートは下の段から開け、次の回戦の前にもとのブロックへ戻す。開けている途中で閉じ直したら、残りの段は開けない。
- **戦いの記録（`CombatTracker`）**：試合が変わったら表を作り直す。空腹の効果は、ほかの理由でかかっているものを上書きしない。
- **ボットの出入り（`BotRoster`）**：
  - ボットのログインは `PlayerLoginEvent` だけを通る（Athena 0399）。そのため、ログイン前の戦績・ランクの読み込み（`AsyncPlayerPreLoginEvent`）で DB に行かない。
  - ログインに失敗したら、入る部屋の予約を消す。
- **ボット（`SgBot` の後半・`Gear`）**：
  - チェストは見えて届くものだけを開ける。戦いに移るときは、急いで取ってから閉じる。
  - 弓は引ききってから放す。防具はいちばん良いものを着る。
- **マップ作り（`MapEditor`・`/sg`）**：
  - 使えるのは管理者だけ。マップの名前（yml のファイル名）は英数字・`_`・`-` の 32 文字まで。
  - 作るためのワールドは保存しない。最後の 1 人が抜けたら捨てる。
- **#54 の見直し**：
  - 書けなかった戦績は、頼まれた順に書き直す。読み込みは、同じスレッドで書く分のあとに並ぶので、まだ書けていない分も含めて返す。
  - 止めるときは、最後にもう一度書きに行き、だめなら 1 件ずつログに残す。
- **Velocity**：
  - 転送は BungeeGuard。秘密のファイル（`forwarding.secret`、ArcanaRanks の `config.properties`）は、velocity のユーザーだけが読める（600）。`/opt/velocity` も 750。
  - バックエンドは、Hub が 127.0.0.1、sg2 が内部網だけ。
  - 版は 4.2.0。今の起動からのログに、警告とエラーはない。
- **本番**：
  - sg2 と Hub は、11-24 に入れ替えてからエラーが 0 件。
  - メモリの空きは、両方とも約 430 MB（`available`）。ディスクは 13% 以下。再起動待ちの更新はない。
  - VPS-A に、まだ入っていないセキュリティ更新が 3 つある（`libssh-4`、`python3-oauthlib`、`tzdata`）。今朝の更新は、一覧を取り直す前（06:54）に動いたので、次の朝に入る。M1 のとおり、そのとき Hub と Velocity が再起動されるかもしれない。

## 読んでいない所

- SG：`Engagements`（中身の計算）、`Terrain`・`Navigator`（経路探索。11-18 の #51 で見た所を除く）、`Teams`、`Tournament`、`BorderSchedule`。
- ArcanaRanks（Velocity のプラグイン）のコード。
- Athena：11-16・11-17 で読んだ所は、もう一度は読んでいない。
