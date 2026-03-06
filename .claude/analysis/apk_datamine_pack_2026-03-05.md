# APK Datamine Pack (2026-03-05)

Scope: deep-link/intent surface, network/domain artifacts, and outbound request mapping from live capture.

## 1) Intent + Deeplink Surface
| Activity | Exported | Action | Categories | Scheme | Host | Path | Likely purpose |
|---|---|---|---|---|---|---|---|
| `com.tap4fun.framework.activity.MainPlayerActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `tfwk1` | `` | `` | main launcher |
| `com.tap4fun.tgs.login.twitter.TGSTwitterLoginManager$TwitterRedirectUriReceiverActivity` | `False` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `kingdomguard` | `` | `` | auth/callback |
| `com.appcoins.sdk.billing.WebIapCommunicationActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `web-iap-result` | `com.tap4fun.odin.kingdomguard` | `` | sdk/other |
| `com.discord.socialsdk.AuthenticationActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `discord-1432631371519758449` | `` | `` | auth/callback |
| `com.linecorp.linesdk.auth.internal.LineAuthenticationCallbackActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `lineauth` | `` | `` | auth/callback |
| `net.openid.appauth.RedirectUriReceiverActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `kingdomguard` | `` | `` | auth/callback |
| `com.facebook.CustomTabActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `fbconnect` | `cct.com.tap4fun.odin.kingdomguard` | `` | auth/callback |
| `com.vk.id.internal.auth.RedirectUriReceiverActivity` | `True` | `android.intent.action.VIEW` | `android.intent.category.DEFAULT, android.intent.category.BROWSABLE` | `vk51877677` | `vk.com` | `` | auth/callback |

Conclusion: no explicit gameplay-menu deeplink paths were found in manifest filters; schemes are mostly launcher/auth/payment callbacks.

## 2) Cleaned Endpoint/Domain Artifacts (from classes*.dex strings)
Top domains by occurrence (cleaned):
| Count | Domain |
|---:|---|
| 13 | `www.googleapis.com` |
| 9 | `a.smobgame.com` |
| 8 | `googleads.g.doubleclick.net` |
| 7 | `developers.facebook.com` |
| 7 | `firebase.google.com` |
| 7 | `github.com` |
| 6 | `www.google.com` |
| 5 | `goo.gle` |
| 4 | `dashif.org` |
| 4 | `facebook.com` |
| 4 | `play.google.com` |
| 3 | `assets.applovin.com` |
| 3 | `dash.applovin.com` |
| 3 | `fb.gg` |
| 3 | `pagead2.googlesyndication.com` |
| 2 | `cdnjs.cloudflare.com` |
| 2 | `d.applovin.com` |
| 2 | `img.applovin.com` |
| 2 | `jabber.org` |
| 2 | `pdn.applovin.com` |
| 2 | `res.applovin.com` |
| 2 | `res1.applovin.com` |
| 2 | `res2.applovin.com` |
| 2 | `res3.applovin.com` |
| 2 | `stage-assets.applovin.com` |
| 2 | `stage-img.applovin.com` |
| 2 | `stage-pdn.applovin.com` |
| 2 | `stage-vid.applovin.com` |
| 2 | `u.appl.vn` |
| 2 | `vid.applovin.com` |
| 2 | `access.line.me` |
| 2 | `api.hhgame.vn` |
| 2 | `app-measurement.com` |
| 2 | `developers.google.com` |
| 2 | `discord.com` |
| 2 | `exoplayer.dev` |
| 2 | `goo.gl` |
| 2 | `ms.applovin.com` |
| 2 | `outcome-ssp.supersonicads.com` |
| 2 | `rt.applovin.com` |

Sample high-signal URLs:
- `http://assets.applovin.com/`
- `http://d.applovin.com/`
- `http://img.applovin.com/`
- `http://pdn.applovin.com/`
- `http://res.applovin.com/`
- `http://res1.applovin.com/`
- `http://res2.applovin.com/`
- `http://res3.applovin.com/`
- `http://schemas.applovin.com/android/1.0`
- `http://stage-assets.applovin.com/`
- `http://stage-img.applovin.com/`
- `http://stage-pdn.applovin.com/`
- `http://stage-vid.applovin.com/`
- `http://vid.applovin.com/`
- `https://a.applovin.com/`
- `https://a.smobgame.com`
- `https://a.smobgame.com/plf/payments/pay`
- `https://a.smobgame.com/plf/users`
- `https://a.smobgame.com/plf/users/login_email`
- `https://a.smobgame.com/plf/users/login_email?`
- `https://a.smobgame.com/plf/users/register_mobile`
- `https://a.smobgame.com/plf/users/register_phone`
- `https://a.smobgame.com/plf/users/reveiceAccessTokenV2`
- `https://a.smobgame.com/plf/users/reveiceMacAddress`
- `https://access.line.me/.well-known/openid-configuration`
- `https://access.line.me/oauth2/v2.1/login`
- `https://api.line.me/`
- `https://api.vk.com`
- `https://app.adjust.cn`
- `https://app.adjust.com`
- `https://app.adjust.net.in`
- `https://app.adjust.world`
- `https://app.eu.adjust.com`
- `https://app.tr.adjust.com`
- `https://app.us.adjust.com`
- `https://applovin.com.`
- `https://assets.applovin.com/`
- `https://assets.applovin.com/gdpr/flow_v1/gdpr-flow-1.html`
- `https://chartboost.com`
- `https://console.firebase.google.com/.`
- `https://core.sdkmain.com/`
- `https://d.applovin.com/`
- `https://da.chartboost.com`
- `https://dash.applovin.com/documentation/mediation/android/getting-started/integration`
- `https://dash.applovin.com/documentation/mediation/android/getting-started/integration#enabling-max-built-in-consent-flow`
- `https://dash.applovin.com/documentation/mediation/unity/getting-started/integration#max-built-in-consent-flow`
- `https://discord.com`
- `https://discord.com/oauth2/authorize`
- `https://firebase.google.com/docs/analytics`
- `https://firebase.google.com/docs/android/kotlin-migration.`
- `https://firebase.google.com/docs/crashlytics/get-started?platform=android#add-plugin`
- `https://firebase.google.com/docs/database/android/retrieve-data#filtering_data`
- `https://firebase.google.com/docs/database/ios/structure-data#best_practices_for_data_structure`
- `https://firebase.google.com/support/guides/disable-analytics`
- `https://firebase.google.com/support/privacy/init-options.`
- `https://gdpr.adjust.cn`
- `https://gdpr.adjust.com`
- `https://gdpr.adjust.net.in`
- `https://gdpr.adjust.world`
- `https://gdpr.eu.adjust.com`
- `https://gdpr.tr.adjust.com`
- `https://gdpr.us.adjust.com`
- `https://googleads.g.doubleclick.net`
- `https://googleads.g.doubleclick.net/mads/static/mad/sdk/native/native_ads.html`
- `https://googleads.g.doubleclick.net/mads/static/mad/sdk/native/production/mraid/v3/mraid_app_banner.js`
- `https://googleads.g.doubleclick.net/mads/static/mad/sdk/native/production/mraid/v3/mraid_app_expanded_banner.js`
- `https://googleads.g.doubleclick.net/mads/static/mad/sdk/native/production/mraid/v3/mraid_app_interstitial.js`
- `https://googleads.g.doubleclick.net/mads/static/mad/sdk/native/production/sdk-core-v40-impl.html`
- `https://googleads.g.doubleclick.net/mads/static/mad/sdk/native/sdk-core-v40-loader.html`
- `https://googleads.g.doubleclick.net/mads/static/sdk/native/sdk-core-v40.html`
- `https://helium-rtb.chartboost.com`
- `https://helium-sdk.chartboost.com`
- `https://id.vk.com`
- `https://img.applovin.com/`
- `https://init.supersonicads.com/sdk/v`
- `https://interact.sdkmain.com/`
- `https://issue.sdkmain.com/`
- `https://live.chartboost.com`
- `https://monetization-support.applovin.com/hc/en-us/articles/236114328-How-can-I-expose-verbose-logging-for-the-SDK`
- `https://ms.applovin.com/`

Total cleaned URLs: `262`

## 3) Top 50 Outbound Requests + Likely UI Trigger
Source session: `stats/protocol_baseline/session_20260305_1206_send_hunt` (direction=send)
| Rank | Request | Delta | Likely UI trigger | Confidence |
|---:|---|---:|---|---|
| 1 | `WildMapViewReq` | 40 | World Map View | high |
| 2 | `RankListReq` | 28 | Rankings/Leaderboards | high |
| 3 | `HeartBeatReq` | 20 | Unknown/General | low |
| 4 | `SetPChatReadTsReq` | 19 | Chat | medium |
| 5 | `UnifyPlayerInfosReq` | 17 | Unknown/General | low |
| 6 | `UnifyBuildingReq` | 14 | Unknown/General | low |
| 7 | `UnifyPlayerInfoReq` | 14 | Unknown/General | low |
| 8 | `Mail2NdHeadListReq` | 13 | Mail | high |
| 9 | `UnionBulletinListReq` | 11 | Alliance/Union | medium |
| 10 | `PlayerMapUnitsReq` | 11 | World Map Entities | medium |
| 11 | `RedPointReq` | 10 | Notifications/Red Dots | high |
| 12 | `ChatPullMsgReq` | 9 | Chat | high |
| 13 | `KvkRankListReq` | 9 | Rankings/Leaderboards | high |
| 14 | `AdventureStageStartReq` | 8 | Unknown/General | low |
| 15 | `PlayerGetCrossRankListReq` | 8 | Rankings/Leaderboards | medium |
| 16 | `AdventureStageFinishReq` | 8 | Unknown/General | low |
| 17 | `NewLineupStateReq` | 7 | Troops/Lineup | medium |
| 18 | `ReleaseHeroSkillReq` | 6 | Hero | medium |
| 19 | `CopyHeroDataReq` | 6 | Hero | medium |
| 20 | `KvkAchievementReq` | 6 | Kvk/Event | medium |
| 21 | `KvkQuestInfoReq` | 6 | Kvk/Event | medium |
| 22 | `ActvInfoReq` | 6 | Unknown/General | low |
| 23 | `BPShopReq` | 6 | Shop | medium |
| 24 | `UnionGiftInfoReq` | 6 | Alliance/Union | medium |
| 25 | `UnionReq` | 5 | Alliance/Union | medium |
| 26 | `GetRePowerDiamondCostReq` | 5 | Unknown/General | low |
| 27 | `KvkLineVotePanelReq` | 5 | Kvk/Event | medium |
| 28 | `KvkLineStateReq` | 5 | Kvk/Event | medium |
| 29 | `EnterWorldReq` | 4 | Unknown/General | low |
| 30 | `KvkInformationReq` | 4 | Kvk/Event | medium |
| 31 | `PhyPowerInfoReq` | 4 | Unknown/General | low |
| 32 | `CoordsFavoritesReq` | 4 | Map Favorites/Coordinates | medium |
| 33 | `DragonArenaQueryReq` | 4 | Dragon/Event | medium |
| 34 | `UnionMemberKickOutSettingInfoReq` | 4 | Alliance/Union | medium |
| 35 | `CancelMapViewReq` | 4 | Unknown/General | low |
| 36 | `GetShopInfoReq` | 4 | Shop | medium |
| 37 | `BPNewGetInfoReq` | 3 | Battle Pass/Event Pass | medium |
| 38 | `DragonInfoReq` | 3 | Dragon/Event | medium |
| 39 | `KvkWeatherReq` | 3 | Kvk/Event | medium |
| 40 | `NewBuffListReq` | 3 | Unknown/General | low |
| 41 | `ChatPullPrivateListReq` | 3 | Chat | medium |
| 42 | `PvpInfoReq` | 3 | PVP | medium |
| 43 | `HeroSkillPropReq` | 3 | Hero | medium |
| 44 | `ActvListReq` | 3 | Unknown/General | low |
| 45 | `SeasonChallengeInfoQueryReq` | 3 | Unknown/General | low |
| 46 | `ActvCalendarV2Req` | 3 | Unknown/General | low |
| 47 | `QuestInfoReq` | 3 | Quest Panel | medium |
| 48 | `AuctionOpenUIReq` | 3 | Unknown/General | low |
| 49 | `ActvUnionDuelInfoReq` | 3 | Alliance/Union | medium |
| 50 | `ActvCustomEmotionListReq` | 3 | Unknown/General | low |

## 4) Next Datamine Steps
1. Build request-response pairs (`*Req` -> `*Ack`/`*Ntf`) from directional counters.
2. Add per-action tagging: start capture marker before each manual action block for stronger UI->packet attribution.
3. Expand APK static scan into IL2CPP metadata correlation for internal enum/key interpretation (e.g., RedPoint dictionary keys).
