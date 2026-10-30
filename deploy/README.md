# deploy

どのファイルが、どこで使われているか。本番（ConoHa 1GB・2 vCPU × 2 台）の unit とスクリプトは、本番に置いてあるものをそのまま写している。本番で変えたら、ここも同じように直す。

| ファイル | 置き場所 | 使っている所 |
|---|---|---|
| `start-small.sh` | `/opt/mc/start-small.sh` | 本番の hub・sg1・sg2 がすべてこれで起動する。JVM の設定、パッチ、AOT のキャッシュの説明は、ファイルの先頭にある |
| `sg@.service` | `/etc/systemd/system/sg@.service`（VPS-B） | sg1・sg2（`systemctl start sg@sg1`）。tmux の中で `start-small.sh` を動かす。`MemoryMax=400M` |
| `hub.service` | `/etc/systemd/system/hub.service`（VPS-A） | hub。sg@ と同じ作りで、`MemoryMax=400M` |
| `velocity.service` | `/etc/systemd/system/velocity.service`（VPS-A） | Velocity。`MemoryMax=320M` |
| `athena_sg_when_empty.sh` | `/root/athena_sg_when_empty.sh`（VPS-B） | sg の paperclip.jar とプラグインの入れ替え。接続が 0 人になるのを待ってから替え、AOT のキャッシュを作るためにもう一度起動し直す |
| `hub_when_empty.sh` | `/root/hub_when_empty.sh`（VPS-A） | hub の入れ替え。やることは上と同じ |
| `mc-small@.service` | 本番では使っていない | `start-small.sh` を tmux なしで動かす汎用の unit |
| `mc@.service`・`setup-oracle-a1.sh` | 本番では使っていない | Oracle Always Free（12 GB）用。ヒープ 3 GB なので、1 GB の VPS には使えない |

入れ替えのスクリプトは実行権限なしで置いてあるので、`bash /root/athena_sg_when_empty.sh sg2 25566 <paperclip.jar> <SurvivalGames.jar> <tag>` のように `bash` で動かす。
