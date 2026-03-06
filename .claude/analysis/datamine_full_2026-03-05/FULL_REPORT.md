# Full APK Data Report (2026-03-05)

Source APK: `apk-pulled/patched/base.apk`

## Scope
This report inventories all data extracted from the APK in this run, plus protocol counters currently observable from the running patched client.

## Extraction Coverage
- Activities parsed from manifest: **90**
- Intent filters extracted: **27**
- VIEW-capable filters: **8**
- Filters with URI schemes: **8**
- Unique URI schemes: **7**
- Unique URLs from dex strings: **283**
- Unique proto field-map message/type keys: **3770**
- Wire registry ID->name rows: **4169**

## URI Scheme / Deeplink Surface
| Scheme | Notes |
|---|---|
| `discord-1432631371519758449` | auth/callback |
| `fbconnect` | auth/callback |
| `kingdomguard` | auth/callback |
| `lineauth` | auth/callback |
| `tfwk1` | launcher/main |
| `vk51877677` | auth/callback |
| `web-iap-result` | billing callback |

Key finding: manifest-declared deeplink schemes are dominated by launcher/auth/payment callbacks; no explicit gameplay menu route scheme/path was found in manifest filters.

## Intent Handler Table (VIEW / Scheme-relevant)
| Activity | Exported | Schemes | Hosts | Likely role |
|---|---|---|---|---|
| `com.tap4fun.framework.activity.MainPlayerActivity` | `True` | `tfwk1` | `` | main launcher |
| `com.tap4fun.tgs.login.twitter.TGSTwitterLoginManager$TwitterRedirectUriReceiverActivity` | `False` | `kingdomguard` | `` | auth/callback |
| `com.appcoins.sdk.billing.WebIapCommunicationActivity` | `True` | `web-iap-result` | `com.tap4fun.odin.kingdomguard` | auth/callback |
| `com.discord.socialsdk.AuthenticationActivity` | `True` | `discord-1432631371519758449` | `` | auth/callback |
| `com.linecorp.linesdk.auth.internal.LineAuthenticationCallbackActivity` | `True` | `lineauth` | `` | auth/callback |
| `net.openid.appauth.RedirectUriReceiverActivity` | `True` | `kingdomguard` | `` | auth/callback |
| `com.facebook.CustomTabActivity` | `True` | `fbconnect` | `cct.com.tap4fun.odin.kingdomguard` | auth/callback |
| `com.vk.id.internal.auth.RedirectUriReceiverActivity` | `True` | `vk51877677` | `vk.com` | auth/callback |

## Network / Endpoint Artifacts
Top URL hosts by count in extracted URL set:
| Count | Host |
|---:|---|
| 19 | `www.w3.org` |
| 13 | `www.googleapis.com` |
| 12 | `developer.android.com` |
| 11 | `www.slf4j.org` |
| 9 | `a.smobgame.com` |
| 8 | `xml.org` |
| 8 | `googleads.g.doubleclick.net` |
| 7 | `developers.facebook.com` |
| 7 | `firebase.google.com` |
| 7 | `github.com` |
| 7 | `issuetracker.google.com` |
| 6 | `www.google.com` |
| 5 | `goo.gle` |
| 4 | `dashif.org` |
| 4 | `facebook.com` |
| 4 | `play.google.com` |
| 4 | `tools.ietf.org` |
| 3 | `jdom.org` |
| 3 | `dash.applovin.com` |
| 3 | `fb.gg` |
| 3 | `pagead2.googlesyndication.com` |
| 2 | `apache.org` |
| 2 | `jabber.org` |
| 2 | `schemas.android.com` |
| 2 | `www.example.com` |
| 2 | `access.line.me` |
| 2 | `api.hhgame.vn` |
| 2 | `app-measurement.com` |
| 2 | `developers.google.com` |
| 2 | `discord.com` |
| 2 | `exoplayer.dev` |
| 2 | `goo.gl` |
| 2 | `ms.applovin.com` |
| 2 | `outcome-ssp.supersonicads.com` |
| 2 | `rt.applvn.com` |
| 2 | `support.google.com` |
| 2 | `youtrack.jetbrains.com` |
| 1 | `chartboo.st` |
| 1 | `etherx.jabber.org` |
| 1 | `g.co` |
| 1 | `javax.xml.xmlconstants` |
| 1 | `localhost` |
| 1 | `ns.adobe.com` |
| 1 | `schemas.applovin.com` |
| 1 | `schemas.microsoft.com` |
| 1 | `temporary` |
| 1 | `w3.org` |
| 1 | `www.bouncycastle.org` |
| 1 | `www.corp.aarki.com` |
| 1 | `%s` |
| 1 | `a.applovin.com` |
| 1 | `a.applvn.com` |
| 1 | `accounts.google.com` |
| 1 | `admob-gmats.uc.r.appspot.com` |
| 1 | `aomedia.org` |
| 1 | `api.line.me` |
| 1 | `api.taboola.com` |
| 1 | `api.twitter.com` |
| 1 | `api.vk.com` |
| 1 | `api.weibo.com` |
| 1 | `api.weixin.qq.com` |
| 1 | `app.adjust.cn` |
| 1 | `app.adjust.com` |
| 1 | `app.adjust.net.in` |
| 1 | `app.adjust.world` |
| 1 | `app.eu.adjust.com` |
| 1 | `app.tr.adjust.com` |
| 1 | `app.us.adjust.com` |
| 1 | `applovin.com` |
| 1 | `assets.applovin.com` |
| 1 | `chartboost.com` |
| 1 | `cloud.google.com` |
| 1 | `console.firebase.google.com` |
| 1 | `core.sdkmain.com` |
| 1 | `csi.gstatic.com` |
| 1 | `d.applovin.com` |
| 1 | `d.applvn.com` |
| 1 | `da.chartboost.com` |
| 1 | `default.url` |
| 1 | `developer.apple.com` |

Representative host groups observed:
- Core game/account/payment style hosts: `a.smobgame.com`, `pay.tap4fun.com`, `*.sdkmain.com`, `api.hhgame.vn`
- Social/auth SDK hosts: `facebook.com`, `discord.com`, `id.vk.com`, `access.line.me`, `accounts.google.com`
- Ads/measurement SDK hosts: `applovin`, `chartboost`, `googleads.g.doubleclick.net`, `adjust`, `supersonicads`
- Firebase/Google infra hosts: `firebase*`, `www.googleapis.com`, `play.google.com`

## Protocol Surface from APK Registries
Family counts (from `wire_registry` name patterns):
| Family Prefix | Count |
|---|---:|
| `ActvUnion` | 45 |
| `Buff` | 6 |
| `Chat` | 20 |
| `ExploreAtlas` | 16 |
| `HeroSkill` | 6 |
| `Kvk` | 142 |
| `Lineup` | 9 |
| `Mail2Nd` | 60 |
| `Quest` | 24 |
| `Rally` | 35 |
| `Rank` | 14 |
| `RedPoint` | 3 |
| `ResourceMine` | 29 |
| `Shop` | 8 |
| `Union` | 304 |
| `WildMap` | 4 |

## Runtime Packet Counters (Current Snapshot)
- Direction `both`: total messages=3140, total types=458
- Direction `recv`: total messages=2333, total types=283
- Direction `send`: total messages=807, total types=175

Top outbound packet names (current snapshot):
| Message | Count |
|---|---:|
| `HeartBeatReq` | 94 |
| `AdventureStageStartReq` | 48 |
| `RankListReq` | 48 |
| `AdventureStageFinishReq` | 47 |
| `WildMapViewReq` | 40 |
| `RedPointReq` | 39 |
| `SetPChatReadTsReq` | 19 |
| `UnifyPlayerInfosReq` | 17 |
| `PlayerGetCrossRankListReq` | 16 |
| `Mail2NdHeadListReq` | 14 |
| `UnifyPlayerInfoReq` | 14 |
| `PlayerMapUnitsReq` | 14 |
| `UnifyBuildingReq` | 14 |
| `UnionBulletinListReq` | 12 |
| `ReleaseHeroSkillReq` | 10 |
| `CopyHeroDataReq` | 10 |
| `ChatPullMsgReq` | 9 |
| `KvkRankListReq` | 9 |
| `Mail2NdModules` | 7 |
| `Mail2NdHead` | 7 |
| `CancelMapViewReq` | 7 |
| `NewLineupStateReq` | 7 |
| `CoordsFavoritesReq` | 7 |
| `UnionGiftInfoReq` | 6 |
| `ActvInfoReq` | 6 |
| `BPShopReq` | 6 |
| `KvkAchievementReq` | 6 |
| `KvkQuestInfoReq` | 6 |
| `UnionReq` | 5 |
| `KvkLineStateReq` | 5 |
| `KvkLineVotePanelReq` | 5 |
| `GetRePowerDiamondCostReq` | 5 |
| `UnionMemberKickOutSettingInfoReq` | 4 |
| `EnterWorldReq` | 4 |
| `DragonArenaQueryReq` | 4 |
| `PhyPowerInfoReq` | 4 |
| `KvkInformationReq` | 4 |
| `CpeTaskInfoReq` | 4 |
| `GetShopInfoReq` | 4 |
| `Mail2NdContentReq` | 3 |
| `ChatPullPrivateListReq` | 3 |
| `ActvCustomEmotionListReq` | 3 |
| `UnionAltarReq` | 3 |
| `PvpInfoReq` | 3 |
| `KvkWeatherReq` | 3 |
| `KvkSuppressReq` | 3 |
| `ActvListReq` | 3 |
| `ActvCalendarV2Req` | 3 |
| `InvitationPanelReq` | 3 |
| `EvilInvasionQueryReq` | 3 |
| `AuctionOpenUIReq` | 3 |
| `BPNewGetInfoReq` | 3 |
| `SeasonChallengeInfoQueryReq` | 3 |
| `ActvUnionDuelInfoReq` | 3 |
| `KvkBPInfoReq` | 3 |
| `SupplyListReq` | 3 |
| `AdEventEndReq` | 3 |
| `WildMapUnionViewReq` | 3 |
| `HeroSkillPropReq` | 3 |
| `NewBuffListReq` | 3 |

## Raw Artifact Index
All raw outputs are in this folder:
- `.claude/analysis/datamine_full_2026-03-05`

- `INDEX.md` (650 bytes)
- `activities_all.json` (15923 bytes)
- `deeplink_related_strings.txt` (57033 bytes)
- `dex_strings_all.txt` (17865653 bytes)
- `domains_all_count.txt` (653138 bytes)
- `intent_filters_all.json` (7284 bytes)
- `manifest_xmltree.txt` (86522 bytes)
- `proto_field_map_keys.txt` (72930 bytes)
- `protocol_message_counts_both.json` (20821 bytes)
- `protocol_message_counts_recv.json` (12829 bytes)
- `protocol_message_counts_send.json` (8114 bytes)
- `url_hosts_count.txt` (2812 bytes)
- `urls_all_unique.txt` (15147 bytes)
- `wire_family_counts.tsv` (164 bytes)
- `wire_registry_id_to_name.tsv` (125207 bytes)
