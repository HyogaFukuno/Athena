# Athena と SurvivalGames の精査 — 2026-11-23

## 対象と方法

- **Athena**：`origin/master` `92aa14a`。コードは本番の sg2 の server 0456（`db42494`）と同じで、違うのは `audit/` の下だけ。
- **SurvivalGames**：`v2` `e2dff71`。本番の sg2 と同じ jar（`57cbb987`）。
- **AthenaHub**：`e516a5b`。VPS-A の Hub と同じもの。
- **読んだ所**：11-22 の精査（`audit/2026-11-22/REVIEW.md`）が「読んでいない所」に挙げたものを中心にした。
  - SG：`Scoreboards`、`CountdownListener`、`ChestLids`・`ChestListener`、`SpectatorCompassListener`、`ArrowLimit`、`MapVote`、`/records`、権限（`plugin.yml`）、ランク・変装（`PlayerRanks`・`Disguises`）、戦績の読み書き（`QueuedStatsRepository`・`MariaDbStatsRepository`・`StatsService`）、#53 で直した `PlayerReset`（見直し）、`SgBot` の前半。
  - Athena：SG が別のスレッドから使うスコアボード（`CraftScoreboardManager`）、退出のイベント、変装の名前の送り方。
  - AthenaHub：DB へのつなぎ方。
  - 本番：両方の VPS の再起動の記録、MariaDB の止まった時間、SSH、開いているポート、メモリ、sg2 のエラー。
- **確かめ方**：本番の再起動と DB の止まった時間は、ログで確かめた。ほかはコードを読んだ結果で、再現はしていない。

## まとめ

- **高**はない。
- **中**は SG に 1 件（M1）。試合の途中で DB が止まると、その間に保存する戦績を捨てる。
  - 両方の VPS が、この 1 週間に ConoHa 側から 1 回ずつ止められている。VPS-A は 11-16 に 11 分、VPS-B は 11-20。
  - VPS-A には Velocity と DB が同居している。VPS-A が止まると、人は全員 sg2 から切れ、試合は身代わりで続き、DB が止まっている間に決着して保存する。この並びが、この形で起きる。
- **低**は 4 件（L1〜L4）。
- Athena のコードに、新しい問題は見つからなかった。

## 中（SG）

### M1 試合の途中で DB が止まると、その間に保存する戦績を捨てる

- **何が起きるか**：
  - 戦績とポイントの保存は、専用のスレッドで 1 回だけ書きに行く（`QueuedStatsRepository.add`）。
  - 失敗すると「Lost the stats of …; add them by hand」とログに出して捨てる（`QueuedStatsRepository.java:79`）。やり直さない。
  - 11-22 の H1（SG #52）で直したのは、起動のときにつなぐ所だけ。動いている途中の書き込みは、前と同じ。
- **本番で起きる流れ**：
  - VPS-A は 11-16 03:21 に ConoHa 側から止められ（`qemu-ga: guest-shutdown`、`hypervisor initiated shutdown`）、03:32 に起き直した。MariaDB は 03:21:24〜03:32:47 の 11 分止まっていた。
  - VPS-A には Velocity も同居しているので、人はその時に全員 sg2 から切れる。切れた人は試合から抜けず、スケルトンが代わりに戦い、決着したとき（`Disconnects` → `finish`）にまとめて保存する。
  - 11 分のうちに決着した試合では、人の戦績・勝ち・ポイントがすべて捨てられる。
  - 11-16 は人のログインがなかったので、実害はなかった（sg2 のログに「Lost the stats」は 0 件、10-30 からのログ全部）。
- **ほかにも起きる場面**：MariaDB の再起動（更新）、内部網の一時的な切れ目。VPS-A は `unattended-upgrades` が有効。
- **直し方の案**：
  - 書けなかった分を、人ごとに足し合わせて手元に持ち、間を広げながら書き直す（1・2・4・…・30 秒ごと）。
  - 読み込み（`load`）では、まだ書けていない分を足して返す。DB が戻る前に入り直した人にも、正しい値が出る。
  - 止めるとき（`close`）は、残っている分をもう一度書きに行き、だめならこれまでどおりログに残す。
  - 書いた結果の返事だけが届かなかった場合は、2 回足されることがある。足し込みの保存（`ON DUPLICATE KEY UPDATE points = points + ?`）なので、これは防げない。起きにくいので許す。

## 低

### L1 Hub は起動時に DB へ 1 回だけつなぎ、失敗すると再起動まで全員 Regular（AthenaHub・運用）

- `AthenaPlugin.openRanks` は 1 回つなぎに行き、失敗すると「everyone is Regular until restart」で諦める。11-22 の H1 と同じ形。
- VPS-A では Hub と MariaDB が同じ箱で同時に起きる。`hub.service` は `After=network-online.target velocity.service` だけで、MariaDB を待たない。
- 11-16 は、MariaDB が 03:32:47 に起き、Hub が 03:32:50 に読みに行って間に合った。順番がたまたま良かっただけ。
- **直し方の案**：`hub.service` に `After=mariadb.service` と `Wants=mariadb.service` を足す（ArcanaServers）。AthenaHub にも、SG の `ConnectRetry` と同じつなぎ直しを入れる。

### L2 ログインのときにランクだけ読めないと、保存してある変装とマスクを消す（SG）

- ログイン前に、ランク・名前の色・変装を別々に読む（`SurvivalGames.onPreLogin`）。読めなかったものは、それぞれ Regular・なしになる。
- ランクだけが読めず、変装は読めた場合、入ったときに次のようになる（`Disguises.join`、`Disguises.java:82`）。
  - 「変装の権限がない」とみなし、変装を外す。
  - 保存してある変装も、null で書き換える（`saveDisguise(id, null, …)`）。
  - マスクも同じく消す。
- DB が一瞬だけ失敗する（読み込みの間に切れる）ときにしか起きない。
- **直し方の案**：ランクを読めなかった人には、変装やマスクを外しても、保存は書き換えない。

### L3 ログインのときに戦績を読めないと、持っていないポイントを使える（SG）

- 読めなかった人は初めての人の戦績になる（`SurvivalGames.java:576`）。持ち点も初期値になる。
- その持ち点で `/sponsor` や `/bounty` ができる。保存は「0 より下にしない」足し込みなので、もとの持ち点が少ない人は、差の分をただで使える。
- M1 を直して書けなかった分を持つようにしても、読めなかった人の問題は残る。読めなかった人には、ポイントを使わせないのが確実。

### L4 変装した人にかけた賞金が、ほかの人の画面に出ない（SG）

- 賞金の数は、スコアボードの本当の名前の行に入れる（`Scoreboards.java:171・172`）。
- Athena は、変装した人をほかの人には変装した名前で送る（`PacketPlayOutPlayerInfo`）。そのため、本当の名前の行はどの人にも結び付かず、タブにも頭の下にも出ない。
- 名前の色（味方・敵）は、本当の名前と見えている名前の両方を入れて対処している（`Scoreboards.addNames`）。賞金にも同じことをすればよい。
- 画面で確かめてはいない。

## Athena

- **新しい問題はない**。コードは 11-17 から 0456 しか変わっていない。
- **SG が部屋ごとのスレッドから触るスコアボードは安全**：`CraftScoreboardManager` は、人ごとのスコアボードの表を `ConcurrentHashMap` にし、スコアボードの一覧をロックで守っている。
- 11-17 の B・C・D と、11-22 の C2（ワールドのスレッドの出入り）は手つかずのまま。

## 運用

- **両方の VPS が、この 1 週間に ConoHa 側から止められた**：VPS-A は 11-16 03:21〜03:32、VPS-B は 11-20 12:43。どちらも `qemu-ga` の `guest-shutdown`。止められても戻れることが、M1 と L1 の前提になる。
- **SSH は両方とも鍵だけ**：`sshd -T` で `passwordauthentication no`、`permitrootlogin without-password`。11-17 の M2 は済んでいる。
- **開いているポート**：
  - VPS-A は SSH、Velocity（25565）、MariaDB（10.10.0.56 だけ）。
  - VPS-B は SSH、sg2（10.10.0.139 だけ）。
  - どちらも ufw が有効。
- **メモリ・ディスク**：
  - VPS-B は使用 538 MB・swap 34 MB。
  - VPS-A は使用 491 MB・swap 173 MB。
  - ディスクはどちらも 13% 以下。
  - 再起動待ちの更新はない。
- **sg2**：11-22 14:30 に SG #53 を入れて起動し直してから、エラーは 0 件。

## 問題がなかった所

- **`/sg`**：`survivalgames.admin`（既定は op）がないと使えない。マップを作るコマンドは、ほかの人には使えない。
- **SQL**：戦績の読み書きは、すべて値を `?` で渡している。テーブルの名前は `config.yml` からで、遊ぶ人は変えられない。
- **`/records`**：オンラインの人だけを、見えている名前（変装を含む）で引く。オフラインの人の戦績を DB に読みに行かない。
- **矢の上限（`ArrowLimit`）**：拾うときは持てる分だけにし、持ち物を動かしたあとと 1 秒ごとに数え直す。#53 で試合に戻るときに足すスポンサーの矢も、ここで上限に戻る。
- **チェストのふた（`ChestLids`）**：見ている人の数は、閉じる・ログアウト・死亡のどれでも減る（`InventoryCloseEvent`）。サーバーのチェストの状態には触らない。
- **カウントダウン（`CountdownListener`）**：投げる物は使うこと自体を止めるので、1.8 でアイテムが減らない。台座から踏み出した分の落下距離も消す。
- **観戦者のコンパス**：動かせず、捨てられない。生存者の頭を押したときは、その人がまだ生存者かを確かめてから移る。頭にはスキンを付けないので、変装した人の本当の名前は出ない。
- **マップの投票**：1 人 1 票。抜けた人の票は取り消す。票に比例して選ぶ。
- **#53 の見直し**：
  - 待っている間に贈られた品は、`unbench` を通るすべての道で渡る（自分の試合が始まる、切断する、休んでいた勝者が次に出る）。
  - 試合が終わったときは `reset` で捨てる。決勝のあとなので、渡す場面はない。

## 読んでいない所

- SG：`SgBot` の後半（戦い方・逃げ方）、`MapEditor`、`LobbyState`、`Arenas`、`DeathmatchVenue`、`CombatTracker`、`Engagements`、`Gear`、`BotRoster`、`ChatColors`、`SurvivalGamesCommand`。
- Athena：11-16・11-17 で読んだ所は、もう一度は読んでいない。
- Velocity：設定もプラグインも読んでいない。
