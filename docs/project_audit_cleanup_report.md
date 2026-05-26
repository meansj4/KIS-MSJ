# KIS LOT Bot Project Audit and Cleanup Report

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. This report records the whole-project audit/cleanup pass performed after the handoff docs. If this report conflicts with `project_handoff_full.md`, re-check code first and then update the full handoff.  
> Last updated: 2026-05-27 / Latest tests after this audit: `155 passed` with one pytest cache warning / Baseline config profile: `expansion_100_safe`.

## 0. 2026-05-27 異붽? 蹂닿컯 ?붿빟

?대쾲 異붽? 蹂닿컯?먯꽌??deprecated inventory??洹몄튂吏 ?딄퀬, 由ъ뒪???놁씠 以꾩씪 ???덈뒗 legacy ?몄텧硫닿낵 ?댁쁺 由ъ뒪?щ? ?ㅼ젣濡??뺣━?덈떎.

| ??ぉ | 議곗튂 |
| --- | --- |
| General UI Config legacy surface | `initial_buy_amount`, `auto_buy_limit`, `absolute_max_investment`, `exposure_buy_bands`, `exposure_sell_bands`, `reentry_drop_rate` were removed from the general Config schema/metadata/form-table surface. Underlying config/model fields remain only for DB/config compatibility and fallback paths. |
| legacy ?ㅻ챸 | UI ?ㅻ챸??`cycle_locked_by_entry_price` 湲곗??쇰줈 ?뺣━?섍퀬 legacy ??ぉ? ?명솚?⑹쑝濡쒕쭔 ?쒖떆 |
| manual request 以묐났 ?뚮퉬 | `StateStore.claim_manual_order_request()` 異붽?. `REQUESTED`?닿퀬 `linked_order_id`媛 鍮꾩뼱 ?덈뒗 row留??먯옄?곸쑝濡?`PROCESSING` claim????泥섎━ |
| manual request runtime interrupt | claim ??submit 吏곸쟾 runtime interrupt媛 諛쒖깮?섎㈃ request瑜?`BLOCKED`濡??꾪솚??PROCESSING stuck怨?以묐났 二쇰Ц??諛⑹? |
| KIS snapshot validator UI | `/api/new-season/validate-snapshot` 異붽?. UI?먯꽌 snapshot 寃쎈줈瑜?利됱떆 寃利앺븯怨?preview 媛??request 媛?μ쓣 遺꾨━ ?쒖떆 |
| ???쒖쫵 ?대? flag | `request_creation_possible` ???대?媛믪? 湲곕낯 ?몄텧 ???怨좉툒 吏꾨떒媛믪쑝濡??묒쓬 |

??젣?섏? ?딆? legacy ?꾨뱶???쒖씪諛?UI/臾몄꽌 ?꾨㈃?앹뿉?쒕뒗 ?④린怨? DB ?명솚/fallback 踰붿쐞?먮쭔 ?④릿??

## 1. 媛먯궗 踰붿쐞

?대쾲 媛먯궗???뱀젙 LOT sizing legacy留?蹂?寃껋씠 ?꾨땲???꾨옒 踰붿쐞瑜??뺤쟻 寃?? 肄붾뱶 ?쎄린, CLI/API ?議? 臾몄꽌 留곹겕 ?먭? 湲곗??쇰줈 ?뺤씤?덈떎.

| 踰붿쐞 | ?뺤씤 ?댁슜 |
| --- | --- |
| `src/kis_msj/**/*.py` | Bot Core, LOT/position/order/storage, UI service/server, runtime control |
| `scripts/**/*.py` | ???쒖쫵 archive/reset/liquidation ?ㅽ겕由쏀듃? CLI ?듭뀡 |
| `tests/**/*.py` | ?꾩옱 湲곕뒫 ?뚯뒪?? legacy ?뚯뒪?몄쓽 ?좎? ?꾩슂??|
| `config/*.json`, `config/**/*.json` | live config, example config, backup config??理쒖떊??|
| `docs/**/*.md`, `README.md` | handoff/local UI/new season/lot sizing/expansion 臾몄꽌 ?뺥빀??|
| UI HTML/CSS/JS | ?? API ?몄텧, ???쒖쫵 wizard 臾멸뎄, manual request ?덈궡 |
| ?댁쁺 蹂댁“ ?뚯씪 | archive/export/runtime 愿??臾몄꽌? 肄붾뱶 寃쎈줈 |

?ㅽ뻾?섏? ?딆? 寃?

- ?ㅺ굅??二쇰Ц
- KIS 二쇰Ц API ?몄텧
- DB reset
- destructive cleanup

## 2. 諛쒓껄??遺덉씪移??붿빟

| 遺꾨쪟 | ??ぉ | ?먮떒 | 議곗튂 |
| --- | --- | --- | --- |
| ?ㅻ옒??config ?덉떆 | `config/lot_auto_trader.example.json`??legacy exposure band 以묒떖, 10媛??곷Ц 醫낅ぉ ?덉떆, `price_lot_bands` ?놁쓬 | 臾몄꽌/?ㅼ젙 ?쇰룞 ?좊컻 | `expansion_100_safe` ?명솚 ?덉떆濡?媛깆떊. live config??嫄대뱶由ъ? ?딆쓬 |
| UI metadata | `exposure_buy_bands`, `exposure_sell_bands`, `auto_buy_limit`, `absolute_max_investment` ?ㅻ챸???꾩옱 cycle-locked LOT sizing蹂대떎 legacy 湲덉븸 湲곗?泥섎읆 蹂댁엫 | UI留??섏젙 ?꾩슂 | ?덇굅???명솚?⑹엫??紐낆떆?섍퀬 ?꾩옱??`add_buy_lot_bands`, `target_profit_lot_bands`, `max_symbol_amount` ?곗꽑?꾩쓣 ?ㅻ챸 |
| UI ???쒖쫵 ?붾㈃ | `request_creation_possible` 媛숈? ?대? flag媛 湲곕낯 ?붾㈃??洹몃?濡??몄텧??| UI留??섏젙 ?꾩슂 | `怨좉툒 吏꾨떒媛?蹂닿린` details濡??묒쓬 |
| ???쒖쫵 wizard step | ?쏹I?먯꽌???꾩쭅 ?ㅽ뻾 踰꾪듉 ?놁씠 ?덉감 ?덈궡留??쒓났??臾멸뎄媛 ?⑥븘 ?덉쓬 | UI 臾멸뎄 ?ㅻ옒??| 諛깆뾽 踰꾪듉??議댁옱?섎뒗 ?꾩옱 援ъ“??留욊쾶 ?섏젙 |
| CLI ?듭뀡 臾몄꽌 | archive root ?듭뀡? ?ㅼ젣濡?`--archive-root` | 臾몄꽌/肄붾뱶 ?뺥빀???뺤씤 | `--help` 湲곗??쇰줈 ?쇱튂 ?뺤씤. 臾몄꽌?먯꽌 ?대? 理쒖떊?붾맖 |
| KIS snapshot strict validation | ?덉쟾 ?쐅enerated_at 沅뚯옣, sellable fallback???쒗쁽 ?붿옱 媛?μ꽦 | 臾몄꽌 ?뺤씤 ?꾩슂 | 二쇱슂 臾몄꽌??preview/create_request 援щ텇?쇰줈 理쒖떊?붾릺???덉쓬 |
| Execution Mapping UI | nav ??? ?쒓굅?먯?留?`loadExecution()`怨?`/api/execution-mapping/status`???⑥븘 ?덉쓬 | deprecated ?좎? ?꾩슂 | ?대? 吏꾨떒/API ?⑸룄濡??좎?. 臾몄꽌???쒗꺆 ?쒓굅, API ?붿〈??紐낆떆??|
| Legacy mode | `legacy_exposure_bands`, `exposure_*_bands`, `initial_buy_amount`, `auto_buy_limit` ?쇰? 肄붾뱶 寃쎈줈 議댁옱 | deprecated ?좎? ?꾩슂 | 湲곗〈 DB/config/test ?명솚 ?뚮Ц????젣?섏? ?딆쓬. UI ?ㅻ챸留??뺣━ |

## 3. ?쒓굅????ぉ

?대쾲 媛먯궗?먯꽌 ?뚯뒪 肄붾뱶瑜?臾쇰━?곸쑝濡???젣????ぉ? ?녿떎. ?댁쑀???ㅼ쓬怨?媛숇떎.

- legacy exposure band 愿???⑥닔? config??`lot_sizing_mode != cycle_locked_by_entry_price`????backward compatibility 寃쎈줈濡??ъ슜?쒕떎.
- `exit_anchor_price`, `base_target_profit_rate`, `auto_buy_limit` ?깆? 湲곗〈 DB row? 濡쒓렇/留덉씠洹몃젅?댁뀡 ?명솚???꾩슂?섎떎.
- `/api/execution-mapping/status`? `loadExecution()`? ?쇰컲 nav?먯꽌???쒓굅?먯?留?泥??ㅼ껜寃?raw mapping 吏꾨떒 API濡??④만 媛移섍? ?덈떎.

## 4. deprecated濡??④릿 ??ぉ

| ?뚯씪 | ??ぉ | ?좎? ?댁쑀 | 二쇱쓽 |
| --- | --- | --- | --- |
| `src/kis_msj/config.py` | `strategy.reentry_drop_rate` | ?덉쟾 ?⑥씪 anchor ?ㅼ젙 ?명솚. UI?먯꽌???④? | ?ㅼ젣 reentry ?먮떒? `normal_reentry_drop_rate`, `trailing_*` ?ъ슜 |
| `src/kis_msj/config.py`, `lot_manager.py`, `strategy.py` | `exposure_buy_bands`, `exposure_sell_bands` | `legacy_exposure_bands` 紐⑤뱶? 湲곗〈 ?뚯뒪???명솚 | 湲곕낯 紐⑤뱶?먯꽌??`add_buy_lot_bands`, `target_profit_lot_bands` ?곗꽑 |
| `positions`/`models` | `auto_buy_limit`, `absolute_max_investment` | 湲곗〈 position row? non-cycle mode ?명솚 | cycle-locked mode?먯꽌??`max_symbol_amount`, `max_lots_per_symbol` ?곗꽑 |
| `lots`/`models` | `base_target_profit_rate`, `target_profit_pct` | 怨쇨굅 LOT 湲곕줉/濡쒓렇 ?명솚 | ?ㅼ젣 SELL ?먮떒? ?꾩옱 OPEN LOT ??湲곕컲 `current_base_target_profit_rate` ?곗꽑 |
| `positions` | `exit_anchor_price` | 湲곗〈 DB row/fallback/log ?명솚 | ?ㅼ젣 reentry??`normal_exit_anchor_price`, `trailing_exit_anchor_price` ?ъ슜 |
| `src/kis_msj/ui_server.py` | `loadExecution()` | raw execution mapping ?대? 吏꾨떒??| ?쇰컲 nav ??? ?쒓굅???곹깭 |

?쇰컲 UI Config?먯꽌 ?④릿 ??ぉ:

- `strategy.initial_buy_amount`
- `strategy.auto_buy_limit`
- `strategy.absolute_max_investment`
- `strategy.exposure_buy_bands`
- `strategy.exposure_sell_bands`
- `strategy.reentry_drop_rate`

??媛믩뱾? JSON raw view??湲곗〈 config/DB ?명솚?먮뒗 ?⑥븘 ?덉쓣 ???덉?留? ?쇰컲 ?댁슜?먭? 議곗젙?댁빞 ?섎뒗 ?꾩옱 湲곕낯 ?꾨왂 ?뚮씪誘명꽣濡쒕뒗 ?몄텧?섏? ?딅뒗??

## 5. 臾몄꽌 ?섏젙 ??ぉ

| ?뚯씪 | ?섏젙/?뺤씤 ?댁슜 |
| --- | --- |
| `docs/project_handoff_full.md` | ??媛먯궗 蹂닿퀬??留곹겕 異붽? |
| `docs/project_handoff_full.md`, `summary`, `thread_prompt`, `new_season_reset.md`, `local_ui.md` | `155 passed`, strict KIS snapshot policy, manual request ?ㅻ챸 理쒖떊???뺤씤 |
| `docs/new_season_reset.md` | pending order/manual request status, generated_at/sellable_quantity strict policy 理쒖떊???뺤씤 |
| `docs/local_ui.md` | KIS 吏곸젒 二쇰Ц API ?놁쓬怨?manual request ?앹꽦 API???덉쓬??援щ텇 ?뺤씤 |

異붽? ?뺤씤 寃곌낵:

- docs 留곹겕 ?먭?: 源⑥쭊 ?곷? 留곹겕 0媛?
- `project_*`, `local_ui`, `strategy_lot_sizing`, `new_season_reset`, `expansion_100_config` 紐⑤몢 authoritative source 臾멸뎄? 理쒖떊 ?뚯뒪??湲곗? ?쒓린 議댁옱

## 6. UI ?섏젙 ??ぉ

| ?뚯씪 | ?꾩튂 | 臾몄젣 | 議곗튂 |
| --- | --- | --- | --- |
| `src/kis_msj/ui_service.py` | `CONFIG_METADATA`, `DETAILED_CONFIG_DESCRIPTIONS` | legacy exposure/auto limit ?ㅻ챸???꾩옱 LOT sizing怨??쇰룞 媛??| ?덇굅???명솚?⑹쑝濡?紐낇솗???섏젙 |
| `src/kis_msj/ui_service.py` | `_new_season_wizard_steps()` | 諛깆뾽 ?④퀎媛 ?쏹I ?ㅽ뻾 踰꾪듉 ?놁쓬?앹씠?쇨퀬 ?쒖떆 | UI 踰꾪듉/CLI 紐⑤몢 ?덈궡?섎룄濡??섏젙 |
| `src/kis_msj/ui_server.py` | New Season ?붾㈃ | ?대? flag媛 湲곕낯 ?붾㈃???몄텧 | `details` ?덉쓽 怨좉툒 吏꾨떒媛믪쑝濡??묒쓬 |
| `src/kis_msj/ui_server.py`, `ui_service.py` | KIS snapshot 寃利?| plan ?앹꽦 ??snapshot ?ㅻ쪟瑜??댄빐?섍린 ?대젮? | snapshot 寃利?API/踰꾪듉 異붽?. preview 媛??request 媛?? generated_at age, sellable ?꾨씫, DB/KIS ?섎웾 mismatch瑜?遺꾨━ ?쒖떆 |

## 6-1. manual_order_requests 以묐났 ?뚮퉬 諛⑹? 蹂닿컯

| ?먭? ??ぉ | 寃곌낵 |
| --- | --- |
| REQUESTED 以묐났 泥섎━ | `claim_manual_order_request()`媛 `WHERE request_id=? AND status='REQUESTED' AND linked_order_id=''` 議곌굔?쇰줈 ?먯옄??claim |
| claim ???곹깭 | claim ?깃났 ??`PROCESSING` |
| ?대? claim/linked??request | claim ?ㅽ뙣, 泥섎━ skip |
| submit ?깃났 | `SUBMITTED` + `linked_order_id` ???|
| fill 諛쒖깮 | fill insert ?깃났 ??湲곗〈 `position_manager.apply_fill()` 寃쎈줈濡?諛섏쁺, ?댄썑 request `FILLED` |
| block/fail | `BLOCKED` ?먮뒗 `FAILED`濡?紐낇솗???꾪솚 |
| bot ?ъ떆??蹂듭닔 ?꾨줈?몄뒪 | 媛숈? DB row瑜??숈떆??蹂대뜑?쇰룄 claim? ?섎굹留??깃났. ?? ?댁쁺 ?꾩젣???ъ쟾???⑥씪 Bot Core ?꾨줈?몄뒪 沅뚯옣 |

?⑥? 痍⑥빟??

- `PROCESSING` ?곹깭?먯꽌 ?꾨줈?몄뒪媛 鍮꾩젙??醫낅즺?섎㈃ ?먮룞 retry?섏? ?딄퀬 reset guard??嫄몃┛?? 以묐났 二쇰Ц 諛⑹? 愿?먯뿉?쒕뒗 ?덉쟾?섏?留? ?댁쁺?먭? ?곹깭瑜?蹂닿퀬 ?섎룞 泥섎━?댁빞 ?쒕떎.
- ?ъ떆???뺤콉? ?꾩쭅 ?녿떎. ?꾩슂?섎㈃ `retry_count`, `claimed_at`, `max_retry`瑜?異붽??쒕떎.

## 6-2. KIS balance snapshot validator UI 蹂닿컯

異붽? API:

- `POST /api/new-season/validate-snapshot`

?묐떟 ?듭떖 ?꾨뱶:

- `snapshot_valid_for_preview`
- `snapshot_valid_for_request`
- `snapshot_warnings`
- `snapshot_errors`
- `snapshot_generated_at`
- `snapshot_age_minutes`
- `missing_required_fields`
- `matched_positions_count`
- `mismatched_positions_count`
- `missing_in_snapshot_codes`
- `extra_in_snapshot_codes`
- `request_creation_allowed`
- `request_creation_block_reason`
- `guide`

UI ?쒖떆:

- ?쒖쟾?됰ℓ???덉젙??誘몃━蹂닿린 媛??遺덇???
- ?쒖쟾?됰ℓ???붿껌 ?앹꽦 媛??遺덇???
- generated_at怨?snapshot age
- sellable_quantity ?꾨씫, stale, mismatch 媛숈? ?ㅻ쪟? ?ㅼ쓬 ?됰룞

以묒슂 ?뺤콉:

- preview?먯꽌 warning?댁뼱??request ?앹꽦? 李⑤떒?????덈떎.
- ?ㅼ젣 request ?앹꽦?먮뒗 理쒖떊 `generated_at`怨??ㅼ젣 `sellable_quantity`媛 ?꾩닔??

## 7. ?뚯뒪???섏젙 ??ぉ

?뚯뒪???뚯씪? ?대쾲 媛먯궗?먯꽌 吏곸젒 ?섏젙?섏? ?딆븯?? 寃??寃곌낵 ?ㅻ옒???대쫫泥섎읆 蹂댁씠???뚯뒪??以??쇰????꾩옱??紐낇솗??legacy/backward compatibility 紐⑹쟻???덈떎.

| ?뚯뒪??| ?먮떒 |
| --- | --- |
| `test_legacy_mode_keeps_exposure_based_target_profit_behavior` | legacy mode 蹂댁〈 寃利앹쑝濡??좎? ?꾩슂 |
| `test_ui_service.py` legacy metadata check | Updated to assert legacy keys are absent from the general Config schema. |
| snapshot strict validation ?뚯뒪??| 理쒖떊 ?뺤콉 寃利앹쑝濡??좎? ?꾩슂 |

沅뚯옣 ?꾩냽:

- legacy 愿???뚯뒪???대쫫?먮뒗 怨꾩냽 `legacy`/`compat`瑜?紐낆떆?쒕떎.
- UI metadata ?ㅻ챸 臾몄옄??議댁옱 ?뚯뒪?몃뒗 ?덈Т 臾멸뎄 怨좎젙??媛뺥빐吏吏 ?딄쾶 ?듭떖 keyword 以묒떖?쇰줈 ?좎??쒕떎.

## 8. ?뺤씤 ?꾩슂 ??ぉ

| ??ぉ | ?꾩옱 ?먮떒 | 沅뚯옣 |
| --- | --- | --- |
| KIS balance snapshot ?먮룞 ?앹꽦 | `prepare_new_season.py`?먮뒗 ?먮룞 ?앹꽦 湲곕뒫 ?놁쓬. JSON ?뚯씪 ?낅젰/寃利?援ъ“ | ?댁쁺?먭? snapshot JSON??以鍮꾪븯???덉감瑜?UI?먯꽌 ???쎄쾶 留뚮뱾吏 寃??|
| `config/lot_auto_trader.example.json` 踰붿쐞 | ?꾩옱??expansion-safe 援ъ“ + 泥?10醫낅ぉ ?덉떆 | 蹂꾨룄 `config/lot_auto_trader.expansion_100.example.json`濡?100醫낅ぉ ?꾩껜 ?덉떆瑜??섏? 寃??|
| `/api/execution-mapping/status` ?몄텧 | nav ??? ?쒓굅?먯?留?API/function ?좎? | ?꾩슂 ?녿떎怨??뺤젙?섎㈃ deprecated 二쇱꽍 ???ㅼ쓬 ?뺣━ ???쒓굅 媛??|
| `cleanup_enabled=false` ?κ린 ?댁슜 | 珥덇린 ?덉젙?붿뿉???곸젅 | 濡쒓렇 ?덉젙????cleanup??耳ㅼ? 蹂꾨룄 寃??|
| UI ???쒖쫵 wizard ?⑥닚??| ?대? flag ?몄텧? 以꾩?吏留?湲곕뒫??留롮쓬 | ?ъ슜?먭? 怨꾩냽 ?룰컝由щ㈃ single-action wizard瑜???媛뺥븯寃??먮룞??|

## 9. ?꾩옱 ?⑥? 由ъ뒪??

| 由ъ뒪??| ?깃툒 | ?꾩옱 諛⑹뼱?μ튂 | ?⑥? 痍⑥빟??| 沅뚯옣 議곗튂 |
| --- | --- | --- | --- | --- |
| 二쇰Ц/泥닿껐 ?숆린??| 以묎컙 | open order 湲곗? reconciliation, startup recent reconciliation, unmatched ignore | ?ㅼ젣 KIS raw 泥닿껐 row 蹂???꾨씫 媛?μ꽦 | 泥??ㅼ껜寃???raw mapping ?ы솗??|
| fill dedupe | ??쓬~以묎컙 | execution_id ?곗꽑, fallback key, duplicate count | KIS媛 execution_id ?놁씠 泥닿껐?쒓컖 ?덉쭏????쑝硫?fallback ?쒓퀎 | execution_id ?ㅼ젣 ?쒓났 ?щ? 吏???뺤씤 |
| partial fill | 以묎컙 | PARTIAL order status, remaining_quantity 湲곗? LOT 諛섏쁺 | ?μ떆媛?PARTIAL/order timeout ?댁쁺 ?먮떒 ?꾩슂 | open order UI 紐⑤땲?곕쭅 媛뺥솕 |
| manual order 以묐났 ?뚮퉬 | ??쓬~以묎컙 | ?먯옄??`REQUESTED -> PROCESSING` claim, linked_order_id ?ъ쿂由?李⑤떒, stale PROCESSING UI/API requeue/cancel, pending status reset guard | PROCESSING 以??꾨줈?몄뒪 鍮꾩젙??醫낅즺 ???먮룞 ?ъ떆?꾨뒗 ?섏? ?딄퀬 ?댁쁺???뺤씤 ?꾩슂 | ?⑥씪 Bot Core ?꾨줈?몄뒪 ?댁쁺, ?꾩슂 ?????꾧꺽??retry policy 異붽? |
| DB reset/archive/liquidation | ?믪쓬 | confirm text, pending order/request/open lot/sync guard, KIS snapshot strict validation | snapshot ?뚯씪???댁쁺?먭? ?섎せ 留뚮뱾 ???덉쓬 | snapshot ?앹꽦 ?꾧뎄 ?먮뒗 import UI 異붽? 寃??|
| KIS snapshot stale/mismatch | 以묎컙~?믪쓬 | generated_at/sellable strict mode, max age, DB hash, plan freshness guard, UI validator | ?먮룞 snapshot ?앹꽦???놁뼱 ?섎룞 ?ㅻ쪟 媛??| snapshot ?앹꽦 ?꾧뎄 ?먮뒗 ?뚯씪 import UX 異붽? 寃??|
| UI 踰꾪듉 ?ㅼ“??| 以묎컙 | live warning, confirm, disabled guide, no direct KIS order API | 留롮? 踰꾪듉???덉뼱 珥덈낫???쇰룞 媛??| wizard UX 吏???⑥닚??|
| config ???寃利?| 以묎컙 | backup, atomic save, validation, history | 紐⑤뱺 config ?섎?瑜?schema媛 ?꾨꼍??寃利앺븯吏???딆쓬 | schema validation ?뺣? |
| runtime pause 諛섏쁺 | 以묎컙 | runtime_control.json, main loop guard | 湲??묒뾽 以?利됱떆 interrupt ?쒓퀎 媛??| loop ??泥댄겕?ъ씤???뺣? 寃??|
| live_trading ?꾪솚 | ?믪쓬 | UI 寃쎄퀬, config confirm, risk guards | ?ъ슜?먭? ?洹쒕え ?꾨낫援곗쑝濡?耳??꾪뿕 | ?뚯븸/paper 寃利????④퀎 ?꾪솚 |
| raw execution log | 以묎컙 | 湲곕낯 留덉뒪?? UI masking | raw log ?κ린 ?쒖꽦????濡쒓렇 怨쇰떎/誘쇨컧?뺣낫 由ъ뒪??| ?뺤씤 ??`enable_execution_raw_log=false` |
| 100醫낅ぉ ?뺤옣 | 以묎컙 | `max_new_buy_per_day=10`, `max_new_buy_amount_per_day=2M`, total limits | 怨좉? LOT ?꾨낫媛 ?욎씠硫??섎（ ?몄텧 蹂????| daily amount limit 濡쒓렇 ?뺤씤 |
| REVIEW/SYNC/RISK guard | ??쓬~以묎컙 | ?곹깭蹂?BUY/SELL 李⑤떒 ?뚯뒪??| ?섎룞留ㅻ룄 ??sync ???곹깭 ?쇰룞 | Review ??recheck/reconciliation ?덈궡 ?ъ슜 |
| cleanup disabled | ??쓬 | `cleanup_enabled=false` | ?ㅻ옒???먯떎 LOT 異뺤쟻 媛??| ?덉젙????cleanup policy ?ш???|

## 10. ?ㅼ쓬 沅뚯옣 ?묒뾽

1. KIS balance snapshot JSON???щ엺?????ㅼ닔?섍쾶 留뚮뱶??import/validator UI瑜?媛뺥솕?쒕떎.
2. ???쒖쫵 wizard?먯꽌 ?쒕떎???덉쟾 ?④퀎 ?섎굹留??ㅽ뻾??踰꾪듉????媛뺥븯寃??⑥닚?뷀븳??
3. 泥??ㅼ껜寃??댄썑 raw execution mapping 寃곌낵瑜??ㅼ떆 蹂닿퀬 `enable_execution_raw_log=false`濡??섎룎由곕떎.
4. `config/lot_auto_trader.example.json`怨?蹂꾨룄濡?full 100-stock example/profile ?뚯씪???섏? 寃곗젙?쒕떎.
5. `legacy_exposure_bands`瑜??κ린?곸쑝濡?怨꾩냽 ?좎??좎?, 紐낇솗??deprecation timeline???뺥븳??

## 11. ?ㅽ뻾???뺤쟻 ?먭? 寃곌낵

| ?먭? | 寃곌낵 |
| --- | --- |
| Python AST parse: `src`, `scripts`, `tests` | parse error 0 |
| UI route inventory | `/api/status`, `/api/stocks`, `/api/lots`, `/api/orders`, `/api/fills`, `/api/manual-order-requests`, `/api/manual-orders/preview`, `/api/manual-orders`, review API, new-season API ???ㅼ젣 route ?뺤씤 |
| CLI help | `scripts/prepare_new_season.py --help` 湲곗? ?듭뀡 ?뺤씤: `--config`, `--archive-root`, `--profile`, `--apply-config`, `--archive`, `--liquidation-plan`, `--create-liquidation-requests`, `--kis-balance-json`, `--liquidation-plan-file`, `--plan-max-age-minutes`, `--reset-db`, `--confirm`, `--dry-run`, `--execute` |
| docs link check | missing relative links 0 |
| KOSPI 100 config count | stocks 100, enabled 97, manual_only 3 |
| reset pending statuses | orders: `REQUESTED`, `PARTIAL`, `SUBMITTED`, `ACCEPTED`, `PENDING`, `OPEN`, `NEW`; manual: `REQUESTED`, `PROCESSING`, `ACCEPTED`, `SUBMITTED`, `PENDING`, `OPEN`, `NEW`, `CREATED`, `RETRYING` |

## 12. ?ㅽ뻾???뚯뒪??寃곌낵

?대쾲 蹂닿퀬???묒꽦 ?쒖젏???꾨옒 ?꾩껜 ?뚯뒪?몃? ?ㅽ뻾?덈떎.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_audit_cleanup_check
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

寃곌낵:

| 紐낅졊 | 寃곌낵 |
| --- | --- |
| `.\\.venv\\Scripts\\python.exe -m pytest -q --basetemp .pytest_tmp_audit_cleanup_check` | `155 passed`, pytest cache warning 1媛?|
| `.\\.venv\\Scripts\\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check` | `155 passed`, pytest cache warning 1媛?|

warning? `.pytest_cache` cache write 愿??`PytestCacheWarning`?대ŉ 湲곕뒫 ?ㅽ뙣???꾨땲??

## 13. 2026-05-27 Final Addendum

This addendum records the final cleanup/hardening pass after the initial audit.

### Actual cleanup completed

- Removed legacy Strategy keys from the general UI Config metadata/form-table surface:
  - `strategy.initial_buy_amount`
  - `strategy.auto_buy_limit`
  - `strategy.absolute_max_investment`
  - `strategy.exposure_buy_bands`
  - `strategy.exposure_sell_bands`
  - `strategy.reentry_drop_rate`
- Kept the underlying config/model/storage fields only for DB compatibility, historical row parsing, fallback, and explicit legacy-mode tests.
- Updated UI tests so these legacy keys are asserted absent from the general Config schema.
- Updated `docs/local_ui.md` so current table-style config editing is described around `price_lot_bands`, `add_buy_lot_bands`, and `target_profit_lot_bands`, not legacy exposure bands.

### Manual order duplicate-consumption hardening

- Added `StateStore.claim_manual_order_request(request_id)`.
- The claim uses a conditional DB update from `REQUESTED` to `PROCESSING` only when `linked_order_id` is empty.
- `AutoTrader.process_manual_order_requests()` now processes only successfully claimed rows.
- Already claimed, linked, or non-REQUESTED rows are skipped.
- If runtime interrupt occurs after claim but before submit, the request is moved to `BLOCKED` to avoid a stuck PROCESSING row.
- Stale PROCESSING visibility was added: `processing_started_at`, `processing_claimed_by`, `claim_attempt_count`, `last_processing_error`, and `stale_processing_reason`.
- If `PROCESSING` is old and `linked_order_id` is empty, UI/API can safely requeue it to `REQUESTED` or cancel it to `BLOCKED` only after operator confirm text.
- Recovery is blocked when the row is not stale, already has `linked_order_id`, the same symbol/LOT has an open order or another pending manual request, the symbol is `SYNC_REQUIRED`/`RISK_BLOCKED`, or the SELL LOT is no longer OPEN.
- Recovery audit logs are `manual_order_request_requeued` and `manual_order_request_blocked_by_operator`; they include `previous_status`, `previous_processing_started_at`, `claim_attempt_count`, `operator_note`, and `reason`.
- If `linked_order_id` exists, requeue/cancel is blocked because a real order may already exist.
- Remaining operational assumption: run a single Bot Core process. The claim reduces duplicate risk if two consumers race. Unexpected crash recovery is operator-controlled, not automatic.

Claim flow:

1. `UPDATE manual_order_requests SET status='PROCESSING' ... WHERE request_id=? AND status='REQUESTED' AND COALESCE(linked_order_id,'')=''`
2. `rowcount == 1` means claim success.
3. The claimed row is read back and passed to the existing order manager path.
4. Submit success records `linked_order_id` and `SUBMITTED`.
5. Guard block records `BLOCKED`.
6. Exception records `FAILED` with `last_processing_error`.

### KIS balance snapshot validator UI/API

- Added `POST /api/new-season/validate-snapshot`.
- Added New Season UI button `snapshot 寃利?.
- Validator returns:
  - `snapshot_valid_for_preview`
  - `snapshot_valid_for_request`
  - `snapshot_warnings`
  - `snapshot_errors`
  - `snapshot_generated_at`
  - `snapshot_age_minutes`
  - `missing_required_fields`
  - `matched_positions_count`
  - `mismatched_positions_count`
  - `missing_in_snapshot_codes`
  - `extra_in_snapshot_codes`
  - `request_creation_allowed`
  - `request_creation_block_reason`
- Preview and request creation remain intentionally different:
  - Preview may show warnings and fallback values.
  - Request creation requires strict validation, including fresh `generated_at` and real `sellable_quantity`.

### Final keyword inventory summary

| Keyword | Final status |
| --- | --- |
| `initial_buy_amount` | Removed from general UI Config metadata. Still present in live config/model/legacy fallback and tests. |
| `add_buy_amount` | No active current-flow usage found. |
| `auto_buy_limit` | Removed from general UI Config metadata. Still present in DB/model/non-cycle fallback and review compatibility. |
| `absolute_max_investment` | Removed from general UI Config metadata. Still present in DB/model/non-cycle fallback. |
| `exposure_buy_bands` / `exposure_sell_bands` | Removed from general UI Config metadata/form-table surface. Still present in config/model/legacy mode validation. |
| `legacy_exposure_bands` | Kept for explicit backward-compatible mode and tests. |
| `target_profit_pct` / `base_target_profit_rate` | Kept for historical LOT rows and display/log compatibility. Actual SELL logic uses current OPEN LOT count based target bands. |
| `exit_anchor_price` | Kept as DB compatibility/fallback/log field. Actual reentry uses `normal_exit_anchor_price` and `trailing_exit_anchor_price` first. |
| `reentry_drop_rate` | Removed from general UI Config metadata. Kept only as legacy config compatibility. |
| `loadExecution` | Kept as internal raw execution diagnostic helper/API; no normal nav tab. |
| `request_creation_possible` | Kept as API/internal diagnostic value; folded under advanced diagnostics in the UI. |
| `dry-run` | Still intentionally present in CLI/UI/docs as the safe preview mode. |

### Final risk update

| Risk | Updated grade | Notes |
| --- | --- | --- |
| manual order duplicate consumption | Low | Atomic claim and operator-controlled stale PROCESSING requeue/cancel added. Requeue/block requires stale age, no linked order, no related pending order/request, and confirm text. Automatic retry remains intentionally disabled. |
| KIS snapshot stale/mismatch | Medium | Validator UI/API added. Residual risk is operator-created snapshot quality because automatic KIS balance snapshot generation is not implemented. |
| legacy UI confusion | Low | General Config UI no longer exposes major legacy strategy keys. |

### Legacy operating config cleanup

The live operating config and example config no longer carry these direct `strategy` keys:

- `initial_buy_amount`
- `add_buy_amount`
- `auto_buy_limit`
- `absolute_max_investment`
- `exposure_buy_bands`
- `exposure_sell_bands`
- `reentry_drop_rate`
- `target_profit_pct`
- `target_profit_rate`

`config.py` still provides backward-compatible defaults for old configs and DB rows. The current operating config uses `price_lot_bands`, `add_buy_lot_bands`, `target_profit_lot_bands`, and `max_lots_per_symbol_default`.

### Final tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_legacy_removal_check
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_manual_snapshot_check
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

Result: all three commands completed with `155 passed` and one pytest cache warning. The warning is a `.pytest_cache` write warning, not a functional failure.

No real trade, KIS order API call, or DB reset was executed during this audit/cleanup pass.

