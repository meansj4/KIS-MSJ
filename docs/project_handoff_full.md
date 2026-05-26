# KIS LOT ?먮룞嫄곕옒 遊??꾩껜 ?몄닔?멸퀎 臾몄꽌

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `155 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


愿??臾몄꽌:

- [?꾩껜 ?몄닔?멸퀎](project_handoff_full.md)
- [?붿빟蹂?(project_handoff_summary.md)
- [??thread 泥?硫붿떆吏???꾨＼?꾪듃](project_handoff_thread_prompt.md)
- [濡쒖뺄 UI 臾몄꽌](local_ui.md)
- [LOT sizing ?꾨왂](strategy_lot_sizing.md)
- [???쒖쫵 reset](new_season_reset.md)
- [100醫낅ぉ ?뺤옣 config](expansion_100_config.md)
- [?꾩껜 ?꾨줈?앺듃 媛먯궗/?뺣━ 蹂닿퀬??(project_audit_cleanup_report.md)

理쒖떊 ?꾩껜 ?뚯뒪/臾몄꽌 媛먯궗 寃곌낵??[docs/project_audit_cleanup_report.md](project_audit_cleanup_report.md)瑜?李멸퀬?쒕떎. 2026-05-27 異붽? 蹂닿컯 湲곗??쇰줈 ?쇰컲 UI Config?먯꽌??legacy exposure/initial amount ??ぉ???④꼈怨? manual order request???먯옄??claim ??泥섎━?섎ŉ, New Season ?붾㈃?먮뒗 KIS balance snapshot validator媛 異붽??섏뿀??

Last updated: 2026-05-26  
湲곗? ?뚯뒪??寃곌낵: `155 passed` (`.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check`)  
湲곗? config profile: `expansion_100_safe`  
二쇱쓽: ?ㅼ젣 ?댁쁺媛믪? ?ㅽ뻾 ?쒖젏??`config/lot_auto_trader.json`, SQLite DB, logs, KIS 怨꾩쥖 ?곹깭瑜??ㅼ떆 ?뺤씤?댁빞 ?쒕떎.

?????μ냼: `C:\MSJ\KIS-MSJ`  
二쇱슂 config: `config/lot_auto_trader.json`  
?묒꽦 紐⑹쟻: ??ChatGPT 梨꾪똿諛? ??Codex thread, ??媛쒕컻 ?몄뀡?먯꽌 ??臾몄꽌留?蹂닿퀬 ?꾩옱 援ы쁽 ?곹깭? ?댁쁺 ?먯튃???댁뼱諛쏄린 ?꾪븳 ?몄닔?멸퀎

## 0. ?꾩옱 ?곹깭 ?ㅻ깄??

| ??ぉ | ?꾩옱媛?|
| --- | --- |
| 臾몄꽌 ?묒꽦/媛깆떊 ?쒓컖 | 2026-05-26 |
| ??μ냼 寃쎈줈 | `C:\MSJ\KIS-MSJ` |
| ?꾩옱 config ?뚯씪 | `config/lot_auto_trader.json` |
| ?꾩옱 risk profile | `expansion_100_safe` |
| KOSPI ?꾨낫 ??/ enabled ??/ manual_only ??| 100 / 97 / 3 |
| `order.live_trading` | false |
| `strategy.cleanup_enabled` | false |
| `ui_manual_trading_enabled` | false |
| `order.enable_execution_raw_log` | true |
| 理쒖떊 ?뚯뒪??寃곌낵 | `155 passed`, pytest cache warning 1媛쒕뒗 湲곕뒫 ?ㅽ뙣 ?꾨떂 |
| ?꾩옱 OPEN LOT ??| 19媛쒕줈 ?뺤씤??|
| ?꾩옱 reset 媛???щ? | 遺덇?. OPEN LOT???⑥븘 ?덉쑝誘濡?李⑤떒?섎뒗 寃껋씠 ?뺤긽 |
| ?꾩옱 liquidation plan 議댁옱 ?щ? | ?ㅽ뻾 ?쒖젏 `exports/liquidation_plan_*.json` 諛?UI `New Season` ??뿉???ы솗???꾩슂 |
| ?꾩옱 KIS balance snapshot 議댁옱 ?щ? | ?ㅽ뻾 ?쒖젏 ?ъ슜?먭? 以鍮??좏깮?댁빞 ?? ?놁쑝硫??꾨웾留ㅻ룄 request ?앹꽦 李⑤떒 |
| ?꾩옱 archive 寃쎈줈 | `archive/reset_YYYYMMDD_HHMMSS/...` ?뺤떇. ?ㅼ젣 理쒖떊 archive???대뜑?먯꽌 ?ы솗??|
| ?ㅼ쓬 1?쒖쐞 ?묒뾽 | KIS balance snapshot 以鍮?-> liquidation plan ?앹꽦 -> ?꾨웾留ㅻ룄 request ?앹꽦 ?щ? 寃곗젙 |

?꾩옱??`expansion_100_safe` config媛 ?곸슜?섏뼱 ?덉쑝??OPEN LOT???⑥븘 ?덉쑝誘濡?DB reset? 李⑤떒?섎뒗 寃껋씠 ?뺤긽?대떎. ?ㅼ쓬 ?④퀎???ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤 ?먮즺??KIS balance snapshot 以鍮? ?꾨웾留ㅻ룄 ?덉젙???앹꽦, ?꾨웾留ㅻ룄 request ?앹꽦 ?щ? 寃곗젙?대떎.

## 1. ?꾩껜 紐⑹쟻怨???以??붿빟

???꾨줈?앺듃??KIS API 湲곕컲 KOSPI LOT ?⑥쐞 ?먮룞留ㅻℓ 遊뉗씠?? ?ъ슜?먮뒗 ?щ윭 KOSPI ?곕웾 ?꾨낫援곗쓣 ?뚯븸 LOT ?⑥쐞濡?遺꾩궛 留ㅼ닔?섍퀬, ?됯퇏?④? ?섎굹媛 ?꾨땲??媛쒕퀎 LOT??留ㅼ닔媛, ?붿뿬?섎웾, ?섏씠, ?먯씡瑜? 紐⑺몴?섏씡瑜좎쓣 湲곗??쇰줈 留ㅼ닔/留ㅻ룄/?ъ쭊???먯떎?뺣━/?섎룞寃?좊? 愿由ы븯?ㅺ퀬 ?쒕떎.

??以??붿빟:

> ??遊뉗? 二쇰Ц ?붿껌???꾨땲???ㅼ젣 泥닿껐 fill??以묐났 ?놁씠 ??λ맂 ?ㅼ뿉留?`lots`? `positions`瑜?媛깆떊?섎뒗 LOT 湲곕컲 ?먮룞嫄곕옒 ?쒖뒪?쒖씠硫? UI??愿???쒖뼱/?섎룞 ?붿껌 ???앹꽦留??대떦?섍퀬 KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딅뒗??

?듭떖 泥좏븰:

- ?섏씡瑜?洹밸??붾낫???댁쁺 ?덉젙?? 泥닿껐 ?숆린?? 怨쇰떎 吏꾩엯 諛⑹?, ?곹깭 異붿쟻?깆쓣 ?곗꽑?쒕떎.
- 紐⑤뱺 留ㅼ닔/留ㅻ룄 ?먮떒? LOT ?⑥쐞濡?異붿쟻?쒕떎.
- ?먯떎 LOT? 臾댁“嫄??먯젅?섏? ?딆?留? ?ㅻ옒???먯떎 LOT??臾댄븳 諛⑹튂?섏? ?딅룄濡?STALE_LOT, CLEANUP_SELL, REVIEW_REQUIRED濡?愿由ы븳??
- ?꾨웾 PROFIT_TAKE ?댄썑?먮뒗 諛붾줈 initial_buy?섏? ?딄퀬 WAIT_REENTRY?먯꽌 蹂꾨룄 ?ъ쭊??議곌굔??蹂몃떎.
- 湲곗〈 ?댁쁺 ?곗씠?곕뒗 ??젣?섏? ?딄퀬 archive ?????쒖쫵???쒖옉?쒕떎.
- ???쒖쫵 reset? ?ㅼ젣 怨꾩쥖/KIS snapshot, DB, manual request, open order媛 紐⑤몢 ?덉쟾???곹깭???뚮쭔 媛?ν븯??

?꾩옱 理쒖쥌 ?곹깭:

| ??ぉ | ?꾩옱媛?|
| --- | --- |
| risk profile | `expansion_100_safe` |
| ?꾨낫 醫낅ぉ ??| 100 |
| enabled 醫낅ぉ ??| 97 |
| disabled/manual_only 醫낅ぉ | 3 |
| `max_active_symbols` | 100 |
| `max_total_invested_amount` | 20,000,000 |
| `max_new_buy_per_day` | 10 |
| `max_new_buy_amount_per_day` | 2,000,000 |
| `max_total_open_lots` | 300 |
| `lot_sizing_mode` | `cycle_locked_by_entry_price` |
| `cleanup_enabled` | false |
| `ui_manual_trading_enabled` | false |
| `live_trading` | false |
| `enable_execution_raw_log` | true |
| ?꾩옱 二쇱쓽 | OPEN LOT???⑥븘 ?덉쑝硫?DB reset 李⑤떒???뺤긽 |

?꾩쭅 ?ㅼ젣 ?댁슜 ???⑥? 寃利?

- 理쒖떊 KIS balance snapshot 以鍮?
- liquidation plan, 利??꾨웾留ㅻ룄 ?덉젙???앹꽦
- ?꾨웾留ㅻ룄 manual SELL request ?앹꽦 ?щ? 寃곗젙
- Bot Core瑜??듯븳 留ㅻ룄 泥섎━? reconciliation ?꾨즺
- OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 ?뺤씤
- DB reset
- ???쒖쫵 ?쒖옉 ??泥??ㅼ껜寃?raw execution field mapping 理쒖쥌 ?뺤씤

## 2. ?덈? 源⑤㈃ ???섎뒗 ?듭떖 ?먯튃

| ?먯튃 | ?ㅻ챸 | 源⑥죱?????꾪뿕 |
| --- | --- | --- |
| 二쇰Ц ?붿껌留뚯쑝濡?lots/positions瑜?諛붽씀吏 ?딅뒗??| `orders`??二쇰Ц ?섎룄? 二쇰Ц ?곹깭瑜?湲곕줉??肉먯씠?? | 二쇰Ц 嫄곗젅, 痍⑥냼, 遺遺꾩껜寃???DB? ?ㅼ젣 怨꾩쥖媛 ?닿툔?쒕떎. |
| fills insert ?깃났 ?꾩뿉留?lots/positions瑜?媛깆떊?쒕떎 | `store.record_fill(fill)`??true???좉퇋 泥닿껐留?`position_manager.apply_fill()`濡?媛꾨떎. | 以묐났 泥닿껐??LOT/position??以묐났 諛섏쁺?쒕떎. |
| duplicate fill ?먮뒗 `record_fill_failed`??apply_fill 湲덉? | `order_manager._record_filled()`??record_fill 諛섑솚媛믪쓣 ?뺤씤?쒕떎. | 媛숈? 泥닿껐???щ윭 LOT ?먮뒗 position??諛섏쁺?쒕떎. |
| UI ?쒕쾭??KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딅뒗??| UI???곹깭 議고쉶, ?ㅼ젙 ??? runtime ?쒖뼱, manual request ?앹꽦留??쒕떎. | 愿??UI 議곗옉??怨??ㅺ굅??二쇰Ц???섎뒗 ?꾪뿕???앷릿?? |
| ?섎룞 二쇰Ц??UI媛 吏곸젒 二쇰Ц?섏? ?딅뒗??| UI??`manual_order_requests` ?먮쭔 ?앹꽦?섍퀬 Bot Core媛 ?뚮퉬?쒕떎. | ?먮룞/?섎룞 二쇰Ц ?덉쟾?μ튂媛 媛덈씪吏꾨떎. |
| Bot Core留?湲곗〈 order_manager 寃쎈줈濡?二쇰Ц?쒕떎 | runtime pause, risk guard, open order guard, live trading guard瑜??듦낵?댁빞 ?쒕떎. | ?섎룞 二쇰Ц???먮룞 二쇰Ц蹂대떎 ???꾪뿕???고쉶濡쒓? ?쒕떎. |
| DB reset? ?덉쟾 議곌굔 異⑹” ?꾨쭔 媛??| OPEN LOT 0, 吏꾪뻾 以?order 0, pending manual request 0, sync mismatch ?놁쓬. | ?ㅼ젣 蹂댁쑀? DB媛 ?곴뎄???닿툔?쒕떎. |
| 湲곗〈 DB/log/config??archive/backup ???쒖옉?쒕떎 | ???쒖쫵 ???댁쟾 ?쒖쫵 ?먮즺瑜?蹂댁〈?쒕떎. | 臾몄젣 遺꾩꽍怨?蹂듦뎄媛 遺덇??ν빐吏꾨떎. |
| DB/KIS 遺덉씪移섎뒗 SYNC_REQUIRED濡?留됰뒗??| ?ㅼ젣 怨꾩쥖? ?대? LOT/position???ㅻⅤ硫??좉퇋 二쇰Ц 李⑤떒. | ?섎せ???섎웾?쇰줈 留ㅼ닔/留ㅻ룄?????덈떎. |
| REVIEW_REQUIRED??媛뺤젣 ?댁젣?섏? ?딅뒗??| recheck, acknowledge, ?섎룞留ㅻ룄, reconciliation ?먮쫫?쇰줈 泥섎━?쒕떎. | ?꾪뿕 ?곹깭?먯꽌 ?먮룞 BUY媛 ?ㅼ떆 ?대┫ ???덈떎. |
| cleanup/review/risk ?곹깭?먯꽌 BUY 李⑤떒 ?뺤콉 ?좎? | REVIEW_REQUIRED, RISK_BLOCKED, SYNC_REQUIRED, COOLDOWN? 蹂댁닔?곸쑝濡??숈옉?쒕떎. | ?먯떎/遺덉씪移??꾪뿕 ?곹깭?먯꽌 臾쇳?湲곌? ?댁뼱吏꾨떎. |

## 3. ?꾩껜 ?꾪궎?띿쿂

### Bot Core

| ?뚯씪 | 梨낆엫 | 以묒슂???먯튃 |
| --- | --- | --- |
| `src/kis_msj/main.py` | 遊?猷⑦봽, strategy decision ?ㅽ뻾, 二쇰Ц ??理쒖쥌 guard, reconciliation, manual request ?뚮퉬, runtime pause/config reload 諛섏쁺 | ?ㅼ젣 二쇰Ц ??runtime/risk/open-order/live/global guard瑜??ㅼ떆 ?뺤씤?쒕떎. |
| `src/kis_msj/strategy.py` | initial/add/reentry/sell ?꾨낫 ?앹꽦, reference price, lot sizing context, target profit ?숈쟻 怨꾩궛 | action ?꾨낫 ?앹꽦 ?④퀎?대ŉ ?ㅼ젣 二쇰Ц? main/order_manager 寃쎈줈濡?媛꾨떎. |
| `src/kis_msj/order_manager.py` | 二쇰Ц ?붿껌 湲곕줉, KIS 二쇰Ц ?곕룞, 泥닿껐 議고쉶/?뺢퇋?? fill record/dedupe logging | `record_fill()` ?ㅽ뙣 ??fill 諛섑솚 湲덉?. |
| `src/kis_msj/position_manager.py` | fill 湲곗??쇰줈 LOT/position ?곹깭 媛깆떊, ?곹깭 ?꾩씠, cycle anchor, lot sizing lock, review/stale ?됯? | fill ?놁씠???섎웾??諛붽씀吏 ?딅뒗?? |
| `src/kis_msj/risk_manager.py` | 怨꾩쥖/媛寃?醫낅ぉ ?꾪뿕 guard, ?꾩뿭 由ъ뒪??context | BUY 李⑤떒 以묒떖. SELL? ?꾩뿭 ?몄텧 ?쒗븳 ?뚮Ц??留됱? ?딅뒗?? |
| `src/kis_msj/lot_manager.py` | LOT 怨꾩궛, age decay, target profit, stale/cleanup ?꾨낫 怨꾩궛 蹂댁“ | OPEN LOT 湲곗?? `remaining_quantity > 0` and `status != CLOSED`. |
| `src/kis_msj/storage.py` | SQLite schema, CRUD, fill dedupe, migration column 蹂닿컯 | DB schema 蹂寃쎌? `_ensure_column`?쇰줈 backward compatible?섍쾶 泥섎━. |
| `src/kis_msj/config.py` | config dataclass? JSON load/parse | strategy/risk/order/stocks 援ъ“??湲곗?. |
| `src/kis_msj/models.py` | Position, Lot, Order, Fill, enum/status 紐⑤뜽 | ?곹깭媛믨낵 ?꾨뱶 ?섎?瑜?肄붾뱶 ?꾩껜?먯꽌 怨듭쑀. |
| `src/kis_msj/kis_client.py` | KIS 議고쉶/二쇰Ц API wrapper | UI?먯꽌 吏곸젒 ?몄텧 湲덉?. 二쇰Ц? Bot Core/order_manager 寃쎈줈留? |

### UI/API Layer

| ?뚯씪 | 梨낆엫 | 二쇱쓽 |
| --- | --- | --- |
| `src/kis_msj/ui_server.py` | localhost Web UI? HTTP API server. HTML/CSS/JS ?ы븿. | KIS 二쇰Ц API瑜??몄텧?섏? ?딅뒗?? |
| `src/kis_msj/ui_service.py` | UI ?곗씠??吏묎퀎, config validate/save/backup, runtime, manual preview/request, review, new season API service | DB 吏곸젒 ?섏젙? manual request/status/review ??愿由??곗씠?곕줈 ?쒗븳. lots/positions/fills 吏곸젒 ?섏젙 湲덉?. |
| `src/kis_msj/runtime_control.py` | `config/runtime_control.json` load/save? pause block reason 怨꾩궛 | runtime control? config蹂대떎 ?곗꽑 ?곸슜?쒕떎. |

### Scripts

| ?뚯씪 | 梨낆엫 | ?덉쟾 湲곕낯媛?|
| --- | --- | --- |
| `scripts/prepare_new_season.py` | archive, liquidation plan, liquidation manual SELL request ?앹꽦, reset dry-run/?ㅽ뻾 ?⑥닔 | 湲곕낯 dry-run. KIS 二쇰Ц API ?몄텧 ?놁쓬. reset/?꾨웾留ㅻ룄 request??confirm text ?꾩슂. |

### Config / Docs

| ?뚯씪 | ?댁슜 |
| --- | --- |
| `config/lot_auto_trader.json` | ?댁쁺 config. stocks, strategy, risk, order, market_hours, paths/account/upstream ?ы븿. |
| `config/runtime_control.json` | runtime pause/reload/start ?곹깭. UI? Bot Core媛 怨듭쑀?쒕떎. |
| `docs/local_ui.md` | UI ?ㅽ뻾, ?덉쟾 ?ㅺ퀎, ?쒖떆 洹쒖튃, ?섎룞 二쇰Ц, ???쒖쫵/由щ럭 ???ъ슜踰? |
| `docs/strategy_lot_sizing.md` | 媛寃⑸?蹂?LOT sizing, cycle lock, target profit lot bands ?ㅻ챸. |
| `docs/new_season_reset.md` | ???쒖쫵 以鍮? archive/reset/liquidation plan ?먮쫫. |
| `docs/expansion_100_config.md` | KOSPI 100 ?꾨낫援곌낵 expansion profile ?ㅻ챸. |

## 4. DB / 紐⑤뜽 援ъ“

### positions

`positions`??醫낅ぉ ?⑥쐞 ?곹깭瑜???ν븳?? `position_state`, 蹂댁쑀?섎웾, ?ъ옄湲? realized/unrealized 愿??媛? reentry anchor, review, lot sizing cycle lock ?꾨뱶媛 ?ㅼ뼱媛꾨떎.

| ?꾨뱶 | ?섎? | ?섏젙 二쇱껜 |
| --- | --- | --- |
| `code`, `name` | 醫낅ぉ ?앸퀎 | storage/position manager |
| `position_state` | NEVER_BOUGHT/HOLDING/WAIT_REENTRY/COOLDOWN_AFTER_CLEANUP/REVIEW_REQUIRED/RISK_BLOCKED/SYNC_REQUIRED | position_manager/review/reconciliation |
| `total_quantity`, `invested_amount` | fill 諛섏쁺 ???대? 蹂댁쑀 ?곹깭 | `position_manager.apply_fill` |
| `normal_exit_anchor_price` | NORMAL_REENTRY 湲곗? anchor | position_manager |
| `trailing_exit_anchor_price` | TRAILING_REENTRY activation 湲곗? anchor | position_manager |
| `exit_anchor_price` | legacy/deprecated anchor. ?명솚/濡쒓렇??| position_manager |
| `cycle_sell_vwap_price`, `cycle_sell_median_price`, `cycle_highest_sell_price`, `cycle_last_sell_price` | ?꾨웾 留ㅻ룄 ?ъ씠?댁쓽 留ㅻ룄 泥닿껐 ?붿빟 | position_manager |
| `post_exit_high_price` | WAIT_REENTRY ?댄썑 怨좎젏 tracking | `update_reentry_tracking()` |
| `review_reason`, `review_created_at`, `review_trigger_values` | REVIEW_REQUIRED ?ъ쑀? ?몃━嫄?媛?| position_manager/ui_service |
| `review_acknowledged_at`, `review_acknowledged_by`, `review_note` | ?ъ슜?먭? 寃?좏뻽?뚯쓣 湲곕줉. BUY 李⑤떒 ?댁젣 ?꾨떂 | ui_service |
| `entry_price_for_lot_sizing`, `lot_unit_amount`, `max_symbol_amount`, `max_lots_per_symbol`, `lot_sizing_bucket`, `lot_sizing_locked_at`, `lot_sizing_mode` | cycle-locked lot sizing | position_manager/strategy migration |
| `sync_status`, `lot_quantity_mismatch` | DB/KIS 遺덉씪移?愿???곹깭 | reconciliation/review recheck |

吏곸젒 ?섏젙 湲덉?: 蹂댁쑀 ?섎웾, invested amount, state瑜?UI?먯꽌 ?꾩쓽濡???뼱?곕㈃ ???쒕떎. ?섎룞 議곗젙? 蹂꾨룄 maintenance mode媛 ?꾩슂?섎떎.

### lots

`lots`??留ㅼ닔 泥닿껐 ?⑥쐞??蹂댁쑀 LOT????ν븳??

| ?꾨뱶 | ?섎? |
| --- | --- |
| `lot_id` | LOT ?앸퀎??|
| `code`, `name` | 醫낅ぉ |
| `buy_price`, `buy_quantity`, `buy_amount`, `buy_time` | 留ㅼ닔 泥닿껐 ?뺣낫 |
| `remaining_quantity` | ?꾩쭅 ?⑥븘 ?덈뒗 ?섎웾. 遺遺꾨ℓ????媛먯냼 |
| `status` / `lot_status` | OPEN/CLOSED/STALE ??|
| `base_target_profit_rate` | 怨쇨굅 ?명솚/濡쒓렇?? ?ㅼ젣 sell ?먮떒? current lot band target ?곗꽑 |
| `effective_target_profit_rate` | current base target - age decay |
| `cleanup_candidate` | ?먯떎?뺣━ ?꾨낫 ?쒖떆 |
| `last_sell_reason` | 留덉?留?留ㅻ룄 ?ъ쑀 |

OPEN LOT 湲곗?? `remaining_quantity > 0`?닿퀬 `status != CLOSED`?대떎. CLOSED LOT? 留ㅼ닔/留ㅻ룄 ?먮떒怨?current_open_lot_count?먯꽌 ?쒖쇅?쒕떎.

### orders

`orders`??二쇰Ц ?붿껌怨??곹깭瑜???ν븳?? 二쇰Ц ?붿껌 吏곹썑 lots/positions??諛붾뚯? ?딅뒗??

吏꾪뻾 以묒쑝濡?媛꾩＜?섎뒗 status:

`REQUESTED`, `PARTIAL`, `SUBMITTED`, `ACCEPTED`, `PENDING`, `OPEN`, `NEW`

醫낃껐濡?媛꾩＜?섎뒗 status:

`FILLED`, `CANCELED`, `REJECTED`, `FAILED`, `EXPIRED`, `PARTIAL_CANCELED`, `NONE`

### fills

`fills`???ㅼ젣 泥닿껐????ν븳?? fill dedupe媛 ?덉쟾?깆쓽 以묒떖?대떎.

| ?꾨뱶 | ?섎? |
| --- | --- |
| `fill_id` | ?대? 泥닿껐 row id |
| `execution_id` | KIS 泥닿껐踰덊샇媛 ?덉쑝硫?理쒖슦??dedupe key |
| `order_id` | ?대? 二쇰Ц id |
| `code`, `side`, `price`, `quantity`, `filled_at` | 泥닿껐 ?뺣낫 |
| `lot_id` | SELL???????LOT, BUY?????앹꽦 LOT ?곌껐 |
| `dedupe_key_type` | `execution_id` ?먮뒗 `fallback` |

fallback dedupe key??`order_id`, `code`, `side`, `lot_id`, `price`, `quantity`, `filled_at` 議고빀?대떎. `filled_at`? 議고쉶?쒓컖???꾨땲??KIS ?먮낯 泥닿껐?쒓컖?먯꽌 ?뚯떛?댁빞 ?쒕떎.

### manual_order_requests

UI??liquidation script媛 ?섎룞 二쇰Ц ?붿껌????ν븯???먮떎. UI?????뚯씠釉붿뿉 ?붿껌留??ｊ퀬, ?ㅼ젣 二쇰Ц? Bot Core媛 ?뚮퉬?쒕떎.

二쇱슂 ?꾨뱶: `request_id`, `source`, `requested_by`, `requested_at`, `code`, `side`, `amount`, `quantity`, `lot_id`, `order_type`, `preview_json`, `runtime_snapshot_json`, `live_trading`, `confirm_text_verified`, `status`, `block_reason`, `linked_order_id`, `created_at`, `updated_at`.

reset??留됰뒗 吏꾪뻾 以?status:

`REQUESTED`, `PROCESSING`, `ACCEPTED`, `SUBMITTED`, `PENDING`, `OPEN`, `NEW`, `CREATED`, `RETRYING`

醫낃껐 status:

`FILLED`, `CANCELED`, `REJECTED`, `FAILED`, `BLOCKED`, `EXPIRED`

## 5. position_state ?곹깭 ?뺤쓽? ?꾩씠

| ?곹깭 | ?섎? | BUY | SELL | 吏꾩엯/?꾩씠 | 二쇱슂 block/skip |
| --- | --- | --- | --- | --- | --- |
| `NEVER_BOUGHT` | ??踰덈룄 留ㅼ닔?????녿뒗 ?꾨낫 醫낅ぉ | initial_buy 媛??| ?놁쓬 | config stock ?꾨낫, OPEN LOT ?놁쓬 | price/lot sizing/global/risk/order guard |
| `HOLDING` | OPEN LOT 1媛??댁긽 | 異붽?留ㅼ닔 媛?? guard ?듦낵 ?꾩슂 | PROFIT_TAKE 媛?? CLEANUP 議곌굔遺 媛??| BUY fill 諛섏쁺 ??| open order, global BUY limit, cleanup cooldown |
| `WAIT_REENTRY` | PROFIT_TAKE ?꾨웾 留ㅻ룄 ???ъ쭊???湲?| NORMAL/TRAILING_REENTRY留?媛??| ?놁쓬 | ?꾨웾 PROFIT_TAKE ??| initial_buy 湲덉?, reentry guard |
| `COOLDOWN_AFTER_CLEANUP` | CLEANUP_SELL ?꾨웾 留ㅻ룄 ??蹂댁닔 ?湲?| 紐⑤뱺 BUY 湲덉? | ?쇰컲?곸쑝濡?OPEN LOT ?놁쓬 | ?꾨웾 cleanup ??| cleanup cooldown, ?먮룞 ?ъ쭊??湲덉? |
| `REVIEW_REQUIRED` | ?먮룞 ?먮떒留뚯쑝濡?怨꾩냽 吏꾪뻾?섍린 ?꾪뿕 | BUY 湲덉? | PROFIT_TAKE ?덉슜, CLEANUP_SELL 李⑤떒 | ?먯떎/LOT怨쇰떎/stale/cleanup ?꾨즺 ??| `review_required` |
| `RISK_BLOCKED` | ?꾪뿕 ?뚮옒洹??곹깭 | 李⑤떒 | ?꾩옱 蹂댁닔?뺤콉??李⑤떒 | stock risk flag | `risk_blocked_buy_sell_blocked` |
| `SYNC_REQUIRED` | KIS ?붽퀬? DB 遺덉씪移?| 李⑤떒 | ?좉퇋 二쇰Ц 李⑤떒 | reconciliation mismatch | `sync_required` |

WAIT_REENTRY? COOLDOWN_AFTER_CLEANUP? 紐낇솗??遺꾨━?쒕떎. PROFIT_TAKE ?꾨웾 留ㅻ룄留?WAIT_REENTRY濡?媛꾨떎. CLEANUP_SELL ?꾨웾 留ㅻ룄??WAIT_REENTRY濡?諛붾줈 媛吏 ?딅뒗??

## 6. LOT ?⑥쐞 留ㅻℓ ?꾨왂

LOT? 媛쒕퀎 留ㅼ닔 泥닿껐 ?⑥쐞?? ??遊뉗? 醫낅ぉ ?됯퇏?④? ?섎굹濡?留ㅻ룄 ?먮떒???섏? ?딄퀬, 媛?LOT??留ㅼ닔媛, ?⑥? ?섎웾, ?섏씠, ?꾩옱媛, target, ?덉긽 ?ㅽ쁽?먯씡??湲곗??쇰줈 ?먮떒?쒕떎.

遺遺꾨ℓ?????대떦 LOT??`remaining_quantity`留?媛먯냼?쒕떎. `remaining_quantity`媛 0???섎㈃ CLOSED 泥섎━?쒕떎. ?щ윭 LOT????醫낅ぉ???덉뼱??媛?LOT? ?낅┰?곸쑝濡?PROFIT_TAKE/CLEANUP ?꾨낫媛 ?쒕떎.

STALE_LOT? ?먮룞 ?먯젅 ?좏샇媛 ?꾨땲???ㅻ옒???먯떎 LOT ?쒖떆?? cleanup 議곌굔怨?loss budget??留뚯”?댁빞 CLEANUP_SELL ?꾨낫媛 ?쒕떎. 議곌굔???ы빐吏硫?REVIEW_REQUIRED ?꾨낫媛 ?쒕떎.

## 7. 媛寃⑸?蹂?LOT sizing / cycle lock

?꾩옱 `lot_sizing_mode = cycle_locked_by_entry_price`?대떎.

| 媛寃?援ш컙 | 1 LOT 湲덉븸 | 醫낅ぉ??理쒕?湲덉븸 | max_lots | enabled | note |
| --- | ---: | ---: | --- | --- | --- |
| 0~300 | 0 | 0 |  | false | 珥덉?媛二쇰뒗 ?먮룞留ㅼ닔 ?쒖쇅 ?먮뒗 paper ?꾩슜 |
| 301~1,000 | 3,000 | 30,000 |  | true |  |
| 1,001~10,000 | 10,000 | 100,000 |  | true |  |
| 10,001~30,000 | 30,000 | 300,000 |  | true |  |
| 30,001~100,000 | 100,000 | 1,000,000 |  | true |  |
| 100,001~300,000 | 300,000 | 3,000,000 |  | true |  |
| 300,001~1,000,000 | 1,000,000 | 3,000,000 | 3 | true | 怨좉?二쇰뒗 3 LOT源뚯?留?|
| 1,000,001~3,000,000 | 0 | 0 |  | false | 珥덇퀬媛二쇰뒗 ?먮룞留ㅼ닔 ?쒖쇅, manual only |

cycle lock ?먯튃:

1. NEVER_BOUGHT initial buy ?먮뒗 WAIT_REENTRY ?ъ쭊?낆쿂?????ъ씠?댁씠 ?쒖옉?????꾩옱媛 湲곗? price_lot_band瑜?怨좊Ⅸ??
2. ?좏깮??`lot_unit_amount`, `max_symbol_amount`, `max_lots_per_symbol`, `lot_sizing_bucket`??position????ν븳??
3. HOLDING 以??꾩옱媛媛 ?ㅻⅨ 媛寃?援ш컙?쇰줈 ?대룞?대룄 ??λ맂 sizing??怨꾩냽 ?대떎.
4. OPEN LOT???⑥븘 ?덉쑝硫?sizing???ш퀎?고븯嫄곕굹 overwrite?섏? ?딅뒗??
5. ?꾨웾 PROFIT_TAKE ??WAIT_REENTRY?먯꽌 ?ъ쭊?낇븯硫????ъ씠?대줈 蹂닿퀬 ?ъ쭊???뱀떆 媛寃?湲곗??쇰줈 overwrite?쒕떎.
6. CLEANUP ?꾨웾 留ㅻ룄 ????吏꾩엯?????ъ씠?댁씠??
7. 湲곗〈 OPEN LOT???덈뒗??lot sizing ?꾨뱶媛 鍮꾩뼱 ?덉쑝硫?泥?OPEN LOT buy_price 湲곗??쇰줈 migration/fallback?쒕떎. ?섎웾? ?덈? 諛붽씀吏 ?딅뒗??
8. UI manual BUY preview? Bot Core 泥섎━ 吏곸쟾 ?ㅼ젣 媛寃?湲곗? bucket/amount媛 ?щ씪吏硫?`lot_sizing_changed_after_preview`濡?李⑤떒?섍퀬 ?ㅼ떆 preview?섍쾶 ?쒕떎.

愿??skip/block reason:

- `lot_sizing_band_disabled`
- `price_out_of_lot_sizing_range`
- `max_lots_per_symbol_reached`
- `max_symbol_amount_reached`
- `lot_unit_amount_below_price`
- `lot_sizing_changed_after_preview`
- `lot_sizing_missing`
- `lot_sizing_migrated`

## 8. 異붽?留ㅼ닔 濡쒖쭅

異붽?留ㅼ닔???덉쟾???덈?湲덉븸 exposure band ???LOT 諛곗닔 band瑜??ъ슜?쒕떎.

| OPEN LOT ??| ?섎씫 議곌굔 | 異붽? LOT |
| --- | ---: | ---: |
| 1~2 | -4.0% | 1 |
| 3~4 | -6.0% | 1 |
| 5~6 | -8.0% | 1 |
| 7~8 | -10.0% | 1 |
| 9~10 | -12.0% | 1 |

current_open_lot_count??諛섎뱶??OPEN LOT 湲곗??대떎. 利?`remaining_quantity > 0`?닿퀬 `status != CLOSED`??LOT留??쇰떎. 9 LOT 蹂댁쑀 ?곹깭?먯꽌 1 LOT 異붽????덉슜?섏뼱 10 LOT源뚯? 媛????덉?留? 10 LOT ?곹깭?먯꽌??異붽?留ㅼ닔 李⑤떒?대떎.

異붽?留ㅼ닔 ?쒗븳? ?ㅼ쓬??紐⑤몢 ?듦낵?댁빞 ?쒕떎.

| ?쒗븳 | ?ㅻ챸 |
| --- | --- |
| `current_open_lot_count < max_lots_per_symbol` | 醫낅ぉ蹂?LOT 媛쒖닔 ?곹븳. |
| `current_invested_amount + next_buy_amount <= max_symbol_amount` | 醫낅ぉ蹂?湲덉븸 ?곹븳. |
| `next_buy_amount = lot_unit_amount * add_lot_count` | cycle-locked LOT 湲덉븸 湲곗?. |
| `quantity >= 1` | ?꾩옱媛媛 lot_unit_amount蹂대떎 ?믪븘 1二쇰룄 紐??щ㈃ 李⑤떒. |
| ?꾩뿭 由ъ뒪???쒗븳 | max_total_open_lots, max_total_invested_amount, max_new_buy_amount_per_day ?깆? 怨꾩냽 ?댁븘 ?덈떎. |

reference_buy_price 怨꾩궛:

| PnL mode | reference_buy_price |
| --- | --- |
| MINUS | `min(open_lot_vwap_buy_price, median_open_buy_price)` |
| NEUTRAL | `min(open_lot_vwap_buy_price, median_open_buy_price)` |
| PLUS | `max(open_lot_vwap_buy_price, median_open_buy_price)` |

`lowest_open_buy_lot_price`, `highest_open_buy_lot_price`??濡쒓렇/?붾쾭源낆슜?쇰줈 ?④린吏留?reference 怨꾩궛?먮뒗 吏곸젒 ?곗? ?딅뒗??

## 9. target_profit_pct / SELL ?먮떒

target profit? 留ㅼ닔 ?뱀떆 LOT??怨좎젙?섏? ?딅뒗?? 留ㅻ룄 ?먮떒 吏곸쟾???꾩옱 OPEN LOT ??湲곗??쇰줈 紐⑤뱺 OPEN LOT??媛숈? current lot band target???곸슜?쒕떎.

| ?꾩옱 OPEN LOT ??| ?숈쟻 target_profit_rate |
| --- | ---: |
| 1~2 | 6.0% |
| 3~4 | 5.0% |
| 5~6 | 4.0% |
| 7~8 | 3.0% |
| 9~10 | 2.0% |

?? 怨쇨굅 1~2 LOT 援ш컙?먯꽌 ??LOT???꾩옱 6 OPEN LOT ?곹깭?쇰㈃ 5~6 LOT 援ш컙 target??4% 湲곗??쇰줈 SELL ?먮떒?쒕떎. ?쇰? LOT??留ㅻ룄?댁꽌 4 OPEN LOT???섎㈃ ?ㅼ쓬 ?먮떒遺???⑥? LOT?ㅼ? 3~4 LOT 援ш컙 target??5% 湲곗??쇰줈 ?ы룊媛?쒕떎.

怨듭떇:

```text
current_base_target_profit_rate = target_profit_lot_bands[current_open_lot_count]
effective_target_profit_rate = current_base_target_profit_rate - lot_age_weeks * age_decay_rate
```

`original_lot_base_target_profit_rate` ?먮뒗 LOT row??`base_target_profit_rate`??怨쇨굅 ?명솚/濡쒓렇?⑹씠?? ?ㅼ젣 SELL ?먮떒? `current_base_target_profit_rate`瑜??곗꽑?쒕떎.

以묒슂 濡쒓렇 ?꾨뱶: `original_lot_base_target_profit_rate`, `current_base_target_profit_rate`, `target_profit_source=current_lot_band`, `target_profit_lot_band`, `effective_target_profit_rate`, `lot_age_weeks`, `age_decay_rate`.

## 10. PROFIT_TAKE / CLEANUP_SELL / STALE_LOT

PROFIT_TAKE???ㅼ젣 ?덉긽 ?먯씡??0 ?댁긽??留ㅻ룄?? 蹂몄쟾 留ㅻ룄??PROFIT_TAKE?? effective target???뚯닔?щ룄 ?ㅼ젣 ?먯씡???뚮윭?ㅻ㈃ PROFIT_TAKE??

CLEANUP_SELL? ?ㅼ젣 ?덉긽 ?먯씡???뚯닔???먯떎 ?뺣━ 留ㅻ룄?? cleanup? ?먯떎 ?뺤젙?대?濡??꾨옒 議곌굔??留뚯”?댁빞 ?쒕떎.

| 議곌굔 | ?섎? |
| --- | --- |
| `cleanup_enabled=true` | ?꾩옱 expansion_100_safe 珥덇린媛믪? false. |
| LOT ?섏씠 >= cleanup_min_age_weeks | ?ㅻ옒??LOT留?cleanup 媛?? |
| `effective_target_profit_rate < 0` | ?쒓컙 寃쎄낵濡?紐⑺몴媛 ?뚯닔 ?곸뿭源뚯? ?대젮??LOT. |
| `realized_pnl_rate < 0` | ?ㅼ젣 ?먯떎 留ㅻ룄留?cleanup. |
| `realized_pnl_rate >= cleanup_min_target_rate` | 湲곕낯 -4%蹂대떎 ???먯떎? ?먮룞 cleanup 湲덉?. |
| `symbol_state == HOLDING` | REVIEW_REQUIRED?먯꽌??cleanup 李⑤떒. |
| open order ?놁쓬 | 媛숈? symbol??REQUESTED/PARTIAL ??吏꾪뻾 以?二쇰Ц???덉쑝硫?李⑤떒. |
| cleanup_loss_budget 異⑹” | ?뱀씪 ?ㅽ쁽?섏씡 ?쇰?濡쒕쭔 ?먯떎 ?곸뇙. |

STALE_LOT 議곌굔? `lot_unrealized_pnl_rate <= -15%`, `lot_age_weeks >= 8`, `current_price <= buy_price * 0.90`?대떎. STALE_LOT? 利됱떆 留ㅻ룄?섏? ?딅뒗?? ?ㅻ옒 吏?띾릺嫄곕굹 醫낅ぉ ?먯떎???ы븯硫?REVIEW_REQUIRED濡?媛꾨떎.

## 11. Reentry 濡쒖쭅

?꾨웾 PROFIT_TAKE ??OPEN LOT??0媛쒓? ?섎㈃ `WAIT_REENTRY`濡?媛꾨떎. CLEANUP_SELL ?꾨웾 留ㅻ룄??WAIT_REENTRY媛 ?꾨땲??COOLDOWN_AFTER_CLEANUP?대떎.

Reentry anchor??normal/trailing ?⑸룄濡?遺꾨━?섏뼱 ?덈떎.

| ?꾨뱶 | 怨꾩궛 | ?⑸룄 |
| --- | --- | --- |
| `normal_exit_anchor_price` | `min(cycle_sell_vwap_price, cycle_sell_median_price)` | ?닿? ????쒓?寃⑸낫??異⑸텇???몄죱?붿? ?먮떒. |
| `trailing_exit_anchor_price` | `max(cycle_sell_vwap_price, cycle_sell_median_price)` | 留ㅻ룄 ?????ㅻⅨ 醫낅ぉ???ㅼ떆 異붿쟻??activation 湲곗?. |
| `exit_anchor_price` | deprecated/fallback, 蹂댄넻 normal anchor? ?명솚 | ??濡쒖쭅?먯꽌 吏곸젒 湲곗??쇰줈 ?곗? ?딅뒗?? |
| `cycle_highest_sell_price`, `cycle_last_sell_price` | 濡쒓렇/李멸퀬 | anchor 怨꾩궛??吏곸젒 ?곗? ?딅뒗?? |

NORMAL_REENTRY 議곌굔: `current_price <= normal_exit_anchor_price * (1 - normal_reentry_drop_rate)`.

TRAILING_REENTRY 議곌굔:

1. `post_exit_high_price >= trailing_exit_anchor_price * (1 + trailing_activation_gain)`
2. `current_price <= post_exit_high_price * (1 - trailing_reentry_drop_rate)`
3. `now - exit_time >= min_reentry_wait_minutes`
4. `trailing_reentry_count_today < max_trailing_reentry_per_day`

`update_reentry_tracking()`? WAIT_REENTRY 以?`post_exit_high_price`留?媛깆떊?쒕떎. `check_reentry_conditions()`???곹깭 蹂寃?遺?묒슜 ?놁씠 ?먮떒?쒕떎.

## 12. 二쇰Ц/泥닿껐/DB 諛섏쁺

?먮쫫:

1. strategy媛 action ?꾨낫瑜?留뚮뱺??
2. main??runtime/risk/open-order/live/global guard瑜???踰????뺤씤?쒕떎.
3. order_manager媛 二쇰Ц???붿껌?섍퀬 `orders`??湲곕줉?쒕떎.
4. KIS executions 議고쉶??利됱떆 泥닿껐 ?뺤씤?쇰줈 raw execution???뺢퇋?뷀븳??
5. `store.record_fill(fill)`???좉퇋 泥닿껐?대㈃ true瑜?諛섑솚?쒕떎.
6. true???뚮쭔 `position_manager.apply_fill(fill)`???몄텧?쒕떎.
7. duplicate ?먮뒗 record_fill ?ㅽ뙣硫?濡쒓렇留??④린怨?positions/lots瑜?諛붽씀吏 ?딅뒗??

泥닿껐 議고쉶:

| ?곹솴 | ?숈옉 |
| --- | --- |
| startup recent reconciliation | `reconcile_recent_executions_on_startup=true`, 理쒓렐 1??議고쉶. ??λ맂 二쇰Ц怨?留ㅼ묶?섎뒗 泥닿껐留?諛섏쁺. |
| ?쇰컲 猷⑦봽 | open order媛 ?덉쓣 ??executions 議고쉶. |
| open order query range | oldest open order requested_at - buffer? ?ㅻ뒛 00:00 以????대Ⅸ ?쒓컖, previous day ?듭뀡 媛?? |
| unmatched/manual execution | ?먮룞 LOT???욎? ?딄퀬 ignored_unmatched濡?吏묎퀎. |

raw execution log??`enable_execution_raw_log=true`???뚮쭔 ?④릿?? 誘쇨컧?뺣낫??留덉뒪?뱁빐???쒕떎. ?ㅼ젣 KIS raw log?먯꽌 ?뺤씤???꾨뱶: order_no, execution_id, filled_at, side, code, price, quantity.

## 13. Manual order requests

UI ?섎룞 二쇰Ц 援ъ“:

1. UI?먯꽌 preview ?앹꽦. ???④퀎?먯꽌??二쇰Ц API ?몄텧 ?놁쓬.
2. live trading?대㈃ confirm text ?꾩슂.
3. `ui_manual_trading_enabled=false`?대㈃ API/踰꾪듉 紐⑤몢 request ?앹꽦 李⑤떒.
4. request ?앹꽦 ??`manual_order_requests`??`REQUESTED` row留?insert.
5. Bot Core媛 猷⑦봽 以?REQUESTED request瑜??쎈뒗??
6. runtime pause, risk guard, open order guard, live guard, lot/symbol ?곹깭, lot sizing ?ш?利앹쓣 ?섑뻾?쒕떎.
7. ?듦낵?섎㈃ 湲곗〈 order_manager 寃쎈줈濡?二쇰Ц ?붿껌??留뚮뱺??
8. fill ?꾧퉴吏 lots/positions??諛붾뚯? ?딅뒗??

manual BUY??NEVER_BOUGHT/WAIT_REENTRY?대㈃ ?꾩옱 泥섎━ ?쒖젏 媛寃?湲곗? lot sizing???덈줈 ?곗젙?쒕떎. HOLDING?대㈃ 湲곗〈 cycle lock???곕Ⅸ?? preview? 泥섎━ 吏곸쟾 bucket???ㅻⅤ硫?`lot_sizing_changed_after_preview`濡?block?쒕떎.

manual SELL? LOT蹂?`lot_id`? ?붿뿬 ?섎웾???좎??쒕떎. CLOSED LOT, ?⑥? ?섎웾 珥덇낵, open SELL order, RISK_BLOCKED, SYNC_REQUIRED, runtime sell pause ?곹깭??李⑤떒?쒕떎.

## 14. REVIEW_REQUIRED 泥섎━

REVIEW_REQUIRED???먮룞 BUY瑜?硫덉텛怨??щ엺???뺤씤?댁빞 ?섎뒗 ?곹깭?? 媛뺤젣 ?댁젣??留뚮뱾吏 ?딅뒗??

吏꾩엯 議곌굔 ??

| reason | ?섎? |
| --- | --- |
| `symbol_loss_review` | 醫낅ぉ ?먯떎瑜좎씠 湲곗? ?댄븯. |
| `too_many_open_lots` | OPEN LOT ?섍? ?쒗븳 珥덇낵. |
| `stale_lot_review_age` | ?ㅻ옒??STALE_LOT ?κ린 吏?? |
| `auto_buy_limit_exceeded` | ?덇굅??湲덉븸 湲곗? ?먮룞留ㅼ닔 ?쒕룄 珥덇낵. |
| `cleanup_cooldown_complete` | cleanup ?꾨웾 留ㅻ룄 ???먮룞 ?ъ쭊??????섎룞寃?좊줈 蹂대궦 ?곹깭. |

UI/API:

| API | ??븷 |
| --- | --- |
| `GET /api/review-required` | REVIEW_REQUIRED 醫낅ぉ 紐⑸줉, reason, trigger values, 異붿쿇 議곗튂. |
| `GET /api/positions/{code}/review-status` | ?뱀젙 醫낅ぉ review ?곹깭. |
| `POST /api/positions/{code}/review/recheck` ?먮뒗 `/api/review-required/{code}/recheck` | ?꾩옱 DB/sync 湲곗? ?ы룊媛. 議곌굔 ?댁냼 ??HOLDING/WAIT_REENTRY ??蹂듦?, mismatch硫?SYNC_REQUIRED. |
| `POST /api/positions/{code}/review/acknowledge` ?먮뒗 `/api/review-required/{code}/acknowledge` | ?ъ슜?먭? ?뺤씤?덈떎??湲곕줉留??④?. BUY 李⑤떒 ?댁젣 ?꾨떂. |

?섎룞留ㅻ룄 ?꾩뿉??reconciliation???꾨즺?섏뼱??recheck媛 ?섎? ?덈떎. sync mismatch媛 ?덉쑝硫?REVIEW_REQUIRED ?댁젣媛 ?꾨땲??SYNC_REQUIRED濡?媛???쒕떎.

## 15. Runtime Control

`config/runtime_control.json`? ?댁쁺 以?利됱떆 ?곸슜?섎뒗 ?쒖뼱 ?뚮옒洹몃떎. config蹂대떎 ?곗꽑?쒕떎.

| ?뚮옒洹?| 李⑤떒 ???| block reason |
| --- | --- | --- |
| `all_orders_paused` | BUY/SELL ?꾩껜 | `runtime_all_orders_paused` |
| `buy_paused` | initial/add/reentry BUY | `runtime_buy_paused` |
| `sell_paused` | PROFIT_TAKE/CLEANUP SELL | `runtime_sell_paused` |
| `cleanup_paused` | CLEANUP_SELL留?| `runtime_cleanup_paused` |
| `reentry_paused` | NORMAL/TRAILING_REENTRY | `runtime_reentry_paused` |
| emergency stop | 紐⑤뱺 pause ?뚮옒洹?true | emergency stop reason |
| `bot_paused` | ?먮룞 猷⑦봽 吏꾪뻾 ?뺤? | UI loop pause |
| `config_reload_requested` | ?ㅽ뻾 以?config reload ?붿껌 | Reset / Config ?ㅼ떆 ?쎄린 |

理쒓렐 援ы쁽?먯꽌??runtime control怨?manual request媛 loop sleep 以묒뿉??鍮좊Ⅴ寃?諛섏쁺?섎룄濡?interrupt 泥댄겕瑜??ｌ뿀??

## 16. Risk / Global limits

?꾩옱 expansion_100_safe 湲곗?:

| ??ぉ | ?꾩옱媛?| ?섎? |
| --- | --- | --- |
| `risk.profile` | `expansion_100_safe` | ?꾩옱 ?곸슜 以묒씤 由ъ뒪???꾨줈??|
| `max_active_symbols` | 100 | 愿由?蹂댁쑀 媛?ν븳 ?쒖꽦 醫낅ぉ ?곹븳 |
| `max_new_buy_per_day` | 10 | ?섎（ ?좉퇋 initial buy 二쇰Ц ???쒗븳. reentry???ы븿?섏? ?딅뒗??|
| `max_new_buy_amount_per_day` | 2,000,000 | ?섎（ ?좉퇋 留ㅼ닔 湲덉븸 ?쒗븳 |
| `max_total_initial_buy_amount_per_day` | 2,000,000 | initial buy 珥앹븸 ?쒗븳 |
| `max_total_open_lots` | 300 | 怨꾩쥖 ?꾩껜 OPEN LOT ???쒗븳 |
| `max_total_invested_amount` | 20,000,000 | 怨꾩쥖 ?꾩껜 ?ъ엯湲??쒗븳 |
| `cleanup_enabled` | false | 珥덇린 100醫낅ぉ ?뺤옣 ?댁슜?먯꽌??false ?좎? 沅뚯옣 |
| `ui_manual_trading_enabled` | false | UI ?섎룞 二쇰Ц ?붿껌 ?앹꽦 湲곕낯媛?|
| `live_trading` | false | ?ㅺ굅??二쇰Ц 媛???щ? |
| `enable_execution_raw_log` | true | 泥??ㅼ껜寃?raw mapping ?뺤씤??|

profile ?꾨낫:

| profile | max_total_invested_amount | max_new_buy_per_day | max_new_buy_amount_per_day | max_total_open_lots | max_active_symbols |
| --- | ---: | ---: | ---: | ---: | ---: |
| expansion_100_safe | 20,000,000 | 10 | 2,000,000 | 300 | 100 |
| expansion_100_medium | 30,000,000 | 15 | 3,000,000 | 450 | 100 |
| expansion_100_aggressive | 50,000,000 | 20 | 5,000,000 | 700 | 100 |

?꾩뿭 由ъ뒪???쒗븳? LOT sizing怨?蹂꾨룄濡?怨꾩냽 ?댁븘 ?덈떎. 醫낅ぉ蹂?max_symbol_amount/max_lots媛 ?듦낵?섏뼱??怨꾩쥖 ?꾩껜 max_total_open_lots, max_total_invested_amount, max_new_buy_per_day, max_new_buy_amount_per_day??嫄몃━硫?BUY??李⑤떒?쒕떎. SELL? ?꾩뿭 ?몄텧 ?쒗븳 ?뚮Ц??留됱? ?딅뒗??

## 17. KOSPI 100 ?꾨낫援?

?꾩옱 config?먮뒗 100醫낅ぉ???ㅼ뼱 ?덇퀬 enabled 97醫낅ぉ, disabled/manual_only 3醫낅ぉ?대떎. disabled/manual_only 醫낅ぉ? ?먮룞留ㅼ닔 ?쒖쇅?대ŉ UI? config?먯꽌 note/risk flag濡??쒖떆?쒕떎.

?밸퀎 泥섎━ 3醫낅ぉ:

| code | name | 泥섎━ | ?댁쑀 |
| --- | --- | --- | --- |
| 005935 | ?쇱꽦?꾩옄??| enabled=false, manual_only=true, liquidity_warning=true | KIS KOSPI master 寃利앹뿉??誘명솗?몃릺???먮룞留ㅼ닔 鍮꾪솢??|
| 001230 | ?숆뎅??⑹뒪 | enabled=false, manual_only=true, trading_halted=true | KIS KOSPI master 湲곗? trading_halt_yn=Y |
| 020560 | ?꾩떆?꾨굹??났 | enabled=false, manual_only=true, administrative_issue=true | ??쒗빆怨??듯빀/釉뚮옖??醫낅즺 ?쇱젙 愿???대깽??由ъ뒪??|

?꾩껜 ?꾨낫援곗? `config/lot_auto_trader.json`??`stocks` 諛곗뿴???먮낯?대떎. ?꾨옒 ?쒕뒗 ?꾩옱 臾몄꽌???쒖젏???꾨낫援곗씠?? ???몄뀡?먯꽌 ?ㅼ젣 ?묒뾽???댁뼱媛??뚮뒗 config瑜??ㅼ떆 ?쎌뼱 理쒖떊 enabled/risk flag瑜??뺤씤?섎뒗 寃껋씠 媛???덉쟾?섎떎.

| # | code | name | market | sector | enabled | manual_only | risk flags / note |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | 005930 | ?쇱꽦?꾩옄 | KOSPI | 諛섎룄泥?| true | false | - |
| 2 | 000660 | SK?섏씠?됱뒪 | KOSPI | 諛섎룄泥?| true | false | - |
| 3 | 005380 | ?꾨?李?| KOSPI | ?먮룞李?| true | false | - |
| 4 | 000270 | 湲곗븘 | KOSPI | ?먮룞李?| true | false | - |
| 5 | 012330 | ?꾨?紐⑤퉬??| KOSPI | ?먮룞李⑤???| true | false | - |
| 6 | 005935 | ?쇱꽦?꾩옄??| KOSPI | 諛섎룄泥댁슦?좎＜ | false | true | liquidity_warning, KIS KOSPI master 誘명솗?몄쑝濡??먮룞留ㅼ닔 鍮꾪솢??|
| 7 | 035420 | NAVER | KOSPI | ?뚮옯??| true | false | - |
| 8 | 035720 | 移댁뭅??| KOSPI | ?뚮옯??| true | false | - |
| 9 | 207940 | ?쇱꽦諛붿씠?ㅻ줈吏곸뒪 | KOSPI | 諛붿씠??| true | false | - |
| 10 | 068270 | ??몃━??| KOSPI | 諛붿씠??| true | false | - |
| 11 | 051910 | LG?뷀븰 | KOSPI | ?뷀븰/諛고꽣由?| true | false | - |
| 12 | 373220 | LG?먮꼫吏?붾（??| KOSPI | 2李⑥쟾吏 | true | false | - |
| 13 | 006400 | ?쇱꽦SDI | KOSPI | 2李⑥쟾吏 | true | false | - |
| 14 | 003670 | ?ъ뒪肄뷀벂泥섏뿞 | KOSPI | 2李⑥쟾吏?뚯옱 | true | false | - |
| 15 | 005490 | POSCO??⑹뒪 | KOSPI | 泥좉컯/2李⑥쟾吏 | true | false | - |
| 16 | 066570 | LG?꾩옄 | KOSPI | ?꾩옄 | true | false | - |
| 17 | 034220 | LG?붿뒪?뚮젅??| KOSPI | ?붿뒪?뚮젅??| true | false | - |
| 18 | 011070 | LG?대끂??| KOSPI | ?꾩옄遺??| true | false | - |
| 19 | 009150 | ?쇱꽦?꾧린 | KOSPI | ?꾩옄遺??| true | false | - |
| 20 | 018260 | ?쇱꽦SDS | KOSPI | IT?쒕퉬??| true | false | - |
| 21 | 003550 | LG | KOSPI | 吏二쇱궗 | true | false | - |
| 22 | 034730 | SK | KOSPI | 吏二쇱궗 | true | false | - |
| 23 | 028260 | ?쇱꽦臾쇱궛 | KOSPI | 吏二?嫄댁꽕 | true | false | - |
| 24 | 086790 | ?섎굹湲덉쑖吏二?| KOSPI | 湲덉쑖 | true | false | - |
| 25 | 105560 | KB湲덉쑖 | KOSPI | 湲덉쑖 | true | false | - |
| 26 | 055550 | ?좏븳吏二?| KOSPI | 湲덉쑖 | true | false | - |
| 27 | 316140 | ?곕━湲덉쑖吏二?| KOSPI | 湲덉쑖 | true | false | - |
| 28 | 024110 | 湲곗뾽???| KOSPI | 湲덉쑖 | true | false | - |
| 29 | 138930 | BNK湲덉쑖吏二?| KOSPI | 吏諛⑷툑??| true | false | - |
| 30 | 175330 | JB湲덉쑖吏二?| KOSPI | 吏諛⑷툑??| true | false | - |
| 31 | 139130 | DGB湲덉쑖吏二?| KOSPI | 吏諛⑷툑??| true | false | - |
| 32 | 032830 | ?쇱꽦?앸챸 | KOSPI | 蹂댄뿕 | true | false | - |
| 33 | 000810 | ?쇱꽦?붿옱 | KOSPI | 蹂댄뿕 | true | false | - |
| 34 | 005830 | DB?먰빐蹂댄뿕 | KOSPI | 蹂댄뿕 | true | false | - |
| 35 | 088350 | ?쒗솕?앸챸 | KOSPI | 蹂댄뿕 | true | false | - |
| 36 | 071050 | ?쒓뎅湲덉쑖吏二?| KOSPI | 利앷텒 | true | false | - |
| 37 | 039490 | ?ㅼ?利앷텒 | KOSPI | 利앷텒 | true | false | - |
| 38 | 006800 | 誘몃옒?먯뀑利앷텒 | KOSPI | 利앷텒 | true | false | - |
| 39 | 030200 | KT | KOSPI | ?듭떊 | true | false | - |
| 40 | 017670 | SK?붾젅肄?| KOSPI | ?듭떊 | true | false | - |
| 41 | 032640 | LG?좏뵆?ъ뒪 | KOSPI | ?듭떊 | true | false | - |
| 42 | 015760 | ?쒓뎅?꾨젰 | KOSPI | ?좏떥由ы떚 | true | false | - |
| 43 | 036460 | ?쒓뎅媛?ㅺ났??| KOSPI | ?좏떥由ы떚 | true | false | - |
| 44 | 051600 | ?쒖쟾KPS | KOSPI | ?꾨젰?쒕퉬??| true | false | - |
| 45 | 052690 | ?쒖쟾湲곗닠 | KOSPI | ?먯쟾/?붿??덉뼱留?| true | false | - |
| 46 | 010950 | S-Oil | KOSPI | ?뺤쑀 | true | false | - |
| 47 | 096770 | SK?대끂踰좎씠??| KOSPI | ?먮꼫吏/諛고꽣由?| true | false | - |
| 48 | 078930 | GS | KOSPI | ?먮꼫吏吏二?| true | false | - |
| 49 | 267250 | HD?꾨? | KOSPI | 議곗꽑/?먮꼫吏吏二?| true | false | - |
| 50 | 329180 | HD?꾨?以묎났??| KOSPI | 議곗꽑 | true | false | - |
| 51 | 010140 | ?쇱꽦以묎났??| KOSPI | 議곗꽑 | true | false | - |
| 52 | 042660 | ?쒗솕?ㅼ뀡 | KOSPI | 議곗꽑/諛⑹궛 | true | false | - |
| 53 | 009540 | HD?쒓뎅議곗꽑?댁뼇 | KOSPI | 議곗꽑吏二?| true | false | - |
| 54 | 064350 | ?꾨?濡쒗뀥 | KOSPI | 諛⑹궛/泥좊룄 | true | false | - |
| 55 | 012450 | ?쒗솕?먯뼱濡쒖뒪?섏씠??| KOSPI | 諛⑹궛 | true | false | - |
| 56 | 047810 | ?쒓뎅??났?곗＜ | KOSPI | 諛⑹궛/??났 | true | false | - |
| 57 | 079550 | LIG?μ뒪??| KOSPI | 諛⑹궛 | true | false | - |
| 58 | 000880 | ?쒗솕 | KOSPI | 吏二?諛⑹궛 | true | false | - |
| 59 | 009830 | ?쒗솕?붾（??| KOSPI | ?뷀븰/?쒖뼇愿?| true | false | - |
| 60 | 011780 | 湲덊샇?앹쑀 | KOSPI | ?뷀븰 | true | false | - |
| 61 | 011170 | 濡?뜲耳誘몄뭡 | KOSPI | ?뷀븰 | true | false | - |
| 62 | 010060 | OCI??⑹뒪 | KOSPI | ?뷀븰/?쒖뼇愿?| true | false | - |
| 63 | 010130 | 怨좊젮?꾩뿰 | KOSPI | 鍮꾩쿋湲덉냽 | true | false | - |
| 64 | 004020 | ?꾨??쒖쿋 | KOSPI | 泥좉컯 | true | false | - |
| 65 | 001230 | ?숆뎅??⑹뒪 | KOSPI | 泥좉컯/吏二?| false | true | trading_halted, KIS master 湲곗? trading_halt_yn=Y |
| 66 | 000720 | ?꾨?嫄댁꽕 | KOSPI | 嫄댁꽕 | true | false | - |
| 67 | 006360 | GS嫄댁꽕 | KOSPI | 嫄댁꽕 | true | false | - |
| 68 | 047040 | ??곌굔??| KOSPI | 嫄댁꽕 | true | false | - |
| 69 | 375500 | DL?댁븻??| KOSPI | 嫄댁꽕 | true | false | - |
| 70 | 294870 | HDC?꾨??곗뾽媛쒕컻 | KOSPI | 嫄댁꽕 | true | false | - |
| 71 | 180640 | ?쒖쭊移?| KOSPI | ??났吏二?| true | false | - |
| 72 | 003490 | ??쒗빆怨?| KOSPI | ??났 | true | false | - |
| 73 | 020560 | ?꾩떆?꾨굹??났 | KOSPI | ??났 | false | true | administrative_issue, ??쒗빆怨??듯빀 ?대깽??由ъ뒪??|
| 74 | 086280 | ?꾨?湲濡쒕퉬??| KOSPI | 臾쇰쪟 | true | false | - |
| 75 | 000120 | CJ??쒗넻??| KOSPI | 臾쇰쪟 | true | false | - |
| 76 | 028670 | ?ъ삤??| KOSPI | ?댁슫 | true | false | - |
| 77 | 011200 | HMM | KOSPI | ?댁슫 | true | false | - |
| 78 | 004990 | 濡?뜲吏二?| KOSPI | 吏二??뚮퉬 | true | false | - |
| 79 | 023530 | 濡?뜲?쇳븨 | KOSPI | ?좏넻 | true | false | - |
| 80 | 004170 | ?좎꽭怨?| KOSPI | ?좏넻 | true | false | - |
| 81 | 139480 | ?대쭏??| KOSPI | ?좏넻 | true | false | - |
| 82 | 282330 | BGF由ы뀒??| KOSPI | ?몄쓽??| true | false | - |
| 83 | 007070 | GS由ы뀒??| KOSPI | ?몄쓽??| true | false | - |
| 84 | 271560 | ?ㅻ━??| KOSPI | ?뚯떇猷?| true | false | - |
| 85 | 097950 | CJ?쒖씪?쒕떦 | KOSPI | ?뚯떇猷?| true | false | - |
| 86 | 004370 | ?띿떖 | KOSPI | ?뚯떇猷?| true | false | - |
| 87 | 007310 | ?ㅻ슌湲?| KOSPI | ?뚯떇猷?| true | false | - |
| 88 | 280360 | 濡?뜲?고뫖??| KOSPI | ?뚯떇猷?| true | false | - |
| 89 | 090430 | ?꾨え?덊띁?쒗뵿 | KOSPI | ?붿옣??| true | false | - |
| 90 | 051900 | LG?앺솢嫄닿컯 | KOSPI | ?붿옣???앺솢?⑺뭹 | true | false | - |
| 91 | 161890 | ?쒓뎅肄쒕쭏 | KOSPI | ?붿옣?늀DM | true | false | - |
| 92 | 192820 | 肄붿뒪留μ뒪 | KOSPI | ?붿옣?늀DM | true | false | - |
| 93 | 001040 | CJ | KOSPI | 吏二??뚮퉬 | true | false | - |
| 94 | 003240 | ?쒓킅?곗뾽 | KOSPI | ?ъ쑀/?뷀븰 | true | false | - |
| 95 | 000150 | ?먯궛 | KOSPI | 吏二?濡쒕큸/?먮꼫吏 | true | false | - |
| 96 | 034020 | ?먯궛?먮꼫鍮뚮━??| KOSPI | ?먯쟾/?뚮옖??| true | false | - |
| 97 | 241560 | ?먯궛諛μ베 | KOSPI | 湲곌퀎 | true | false | - |
| 98 | 042700 | ?쒕?諛섎룄泥?| KOSPI | 諛섎룄泥댁옣鍮?| true | false | - |
| 99 | 000990 | DB?섏씠??| KOSPI | 諛섎룄泥?| true | false | - |
| 100 | 112610 | ?⑥뿉?ㅼ쐢??| KOSPI | ?띾젰/?좎옱??| true | false | - |

## 18. UI ?꾩껜 援ъ“

UI??localhost 愿???쒖뼱 ?붾㈃?대떎. ?몃? 怨듦컻 湲덉?. 怨꾩쥖踰덊샇/API ??token? ?쒖떆?섏? ?딅뒗?? ?뚯씠釉붿? ?쒓? ?쇰꺼怨??대? key瑜?蹂묎린?섍퀬, ?ъ슜?먭? column ?좏깮怨?column width resize瑜??????덈떎. ??議곗젅媛믪? localStorage????λ맂??

| ??| ??븷 | 二쇱슂 API/?숈옉 | 湲덉??ы빆 |
| --- | --- | --- | --- |
| Dashboard / ??쒕낫??| 遊??곹깭, 由ъ뒪?? warnings, runtime ?붿빟 | `/api/status` | 二쇰Ц ?놁쓬 |
| Stocks / 醫낅ぉ | ?꾨낫/蹂댁쑀 醫낅ぉ, risk flags, 醫낅ぉ蹂?LOT 蹂닿린, manual BUY preview ?곌껐 | `/api/stocks`, `/api/stocks/{code}` | KIS 二쇰Ц 吏곸젒 ?몄텧 ?놁쓬 |
| Lots / LOT | LOT 紐⑸줉, ?먯씡, stale/cleanup, manual SELL preview ?곌껐 | `/api/lots` | LOT 吏곸젒 ?섏젙 湲덉? |
| Orders/Fills / 二쇰Ц/泥닿껐 | 二쇰Ц/泥닿껐 ?곌껐, dedupe_key_type, fallback 泥닿껐 ?뺤씤 | `/api/orders`, `/api/fills` | 二쇰Ц 痍⑥냼/?뺤젙 吏곸젒 ?몄텧 ?놁쓬 |
| Logs / 濡쒓렇 | log tail, keyword/level/event filter, masking | `/api/logs/tail` | 誘쇨컧?뺣낫 ?몄텧 湲덉? |
| Config / ?ㅼ젙 | ??ぉ蹂??ㅻ챸/?낅젰, diff, backup, atomic save, raw JSON 怨좉툒 蹂닿린 | `/api/config`, `/api/config/schema`, `/api/config/validate` | ?섎せ??config ???湲덉? |
| Runtime / ?고???| pause/resume/emergency stop/start-loop/reload-config | `/api/runtime/*` | 二쇰Ц 吏곸젒 ?몄텧 ?놁쓬 |
| Manual / ?섎룞 二쇰Ц | manual order preview/request 紐⑸줉 | `POST /api/manual-orders/preview`, `POST /api/manual-orders`, `GET /api/manual-order-requests` | UI 吏곸젒 二쇰Ц 湲덉? |
| New Season / ???쒖쫵 | archive, liquidation plan, request ?앹꽦, reset guard wizard | `/api/new-season/*` | KIS 二쇰Ц API/DB reset 吏곸젒 ?ㅽ뻾 湲덉?. 踰꾪듉? confirm怨?guard ?꾩슂 |
| Review / ?섎룞寃??| REVIEW_REQUIRED 紐⑸줉, recheck, acknowledge, ?섎룞留ㅻ룄 ?덈궡 | `/api/review-required` | 媛뺤젣 ?댁젣 湲덉? |

李멸퀬: `ui_server.py`?먮뒗 `/api/execution-mapping/status`? `loadExecution()` ?⑥닔媛 ?⑥븘 ?덉?留? 蹂꾨룄 nav ??? ?쒓굅???곹깭?? raw mapping ?곹깭??Dashboard warning/log/API濡??뺤씤?쒕떎.

## 19. Prepare New Season / ???쒖쫵 以鍮?

紐⑹쟻? 湲곗〈 ?쒖쫵 ?댁쁺 湲곕줉???덉쟾?섍쾶 archive?섍퀬, 蹂댁쑀遺꾩쓣 ?ㅼ젣 怨꾩쥖? 留욎떠 ?뺣━???? ??DB/config ?곹깭濡??쒖옉?섎뒗 寃껋씠?? 諛붾줈 DB ?뚯씪??吏?곕㈃ ???쒕떎. ?ㅼ젣 蹂댁쑀媛 ?⑥븘 ?덇굅??泥닿껐 ?숆린?붽? ?앸굹吏 ?딆? ?곹깭?먯꽌 reset?섎㈃ 怨꾩쥖? DB媛 ?곴뎄???닿툔?쒕떎.

?⑹뼱:

| ?⑹뼱 | ?ъ슜??移쒗솕???섎? |
| --- | --- |
| archive | ?댁쟾 ?쒖쫵 諛깆뾽 |
| liquidation plan | ?꾨웾留ㅻ룄 ?덉젙??|
| KIS balance snapshot | ?ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤 ?먮즺 |
| manual SELL request | 遊뉗뿉寃??꾨웾留ㅻ룄 ?붿껌 |
| reset | DB 珥덇린??|
| dry-run | ?ㅽ뻾 ??誘몃━蹂닿린 |

Wizard ?④퀎:

1. ?댁쟾 ?쒖쫵 諛깆뾽: config, DB, logs, table exports瑜?timestamp archive濡?蹂듭궗.
2. ?ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤: KIS balance snapshot???꾩슂?섎떎. 二쇰Ц???꾨땲???붽퀬 ?뺤씤 ?먮즺??
3. ?꾨웾留ㅻ룄 ?덉젙???앹꽦: ?꾩옱 DB OPEN LOT怨?KIS snapshot??鍮꾧탳??留ㅻ룄 ?덉젙???앹꽦.
4. ?꾨웾留ㅻ룄 ?붿껌 ?앹꽦: confirm text `?꾨웾留ㅻ룄 ?붿껌 ?뺤씤` ??manual_order_requests ?먯뿉 SELL request ?앹꽦. UI/script??KIS 二쇰Ц API瑜??몄텧?섏? ?딅뒗??
5. 泥닿껐 諛??숆린???뺤씤: Bot Core媛 request瑜?泥섎━?섍퀬, fills/reconciliation ??OPEN LOT??以꾩뼱???쒕떎.
6. DB 珥덇린?? OPEN LOT 0, 吏꾪뻾 以?order 0, pending manual request 0, SYNC_REQUIRED 0, mismatch 0???뚮쭔 confirm text `RESET ?뺤씤`?쇰줈 媛??
7. ??100醫낅ぉ config ?곸슜 ?뺤씤: expansion_100_safe? KOSPI 100 ?꾨낫援??곸슜 ?뺤씤.
8. ???쒖쫵 ?쒖옉 以鍮??꾨즺: 紐⑤뱺 議곌굔 異⑹” ??UI??以鍮??꾨즺 ?쒖떆.

Plan metadata:

| ?꾨뱶 | ?섎? |
| --- | --- |
| `plan_id`, `created_at` | ?덉젙???앸퀎/?앹꽦?쒓컖 |
| `db_snapshot_at`, `kis_balance_snapshot_at` | 湲곗? snapshot ?쒓컖 |
| `db_open_lot_hash`, `kis_snapshot_hash` | ?앹꽦 ???곹깭 蹂寃?寃利앹슜 hash |
| `open_lot_count`, `pending_order_count`, `pending_manual_request_count` | ?앹꽦 ?쒖젏 ?곹깭 |
| `sync_required_count`, `lot_mismatch_count` | mismatch guard |
| `status` | ACTIVE / EXPIRED / SUPERSEDED / USED / BLOCKED |
| `expires_at`, `max_age_minutes` | KIS snapshot/plan ?좏슚?쒓컙 |

?꾨웾留ㅻ룄 request ?앹꽦 ??plan???ㅼ떆 寃利앺븳?? plan ?앹꽦 ??DB OPEN LOT hash媛 ?щ씪吏嫄곕굹, pending order/manual request媛 ?앷린嫄곕굹, KIS snapshot??留뚮즺?섎㈃ request ?앹꽦? 李⑤떒?쒕떎. ??ACTIVE plan ?앹꽦 ??湲곗〈 ACTIVE plan? SUPERSEDED/EXPIRED 泥섎━?쒕떎. request ?앹꽦 ?깃났 ??plan? USED ?먮뒗 REQUESTED 怨꾩뿴濡?媛꾩＜?쒕떎.

reset 李⑤떒 議곌굔:

| 議곌굔 | block reason |
| --- | --- |
| OPEN LOT 議댁옱 | `reset_open_lot_exists` |
| 吏꾪뻾 以?order 議댁옱 | `reset_pending_order_exists` |
| pending manual request 議댁옱 | `reset_pending_manual_request_exists` |
| SYNC_REQUIRED | `reset_sync_required` |
| lot mismatch | `liquidation_plan_lot_mismatch` |
| KIS/DB mismatch | liquidation request ?④퀎?먯꽌 李⑤떒 |

?꾩옱 ?곹깭??OPEN LOT???⑥븘 ?덉쑝硫?DB reset??李⑤떒?섎뒗 寃껋씠 ?뺤긽?대떎.

## 20. KIS balance snapshot / execution raw mapping

KIS balance snapshot? ?꾨웾留ㅻ룄 ?덉젙??DB reset safety瑜??꾪빐 ?꾩슂?섎떎. DB 湲곗? ?섎웾留뚯쑝濡??꾨웾留ㅻ룄 request瑜?留뚮뱾硫??ㅼ젣 怨꾩쥖 ?붽퀬? ?ㅻ? ???덈떎.

Snapshot???꾩슂??媛?

| ?꾨뱶 | ?섎? |
| --- | --- |
| `code` / `pdno` / `symbol` | 醫낅ぉ肄붾뱶. 肄붾뱶?먯꽌??6?먮━ 臾몄옄?대줈 ?뺢퇋?뷀븳?? |
| `holding_quantity` / `hldg_qty` / `quantity` | ?ㅼ젣 蹂댁쑀?섎웾. 肄붾뱶 寃利앹뿉???섎웾 鍮꾧탳???ъ슜?쒕떎. |
| `sellable_quantity` / `ord_psbl_qty` / `available_quantity` | 留ㅻ룄媛?μ닔?? plan preview/dry-run?먯꽌???놁쑝硫?蹂댁쑀?섎웾?쇰줈 fallback?섍퀬 warning???④만 ???덉?留? ?ㅼ젣 liquidation request ?앹꽦 ?④퀎?먯꽌???꾩닔?? |
| `generated_at` | snapshot ?앹꽦?쒓컖. plan preview/dry-run?먯꽌???놁쑝硫?warning???④만 ???덉?留? ?ㅼ젣 liquidation request ?앹꽦 ?④퀎?먯꽌???꾩닔?대ŉ ISO ?쒓컙 ?뚯떛怨?max age 寃利앹쓣 ?듦낵?댁빞 ?쒕떎. |
| name/price ??| ?덉쑝硫?UI/plan ?쒖떆??|

寃利?

1. DB OPEN LOT total quantity? DB position quantity瑜?怨꾩궛?쒕떎.
2. KIS holding_quantity? 鍮꾧탳?쒕떎.
3. sellable_quantity媛 留ㅻ룄 ?붿껌 ?섎웾 ?댁긽?몄? ?뺤씤?쒕떎.
4. open order/pending manual SELL/SYNC_REQUIRED/lot mismatch媛 ?덉쑝硫?李⑤떒?쒕떎.
5. 議고쉶 ?ㅽ뙣 ?먮뒗 snapshot 留뚮즺硫?request ?앹꽦 湲덉?.

Execution raw mapping? 泥닿껐?댁뿭 ?꾨뱶 寃利앹슜?대떎. ?꾨웾留ㅻ룄 snapshot怨?紐⑹쟻???ㅻⅤ?? raw mapping? order_no, execution_id, filled_at, side, code, price, quantity媛 ?ㅼ뼱?ㅻ뒗吏 ?뺤씤?섍퀬 誘쇨컧?뺣낫媛 留덉뒪?밸릺?붿? 蹂몃떎.

## 21. Config 援ъ“

?듭떖 ?뱀뀡:

| ?뱀뀡 | ?댁슜 | ?꾪뿕 ?ㅼ젙 |
| --- | --- | --- |
| `stocks` | 100媛??꾨낫援? enabled/manual_only/risk flags/sector/note | risk flag true?대㈃ RISK_BLOCKED ?꾨낫 |
| `strategy` | LOT sizing, add bands, target bands, reentry, cleanup, stale/review | cleanup_enabled, lot sizing disabled band |
| `risk` | ?꾩뿭 怨꾩쥖 ?쒗븳, profile, max_open_lots, invested amount, daily limits | max 媛?蹂寃쎌? ?몄텧 吏곸젒 ?곹뼢 |
| `order` | live_trading, price sample, limit order, raw log, reconciliation | live_trading/emergency/cancel/raw log ?꾪뿕 |
| `market_hours` | ?μ쨷/?μ쟾/?λ쭏媛?李⑤떒 ?쒓컙 | 留ㅻℓ 媛???쒓컙 ?곹뼢 |
| `kis_account` | env key ?대쫫留????| ?ㅼ젣 怨꾩쥖踰덊샇/API ???몄텧 湲덉? |
| `upstream_watch` | repo update watcher | ?먮룞 肄붾뱶 蹂寃?二쇱쓽 |
| `ui_manual_trading_enabled` | UI ?섎룞 二쇰Ц ?붿껌 ?앹꽦 ?덉슜 ?щ? | 湲곕낯 false ?좎? 沅뚯옣 |

Config ???UX??backup, validation, diff, atomic write, round-trip verify, change history瑜?嫄곗튇?? raw JSON? 怨좉툒 蹂닿린濡??좎??섎릺 湲곕낯? ??ぉ蹂?form?대떎.

## 22. Logs / Decision log

以묒슂 濡쒓렇 ?꾨뱶:

| ?꾨뱶/?대깽??| ?섎? |
| --- | --- |
| `action_created` | strategy媛 BUY/SELL ?꾨낫 action??留뚮뱾?덈뒗吏 |
| `action_blocked_before_request` | main guard?먯꽌 二쇰Ц ?붿껌 ??李⑤떒 |
| `action_execution_state` | ?앹꽦/李⑤떒/?붿껌/?ㅽ뙣 ??理쒖쥌 ?ㅽ뻾?곹깭 |
| `final_block_reason`, `skip_reason` | ?ㅼ젣濡?二쇰Ц???섍?吏 ?딆? 理쒖쥌 ?댁쑀 |
| `pnl_mode`, `reference_buy_price`, `open_lot_vwap_buy_price`, `median_open_buy_price` | 異붽?留ㅼ닔 ?먮떒 湲곗? |
| `lot_unit_amount`, `max_symbol_amount`, `max_lots_per_symbol`, `lot_sizing_bucket`, `entry_price_for_lot_sizing`, `lot_sizing_locked_at` | cycle sizing 異붿쟻 |
| `add_buy_lot_band`, `current_open_lot_count` | 異붽?留ㅼ닔 band 異붿쟻 |
| `target_profit_lot_band`, `current_base_target_profit_rate`, `effective_target_profit_rate` | ?숈쟻 sell target 異붿쟻 |
| `review_reason`, `review_trigger_values` | REVIEW_REQUIRED ?댁쑀 |
| `manual_order_request_*` | ?섎룞 二쇰Ц preview/created/blocked/submitted/failed |
| `liquidation_plan_*`, `reset_*` | ???쒖쫵 以鍮??꾨웾留ㅻ룄/珥덇린??guard |
| `kis_raw_executions` | raw execution sample/mapping. raw log ?듭뀡 true?먯꽌留?|
| `dedupe_key_type` | execution_id/fallback dedupe 援щ텇 |

濡쒓렇留?蹂닿퀬 ?쒖솢 ??붿?/??????붿?/???붿븯?붿?/??留됲삍?붿???異붿쟻?섎뒗 寃껋씠 UI/濡쒓렇 ?ㅺ퀎 紐⑺몴??

## 23. ?뚯뒪???꾪솴

理쒖떊 ?뺤씤 湲곗? ?꾩껜 ?뚯뒪?몃뒗 ?ㅼ쓬 紐낅졊?먯꽌 `155 passed`??? pytest cache warning 1媛쒕뒗 basetemp/cache write 愿?⑥씠硫?湲곕뒫 ?ㅽ뙣媛 ?꾨땲??

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

?뚯뒪??踰붿＜:

| 踰붿＜ | 寃利??댁슜 |
| --- | --- |
| LOT sizing | 媛寃⑸?蹂?band, cycle lock, migration, manual preview mismatch |
| add buy lot bands | 1~2/3~4/5~6/7~8/9~10 LOT band, 9->10 ?덉슜, 10 李⑤떒 |
| target profit dynamic | ?꾩옱 OPEN LOT ??湲곗? target, ?쇰? 留ㅻ룄 ??target ?ш퀎??|
| REVIEW_REQUIRED | 吏꾩엯, recheck, acknowledge, sync mismatch ??SYNC_REQUIRED |
| manual order requests | UI/API request ?앹꽦, guard, Bot Core ?뚮퉬, fill ??positions 遺덈? |
| UI ?쒓????뺣젹/而щ읆 | label/key 蹂묎린, column ?좏깮, width resize/localStorage |
| Prepare New Season | archive, liquidation plan freshness, reset guard, no KIS order API |
| reconciliation/dedupe | execution_id/fallback dedupe, duplicate count, raw mapping mock |
| global risk limits | active/new buy/open lot/invested amount/day amount ?쒗븳 |

## 24. ?꾩옱 ?댁쁺 ?곹깭? ?ㅼ쓬 ?④퀎

?꾩옱 ?곹깭:

| ??ぉ | ?곹깭 |
| --- | --- |
| config profile | `expansion_100_safe` |
| KOSPI ?꾨낫援?| 100醫낅ぉ ?곸슜 |
| enabled/manual_only | enabled 97, disabled/manual_only 3 |
| OPEN LOT | ?꾩옱 DB???⑥븘 ?덉쑝硫?reset 李⑤떒???뺤긽 |
| DB reset | OPEN LOT???⑥븘 ?덉쑝硫?李⑤떒 |
| ?꾨웾留ㅻ룄 request | 理쒖떊 KIS balance snapshot怨?liquidation plan ?꾩슂 |
| live_trading | false |
| cleanup_enabled | false |
| ui_manual_trading_enabled | false |
| enable_execution_raw_log | true |
| raw execution mapping | 泥??ㅼ껜寃?row 理쒖쥌 ?뺤씤 ?꾩슂 warning 媛??|

?ㅼ쓬 ?④퀎:

1. UI ???쒖쫵 ??뿉???꾩옱 李⑤떒 ?ъ쑀 ?뺤씤.
2. KIS balance snapshot 以鍮?
3. liquidation plan ?앹꽦.
4. ?꾨웾留ㅻ룄 request ?앹꽦 ?щ? 寃곗젙. confirm text ?꾩슂.
5. Bot Core媛 manual SELL request瑜?泥섎━?섎룄濡?遊??ㅽ뻾.
6. 泥닿껐/reconciliation ?꾨즺 ?뺤씤.
7. OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 ?뺤씤.
8. DB reset dry-run ??confirm reset.
9. ???쒖쫵 ?쒖옉 以鍮??꾨즺 ?뺤씤.
10. ?쒗븳 ?댁슜 ?쒖옉.
11. decision log濡?lot sizing/target profit ?뺤씤.
12. raw execution mapping ?뺤씤 ??`enable_execution_raw_log=false`濡?蹂듦뎄.

## 25. ?꾩쭅 ?섏? 留먯븘????寃?

| 湲덉? | ?댁쑀 |
| --- | --- |
| OPEN LOT ?⑥? ?곹깭?먯꽌 DB reset | ?ㅼ젣 蹂댁쑀/泥닿껐 ?대젰怨?DB媛 ?닿툔??|
| KIS snapshot ?놁씠 ?꾨웾留ㅻ룄 request ?앹꽦 | ?ㅼ젣 ?붽퀬 ?섎웾怨?DB ?섎웾 遺덉씪移??꾪뿕 |
| UI?먯꽌 KIS 二쇰Ц API 吏곸젒 ?몄텧 | 愿??UI媛 二쇰Ц湲곌? ?섎뒗 ?꾪뿕 |
| live_trading=true ?꾪솚 ???洹쒕え ?댁슜 | raw mapping/?꾨웾留ㅻ룄/reset 寃利????꾪뿕 |
| cleanup_enabled 利됱떆 true | 100醫낅ぉ 珥덇린 濡쒓렇/?숆린???덉젙?????먯떎 ?뺤젙 ?꾪뿕 |
| 100醫낅ぉ ?꾩껜 泥ル궇 臾댁젣??吏꾩엯 | max_new_buy/day amount ?쒗븳???고쉶?섎㈃ 怨쇰떎 ?몄텧 |
| pending manual request ?곹깭?먯꽌 reset | ?붿껌 泥섎━ 寃곌낵瑜??껋쓬 |
| DB/KIS mismatch ?곹깭?먯꽌 reset | SYNC_REQUIRED ?곹깭瑜?臾댁떆?섍쾶 ??|

## 26. ??thread?먯꽌 ?댁뼱媛湲??꾪븳 ?뺤씤 吏덈Ц

??梨꾪똿諛???Codex ?몄뀡?먯꽌 ?댁뼱媛???癒쇱? ?뺤씤??吏덈Ц:

1. ?꾩옱 DB??OPEN LOT??紐?媛??⑥븘 ?덈뒗媛?
2. KIS balance snapshot??以鍮꾪뻽?붽??
3. liquidation plan? ACTIVE?닿퀬 理쒖떊 DB/KIS snapshot怨??쇱튂?섎뒗媛?
4. reset 媛??議곌굔??留뚯”?섎뒗媛?
5. `live_trading`? false?멸??
6. `enable_execution_raw_log`??true?멸?, 泥??ㅼ껜寃?mapping ?뺤씤???앸궗?붽??
7. 理쒖떊 ?꾩껜 ?뚯뒪?몃뒗 ?듦낵?덈뒗媛?
8. ?꾩옱 config profile? `expansion_100_safe`?멸??
9. ?ъ슜?먭? ?먰븯???ㅼ쓬 ?묒뾽? UI 媛쒖꽑, ?ㅼ젣 ?꾨웾留ㅻ룄 以鍮? config ?쒕떇, 濡쒓렇 寃利?以?臾댁뾿?멸??
10. manual_order_requests??pending request媛 ?덈뒗媛?
11. SYNC_REQUIRED/REVIEW_REQUIRED/RISK_BLOCKED 醫낅ぉ???덈뒗媛?

## 27. ?뺤씤 ?꾩슂 / 蹂대쪟 / 二쇱쓽?ы빆

| ??ぉ | ?곹깭 | ?ㅻ챸 |
| --- | --- | --- |
| ?ㅼ젣 KIS raw execution field mapping | ?뺤씤 ?꾩슂 | mock怨??쇰? ?ㅻ줈洹?寃利앹? ?덉쑝??Dashboard warning 湲곗? 泥??ㅼ젣 raw row 理쒖쥌 ?뺤씤 ?꾩슂媛 ?⑥븘 ?덉쓣 ???덈떎. |
| KIS balance snapshot 理쒖떊 ?앹꽦 諛⑹떇 | ?뺤씤 ?꾩슂 | UI/script??snapshot path瑜?諛쏅뒗 援ъ“?? ?ㅼ젣 議고쉶 ?뚯씪 ?앹꽦/?좏깮 ?먮쫫? ?댁쁺 ?덉감濡??뺤씤?댁빞 ?쒕떎. |
| Execution Mapping Check ??| 蹂寃쎈맖 | nav ??? ?쒓굅?섏뿀怨?API/function? ?⑥븘 ?덈떎. ?꾩슂?섎㈃ ?ㅼ떆 ?몄텧 媛?? |
| Native Windows/Android ??| 蹂대쪟 | ?꾩옱 ?쒖? API??localhost Web UI/API. ?먭꺽 怨듦컻 湲덉?, VPN/secure relay 沅뚯옣. |
| cleanup ?먮룞??| 蹂대쪟/鍮꾪솢??| `cleanup_enabled=false`濡??쒖옉. 濡쒓렇 ?덉젙????蹂꾨룄 寃?? |
| DB maintenance mode | 蹂대쪟 | LOT/position 吏곸젒 ?섏젙 湲곕뒫? 湲곕낯 ?쒓났?섏? ?딅뒗?? |

## 28. ?ㅼ젣 ?ㅽ뻾 Runbook

???뱀뀡? ?댁쁺?먭? PowerShell?먯꽌 蹂듬텤???곕씪媛????덈뒗 ?덉감?? 紐⑤뱺 紐낅졊? ??μ냼 猷⑦듃 `C:\MSJ\KIS-MSJ`?먯꽌 ?ㅽ뻾?쒕떎. `kis_msj` 紐⑤뱢? ?꾩옱 ?뚯뒪 ?덉씠?꾩썐??`PYTHONPATH=src`媛 ?꾩슂?????덈떎.

### 28-1. UI ?ㅽ뻾

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | localhost 愿???ㅼ젙/?섎룞 ?붿껌/???쒖쫵 以鍮?UI ?ㅽ뻾 |
| ?ㅽ뻾 ??議곌굔 | ?ㅺ굅??二쇰Ц???대뒗 紐낅졊???꾨떂. UI??KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딅뒗?? |
| 紐낅졊 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765` |
| ?ㅽ뻾 ???뺤씤 | 釉뚮씪?곗??먯꽌 `http://127.0.0.1:8765` ?묒냽. Dashboard??`live_trading=false`, profile, OPEN LOT ???뺤씤 |
| ?덈? 湲덉? | ?몃? IP濡?怨듦컻, ?ы듃?ъ썙?? 怨꾩쥖/API key ?몄텧 |

### 28-2. 遊??ㅽ뻾

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | Bot Core ?먮룞 猷⑦봽 ?ㅽ뻾 |
| ?ㅽ뻾 ??議곌굔 | config, runtime pause, live_trading, OPEN order ?곹깭 ?뺤씤. ?ㅺ굅????異⑸텇??paper/mock ?뺤씤 ?꾩슂 |
| 1???ㅽ뻾 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.main --config config\lot_auto_trader.json --once --mock` |
| ?쇰컲 ?ㅽ뻾 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m kis_msj.main --config config\lot_auto_trader.json` |
| ?ㅽ뻾 ???뺤씤 | Logs, Orders/Fills, Manual Order Request, Dashboard warnings |
| ?덈? 湲덉? | ?섎룄 ?놁씠 `live_trading=true` ?곹깭?먯꽌 ?μ쨷 ?洹쒕え ?댁슜 ?쒖옉 |

### 28-3. ?뚯뒪???ㅽ뻾

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | ?꾩껜 ?뚭? ?뚯뒪??|
| 紐낅졊 | `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check` |
| 湲곕? 寃곌낵 | 理쒖떊 湲곗? `155 passed`. warning 1媛쒕뒗 pytest cache 愿?⑥씠硫?湲곕뒫 ?ㅽ뙣 ?꾨떂 |
| ?ㅽ뙣 ??| ?ㅽ뙣 ?뚯뒪???대쫫怨?愿???뚯씪??癒쇱? ?뺤씤. ?ㅺ굅??二쇰Ц?쇰줈 ?뺤씤?섎젮 ?섏? 留?寃?|

### 28-4. ???쒖쫵 ?곹깭 ?뺤씤

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | ???쒖쫵 以鍮?媛???щ?, 李⑤떒 ?ъ쑀 ?뺤씤 |
| UI | `New Season / ???쒖쫵` ??|
| API | `GET /api/new-season/status` |
| ?뺤씤 | OPEN LOT, pending order, pending manual request, SYNC_REQUIRED, plan status, reset_possible |
| ?덈? 湲덉? | OPEN LOT???⑥? ?곹깭?먯꽌 reset ?ㅽ뻾 |

### 28-5. archive ?앹꽦

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | config/DB/logs/exports瑜??댁쟾 ?쒖쫵 archive濡?蹂댁〈 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --dry-run` |
| ?ㅽ뻾 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --execute` |
| ?뺤씤 | `archive/reset_YYYYMMDD_HHMMSS/` ?꾨옒 config/db/logs/exports ?앹꽦 |
| ?덈? 湲덉? | archive ?놁씠 湲곗〈 DB/log/config ??젣 |

### 28-6. KIS balance snapshot 以鍮?寃利?

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | DB 蹂댁쑀?섎웾怨??ㅼ젣 怨꾩쥖 ?붽퀬 ?섎웾 鍮꾧탳 |
| ?낅젰 | JSON snapshot ?뚯씪. ?? `exports/kis_balance_snapshot_YYYYMMDD_HHMMSS.json` |
| ?뺤씤 | code/pdno/symbol, holding_quantity/hldg_qty/quantity, sellable_quantity/ord_psbl_qty/available_quantity, generated_at. dry-run? ?쇰? ?꾨씫??warning?쇰줈 ?덉슜?????덉?留??ㅼ젣 request ?앹꽦? strict 寃利앹쓣 ?듦낵?댁빞 ?쒕떎. |
| ?덈? 湲덉? | snapshot ?놁씠 ?꾨웾留ㅻ룄 request ?앹꽦 |

?꾩옱 援ы쁽? snapshot path瑜??낅젰諛쏆븘 寃利앺븳?? KIS 二쇰Ц API瑜??몄텧?섏? ?딅뒗?? 肄붾뱶 湲곗??쇰줈??KIS ?붽퀬 snapshot JSON???먮룞 ?앹꽦?섎뒗 湲곕뒫??`prepare_new_season.py`???녿떎. ?댁쁺?먮뒗 UI/CLI???섍만 JSON ?뚯씪??蹂꾨룄濡?以鍮꾪빐???쒕떎. plan preview/dry-run?먯꽌??`generated_at` ?먮뒗 `sellable_quantity` ?꾨씫??warning?쇰줈 ?쒖떆?????덉?留? ?ㅼ젣 manual SELL request ?앹꽦 ?④퀎?먯꽌??`generated_at` ?뚯떛, snapshot max age, ?ㅼ젣 留ㅻ룄媛?μ닔?됱쓣 strict 寃利앺븳??

### 28-7. liquidation plan ?앹꽦

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | DB OPEN LOT怨?KIS snapshot??湲곗??쇰줈 ?꾨웾留ㅻ룄 ?덉젙???앹꽦 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --dry-run` |
| ?ㅽ뻾 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --execute` |
| ?뺤씤 | `exports/liquidation_plan_*.json`, status, eligible_for_liquidation_request |
| ?덈? 湲덉? | ?덉쟾 plan??理쒖떊 寃利??놁씠 ?ъ궗??|

### 28-8. liquidation plan 蹂닿린/寃利?

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | plan??ACTIVE?몄?, DB hash/KIS snapshot??理쒖떊?몄? ?뺤씤 |
| UI | New Season ??뿉??plan status, block_reason, next_action ?뺤씤 |
| API | `GET /api/new-season/status` |
| 李⑤떒 ??| `liquidation_plan_db_changed`, `liquidation_plan_snapshot_expired`, `liquidation_kis_balance_mismatch` |

### 28-9. ?꾨웾留ㅻ룄 manual SELL request ?앹꽦

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | plan 湲곗? SELL request瑜?`manual_order_requests` ?먯뿉 ?앹꽦 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "?꾨웾留ㅻ룄 ?붿껌 ?뺤씤" --dry-run` |
| ?ㅽ뻾 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "?꾨웾留ㅻ룄 ?붿껌 ?뺤씤" --execute` |
| ?뺤씤 | Manual Order Request ??뿉 SELL REQUESTED ?앹꽦, Bot Core媛 ?댄썑 泥섎━ |
| ?덈? 湲덉? | script/UI媛 KIS 二쇰Ц API 吏곸젒 ?몄텧, fill ??lots/positions ?좊컲??|

### 28-10. DB reset dry-run / ?ㅽ뻾

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | OPEN LOT 0 ???덉쟾 議곌굔 異⑹” ??DB 珥덇린??媛???щ? ?뺤씤 |
| dry-run | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET ?뺤씤" --dry-run` |
| ?ㅽ뻾 | `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET ?뺤씤" --execute` |
| ?ㅽ뻾 ??議곌굔 | OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0, lot mismatch 0 |
| ?덈? 湲덉? | ?꾨웾留ㅻ룄/reconciliation ?꾨즺 ??reset |

### 28-11. raw execution log ?뺤씤 ???꾧린

| ??ぉ | ?댁슜 |
| --- | --- |
| 紐⑹쟻 | 泥??ㅼ껜寃?raw field mapping ?뺤씤 ??誘쇨컧 濡쒓렇 理쒖냼??|
| ?뺤씤??媛?| `has_order_no`, `has_filled_at`, `has_side`, `has_execution_id` ?먮뒗 fallback ?덉젙?? `dedupe_key_type`, masked sample |
| ?꾨뒗 ?덉감 | UI Config -> Order -> `enable_execution_raw_log=false` ???-> Runtime `Reset / Config ?ㅼ떆 ?쎄린` |
| ?덈? 湲덉? | raw log true瑜??κ린媛??좎??섍굅??誘쇨컧?뺣낫 ?먮Ц 異쒕젰 |

## 29. KIS Balance Snapshot ?곸꽭 ?щ㎎

KIS balance snapshot? ?쒖떎??怨꾩쥖 ?붽퀬 ?뺤씤 ?먮즺?앸떎. ???쒖쫵 ?꾨웾留ㅻ룄 request瑜?DB ?섎웾留뚯쑝濡?留뚮뱾硫??ㅼ젣 怨꾩쥖? ?ㅻ? ???덉쑝誘濡? DB OPEN LOT ?섎웾怨?KIS ?ㅼ젣 蹂댁쑀/留ㅻ룄媛???섎웾??鍮꾧탳?섍린 ?꾪빐 ?꾩슂?섎떎.

snapshot???놁쑝硫?liquidation request ?앹꽦? 留됲????쒕떎. ???block_reason? `liquidation_kis_balance_fetch_required` ?먮뒗 `liquidation_kis_balance_fetch_failed`??

吏???щ㎎? ?꾩옱 臾몄꽌 湲곗? JSON???쒖??쇰줈 ?붾떎. CSV瑜??곕젮硫?script/service?먯꽌 紐낆떆 吏???щ?瑜?癒쇱? ?뺤씤?댁빞 ?쒕떎.

### ?덉떆 JSON

```json
{
  "generated_at": "2026-05-26T15:30:00+09:00",
  "account_id_masked": "****1234",
  "source": "kis_balance_snapshot",
  "positions": [
    {
      "code": "005930",
      "name": "?쇱꽦?꾩옄",
      "holding_quantity": 3,
      "sellable_quantity": 3,
      "average_price": 70000,
      "current_price": 71000
    }
  ]
}
```

| ?꾨뱶 | ?꾩닔 | ?섎? |
| --- | --- | --- |
| `generated_at` | request ?앹꽦 ???꾩닔 | snapshot ?앹꽦 ?쒓컖. plan preview/dry-run?먯꽌??warning?쇰줈 ?덉슜 媛?ν븯吏留? ?ㅼ젣 request ?앹꽦 ?④퀎?먯꽌??ISO ?쒓컙 ?뚯떛怨?max age 寃利앹쓣 ?듦낵?댁빞 ?쒕떎. |
| `source` | 沅뚯옣 | `kis_balance_snapshot` ??異쒖쿂 ?쒖떆 |
| `account_id_masked` | ?좏깮 | 怨꾩쥖 ?앸퀎??留덉뒪??媛? ?먮Ц 怨꾩쥖踰덊샇 湲덉? |
| `positions[].code` / `pdno` / `symbol` | ?꾩닔 | 醫낅ぉ肄붾뱶. 6?먮━ 臾몄옄?대줈 ?뺢퇋??|
| `positions[].name` | 沅뚯옣 | 醫낅ぉ紐?|
| `positions[].holding_quantity` / `hldg_qty` / `quantity` | ?꾩닔 | ?ㅼ젣 怨꾩쥖 蹂댁쑀?섎웾 |
| `positions[].sellable_quantity` / `ord_psbl_qty` / `available_quantity` | request ?앹꽦 ???꾩닔 | ?ㅼ젣 留ㅻ룄媛?μ닔?? plan preview/dry-run?먯꽌???놁쑝硫?蹂댁쑀?섎웾 fallback + warning??媛?ν븯吏留? ?ㅼ젣 request ?앹꽦 ?④퀎?먯꽌???꾨씫 ??`liquidation_kis_sellable_quantity_missing`?쇰줈 李⑤떒?쒕떎. |
| `positions[].average_price` | ?좏깮 | ?ㅼ젣 怨꾩쥖 ?됯퇏?④?. DB LOT ?먮떒?먮뒗 吏곸젒 ?곗? ?딆쓬 |
| `positions[].current_price` | ?좏깮 | ?쒖떆/?덉긽湲덉븸 怨꾩궛??|

鍮꾧탳 諛⑹떇:

1. DB OPEN LOT quantity = `lots.remaining_quantity > 0 AND status != CLOSED` ?⑷퀎.
2. DB position total quantity???鍮꾧탳?쒕떎.
3. KIS `holding_quantity`? DB OPEN LOT quantity媛 ?ㅻⅤ硫?`liquidation_kis_balance_mismatch`.
4. KIS `sellable_quantity`媛 request ?섎웾蹂대떎 ?묒쑝硫?`liquidation_sellable_quantity_insufficient`.
5. snapshot??`generated_at`???놁쑝硫?request ?앹꽦 ?④퀎?먯꽌 `liquidation_kis_balance_snapshot_missing_generated_at`.
6. `generated_at` ?뚯떛 ?ㅽ뙣 ??`liquidation_kis_balance_snapshot_invalid_generated_at`.
7. snapshot age媛 max age瑜?珥덇낵?섎㈃ `liquidation_kis_balance_snapshot_stale`.
8. snapshot???ㅻ옒??plan?대㈃ `liquidation_plan_snapshot_expired`.

raw execution mapping怨쇱쓽 李⑥씠:

| ??ぉ | KIS balance snapshot | raw execution mapping |
| --- | --- | --- |
| 紐⑹쟻 | ?ㅼ젣 蹂댁쑀/留ㅻ룄媛???섎웾 ?뺤씤 | 泥닿껐?댁뿭 row ?꾨뱶紐?寃利?|
| ?ъ슜 ?쒖젏 | ?꾨웾留ㅻ룄 plan/request/reset ??| 泥??ㅼ껜寃???reconciliation 寃利?|
| ?듭떖 ?꾨뱶 | code/pdno/symbol, holding_quantity/hldg_qty/quantity, sellable_quantity/ord_psbl_qty/available_quantity, generated_at(request ?앹꽦 ???꾩닔) | order_no, execution_id, filled_at, side, code, price, quantity |
| ?놁쓣 ??| ?꾨웾留ㅻ룄 request 李⑤떒 | raw mapping warning ?좎? |

## 30. API Endpoint / Payload ?덉떆

紐⑤뱺 API??localhost UI ?쒕쾭 湲곗??대떎. UI ?쒕쾭??KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딅뒗??

| API | 紐⑹쟻 | KIS 二쇰Ц API ?몄텧 | lots/positions/fills 吏곸젒 蹂寃?|
| --- | --- | --- | --- |
| `GET /api/status` | Dashboard ?꾩껜 ?곹깭 | ?꾨땲??| ?꾨땲??|
| `GET /api/stocks` | 醫낅ぉ 紐⑸줉 | ?꾨땲??| ?꾨땲??|
| `GET /api/lots` | LOT 紐⑸줉 | ?꾨땲??| ?꾨땲??|
| `GET /api/orders` | 二쇰Ц 紐⑸줉 | ?꾨땲??| ?꾨땲??|
| `GET /api/fills` | 泥닿껐 紐⑸줉 | ?꾨땲??| ?꾨땲??|
| `GET /api/manual-order-requests` | ?섎룞 ?붿껌 紐⑸줉 | ?꾨땲??| ?꾨땲??|
| `POST /api/manual-orders/preview` | ?섎룞 二쇰Ц 誘몃━蹂닿린 | ?꾨땲??| ?꾨땲??|
| `POST /api/manual-orders` | manual_order_requests row ?앹꽦 | ?꾨땲??| ?꾨땲??|
| `GET /api/review-required` | REVIEW_REQUIRED 紐⑸줉 | ?꾨땲??| ?꾨땲??|
| `GET /api/positions/{code}/review-status` | ?뱀젙 醫낅ぉ review ?곹깭 | ?꾨땲??| ?꾨땲??|
| `POST /api/positions/{code}/review/recheck` | review 議곌굔 ?ы룊媛 | ?꾨땲??| position_state/review field??諛붾????덉쓬 |
| `POST /api/positions/{code}/review/acknowledge` | ?ъ슜???뺤씤 湲곕줉 | ?꾨땲??| review ack ?꾨뱶留?|
| `GET /api/new-season/status` | ???쒖쫵 wizard ?곹깭 | ?꾨땲??| ?꾨땲??|
| `POST /api/new-season/archive` | archive dry-run/?ㅽ뻾 | ?꾨땲??| DB ?댁슜 蹂寃??놁쓬 |
| `POST /api/new-season/validate-snapshot` | KIS balance snapshot JSON 寃利?| ?꾨땲??| ?꾨땲??|
| `POST /api/new-season/liquidation-plan` | liquidation plan ?앹꽦 | ?꾨땲??| DB ?댁슜 蹂寃??놁쓬 |
| `POST /api/new-season/liquidation-requests` | manual SELL request ?앹꽦 | ?꾨땲??| manual_order_requests留?|
| `POST /api/new-season/reset-db` | DB reset dry-run/?ㅽ뻾 | ?꾨땲??| execute+confirm+guard ?듦낵 ??DB 珥덇린??|

Snapshot strict validation:

- `POST /api/new-season/liquidation-plan`: preview/plan ?앹꽦 紐⑹쟻?대떎. `generated_at` ?먮뒗 `sellable_quantity` ?꾨씫? plan??`snapshot_warnings`, `request_creation_allowed=false`, `request_creation_block_reason`?쇰줈 ?④만 ???덈떎.
- `POST /api/new-season/liquidation-requests`: ?ㅼ젣 manual SELL request ?앹꽦 吏곸쟾 strict 紐⑤뱶?? `generated_at` ?꾨씫/?뚯떛 ?ㅽ뙣/age 珥덇낵, `sellable_quantity` ?꾨씫/遺議깆? request ?앹꽦??李⑤떒?쒕떎.

### ?섎룞 BUY preview ?덉떆

```json
{
  "code": "005930",
  "side": "BUY",
  "amount": 30000,
  "requested_by": "local_ui",
  "confirm_text": ""
}
```

?듭떖 response ?꾨뱶:

- `allowed`
- `block_reasons`
- `quantity`
- `estimated_amount`
- `current_price`
- `lot_sizing_bucket`
- `lot_unit_amount`
- `max_symbol_amount`
- `runtime_snapshot`

### ?섎룞 二쇰Ц request ?앹꽦 ?덉떆

```json
{
  "code": "005930",
  "side": "BUY",
  "amount": 30000,
  "requested_by": "local_ui",
  "confirm_text": "?섎룞二쇰Ц ?뺤씤"
}
```

response ?듭떖:

- ?앹꽦 ?깃났 ??`request_id`, `status=REQUESTED`
- 李⑤떒 ??`created=false`, `block_reason` ?먮뒗 `block_reasons`

二쇱쓽: ??API??KIS 二쇰Ц???댁? ?딄퀬 ?먮쭔 留뚮뱺?? ?ㅼ젣 二쇰Ц? ?ㅽ뻾 以묒씤 Bot Core媛 泥섎━?쒕떎.

### REVIEW recheck ?덉떆

```json
{}
```

`POST /api/positions/005930/review/recheck` ?먮뒗 `POST /api/review-required/005930/recheck`.

?듭떖 response:

- `cleared`
- `state`
- `remaining_reasons`
- `sync_required`

### REVIEW acknowledge ?덉떆

```json
{
  "acknowledged_by": "local_ui",
  "note": "?섎룞留ㅻ룄 ???ы솗???덉젙"
}
```

acknowledge???뺤씤 湲곕줉留??④린硫?BUY 李⑤떒 ?댁젣媛 ?꾨땲??

### New Season liquidation plan ?덉떆

```json
{
  "execute": true,
  "kis_balance_json_path": "exports/kis_balance_snapshot_20260526_153000.json",
  "max_age_minutes": 60
}
```

### New Season liquidation request ?덉떆

```json
{
  "execute": true,
  "plan_path": "exports/liquidation_plan_20260526_153500_xxxx.json",
  "kis_balance_json_path": "exports/kis_balance_snapshot_20260526_153000.json",
  "confirm": "?꾨웾留ㅻ룄 ?붿껌 ?뺤씤"
}
```

### New Season reset ?덉떆

```json
{
  "execute": true,
  "confirm": "RESET ?뺤씤"
}
```

reset? ?꾪뿕 ?숈옉?대떎. OPEN LOT, pending order, pending manual request, sync mismatch媛 ?덉쑝硫?李⑤떒?섏뼱???쒕떎.

## 31. Troubleshooting / ?곹솴蹂???묓몴

| 利앹긽 | 媛?ν븳 ?먯씤 | ?뺤씤??UI/API/log | ?섎㈃ ?섎뒗 議곗튂 | ?섎㈃ ???섎뒗 議곗튂 |
| --- | --- | --- | --- | --- |
| `request_creation_possible=false` | plan ?놁쓬, snapshot ?놁쓬, plan stale, pending work | New Season, `/api/new-season/status` | ?쒓? block guide???ㅼ쓬 ?됰룞 ?섑뻾 | ?대? flag留?蹂닿퀬 媛뺤젣 吏꾪뻾 |
| `liquidation_plan_missing` | ?꾨웾留ㅻ룄 ?덉젙???놁쓬 | New Season | KIS snapshot 以鍮???plan ?앹꽦 | ?덉쟾 plan ?꾩쓽 吏??|
| `liquidation_plan_stale` | plan max age 珥덇낵 | New Season | 理쒖떊 snapshot/DB 湲곗? plan ?ъ깮??| ?ㅻ옒??plan?쇰줈 request ?앹꽦 |
| `liquidation_plan_db_changed` | plan ?앹꽦 ??OPEN LOT 蹂寃?| New Season | plan ?ъ깮??| 湲곗〈 plan ?ъ궗??|
| `liquidation_kis_balance_fetch_required` | KIS balance snapshot ?놁쓬 | New Season | snapshot 以鍮??좏깮 | DB ?섎웾留?誘욧퀬 ?꾨웾留ㅻ룄 |
| `liquidation_kis_balance_mismatch` | DB LOT ?섎웾怨?KIS 蹂댁쑀?섎웾 遺덉씪移?| Reconciliation, New Season | reconciliation/sync ?뺤씤 | reset ?먮뒗 ?꾨웾留ㅻ룄 媛뺥뻾 |
| `reset_open_lot_exists` | OPEN LOT ?⑥븘 ?덉쓬 | Lots, New Season | ?꾨웾留ㅻ룄/reconciliation ?꾨즺 | DB reset |
| `reset_pending_order_exists` | 吏꾪뻾 以?二쇰Ц ?덉쓬 | Orders/Fills | 二쇰Ц 泥닿껐/痍⑥냼/嫄곗젅 醫낃껐 ?湲?| 二쇰Ц 臾댁떆?섍퀬 reset |
| `reset_pending_manual_request_exists` | 誘몄쿂由??섎룞 ?붿껌 ?덉쓬 | Manual Order Request | ?붿껌 泥섎━ ?꾨즺 ?湲?| request row ??젣濡??고쉶 |
| `reset_sync_required` | DB/KIS ?숆린???꾩슂 | Dashboard, Review, Reconciliation | reconciliation ?곗꽑 | ?곹깭 媛뺤젣 蹂寃?|
| SYNC_REQUIRED ?곹깭 | ?ㅼ젣 ?붽퀬? DB 遺덉씪移?| Stocks/Review/Reconciliation | 泥닿껐/?붽퀬 ?숆린???뺤씤 | ?좉퇋 二쇰Ц |
| REVIEW_REQUIRED ?곹깭 | ?먯떎/LOT怨쇰떎/stale/cleanup ?꾨즺 | Review ??| reason ?뺤씤, ?섎룞留ㅻ룄/ack/recheck | 媛뺤젣 HOLDING 蹂寃?|
| RISK_BLOCKED ?곹깭 | risk flag true | Stocks risk flags | ?ъ쑀 ?뺤씤, ?꾩슂 ??config flag 議곗젙 | SELL ?덉슜 ?뺤콉 ?꾩쓽 蹂寃?|
| `lot_sizing_changed_after_preview` | preview ??媛寃?援ш컙 蹂寃?| Manual request log | ?ㅼ떆 preview | ?댁쟾 preview濡?二쇰Ц |
| `max_lots_per_symbol_reached` | 醫낅ぉ LOT ???곹븳 ?꾨떖 | Decision log, Lots | 異붽?留ㅼ닔 以묐떒/?섎룞寃??| ?곹븳 ?고쉶 |
| `max_symbol_amount_reached` | 醫낅ぉ蹂?湲덉븸 ?곹븳 ?꾨떖 | Decision log | ?몄텧 異뺤냼/寃??| max留??꾩쓽 ?뺣? |
| `max_new_buy_amount_per_day_reached` | ?섎（ ?좉퇋留ㅼ닔 湲덉븸 ?쒗븳 | Dashboard/Risk log | ?ㅼ쓬 嫄곕옒???湲?| ?쒗븳 利됱떆 ?댁젣 |
| `record_fill_failed` | 以묐났 fill ?먮뒗 insert ?ㅽ뙣 | Logs, Fills | dedupe_key_type ?뺤씤 | apply_fill ?섎룞 ?몄텧 |
| duplicate fill 利앷? | 議고쉶 踰붿쐞 ?뺣?/?ъ“??| Reconciliation log | new_fill_count? ?④퍡 ?뺤씤 | 以묐났?대씪怨?臾댁“嫄??ㅻ쪟 ?먮떒 |
| raw execution mapping warning | 泥??ㅼ젣 row 理쒖쥌 ?뺤씤 ??| Dashboard/Logs | raw log ?뺤씤 ???꾨뱶 寃利?| raw log ?κ린 諛⑹튂 |
| `enable_execution_raw_log=true` 怨꾩냽 ?좎? | 寃利???off ?꾨씫 | Config Order | false ?????config reload | 誘쇨컧 raw sample 諛⑹튂 |
| disabled/manual_only 醫낅ぉ ?쒖떆 | risk/master/event ?ъ쑀 | Stocks/Config | note/risk flag ?뺤씤 | enabled 臾댁“嫄?true |
| New Season ?ㅼ쓬 踰꾪듉 鍮꾪솢??| ?댁쟾 ?④퀎 議곌굔 誘몄땐議?| New Season guidance | ?쒖떆???ㅼ쓬 ?됰룞 ?섑뻾 | disabled 踰꾪듉 ?고쉶 |

## 32. ???쒖쫵 以鍮?UI ?ъ슜???ㅻ챸

???쒖쫵 ??뿉 ?ㅼ뼱媛硫?癒쇱? ?ㅼ쓬??蹂몃떎.

1. OPEN LOT ??
2. 誘몄껜寃?二쇰Ц ??
3. 誘몄쿂由?manual request ??
4. SYNC_REQUIRED / lot mismatch ?щ?
5. liquidation plan 議댁옱/?곹깭/留뚮즺 ?щ?
6. KIS balance snapshot ?꾩슂 ?щ?
7. reset 媛???щ?

OPEN LOT???⑥븘 ?덉쑝硫?reset??留됲엺?? DB 珥덇린?붾뒗 ?쒓린濡??뺣━?앷? ?꾨땲???대? 蹂댁쑀 ?곹깭瑜??덈줈 ?쒖옉?섎뒗 ?됱쐞?대?濡? ?ㅼ젣 怨꾩쥖??蹂댁쑀媛 ?⑥븘 ?덇굅??泥닿껐 ?숆린?붽? ?앸굹吏 ?딆븯?붾뜲 reset?섎㈃ ?댄썑 紐⑤뱺 二쇰Ц ?먮떒???꾪뿕?댁쭊??

dry-run? ?ㅽ뻾 ??誘몃━蹂닿린?? archive, liquidation plan, liquidation requests, reset 紐⑤몢 dry-run?쇰줈 癒쇱? ?뺤씤?섍퀬, ?ㅼ젣 ?ㅽ뻾? confirm怨?guard瑜??듦낵?댁빞 ?쒕떎.

liquidation plan? ?꾨웾留ㅻ룄 ?덉젙?쒕떎. 怨좎젙 臾몄꽌媛 ?꾨땲???꾩옱 DB ?곹깭? ?꾩옱 KIS balance snapshot 湲곗??쇰줈 留ㅻ쾲 ?덈줈 怨꾩궛?섏뼱???쒕떎.

Plan status ?섎?:

| status | ?ъ슜???ㅻ챸 |
| --- | --- |
| ACTIVE | ?꾩옱 ?덉젙?쒓? ?좏슚?섎떎 |
| EXPIRED | ?ㅻ옒?섏뼱 ?덈줈 留뚮뱾?댁빞 ?쒕떎 |
| SUPERSEDED | ??理쒖떊 ?덉젙?쒓? ?덉뼱 ?ъ슜?????녿떎 |
| USED | ?대? ?꾨웾留ㅻ룄 request ?앹꽦???ъ슜?먮떎 |
| BLOCKED | 李⑤떒 ?ъ쑀媛 ?덉뼱 ?ъ슜?????녿떎 |

?꾨웾留ㅻ룄 ?붿껌 ?앹꽦 踰꾪듉??鍮꾪솢?깆씤 ????먯씤:

- KIS balance snapshot ?놁쓬
- liquidation plan ?놁쓬
- plan 留뚮즺
- plan ?앹꽦 ??OPEN LOT 蹂寃?
- 誘몄껜寃?二쇰Ц 議댁옱
- pending manual request 議댁옱
- SYNC_REQUIRED ?먮뒗 lot mismatch

DB 珥덇린??踰꾪듉??鍮꾪솢?깆씤 ????먯씤:

- OPEN LOT???⑥븘 ?덉쓬
- 誘몄껜寃?二쇰Ц???덉쓬
- 誘몄쿂由?manual request媛 ?덉쓬
- DB? KIS ?붽퀬媛 ?쇱튂?섏? ?딆쓬
- ?꾨웾留ㅻ룄 泥닿껐/reconciliation???꾨즺?섏? ?딆쓬

???쒖쫵 ?쒖옉 以鍮??꾨즺 議곌굔:

- archive ?꾨즺
- ?꾨웾留ㅻ룄/reconciliation ?꾨즺
- OPEN LOT 0
- 吏꾪뻾 以?order 0
- pending manual request 0
- SYNC_REQUIRED 0
- lot mismatch 0
- expansion_100_safe/KOSPI 100 config ?뺤씤

## 33. REVIEW_REQUIRED 泥섎━ ?곸꽭 ?щ?

### ?щ? 1. ?먯떎瑜?-20% ?댄븯

| ??ぉ | ?ㅻ챸 |
| --- | --- |
| ??諛쒖깮 | 醫낅ぉ ?꾩껜 ?됯??먯떎瑜좎씠 review threshold ?댄븯濡??대젮媛?|
| UI ?꾩튂 | Review ?? Stocks ?곸꽭, Lots |
| 媛?ν븳 議곗튂 | 異붽?留ㅼ닔 以묐떒, ?섏씡沅?LOT留??뺣━, ?꾩슂 ???섎룞 SELL request |
| recheck ?쒖젏 | ?섎룞留ㅻ룄/reconciliation ???먯떎瑜좎씠 湲곗? ?댁긽?쇰줈 ?뚮났?먯쓣 ??|
| acknowledge | ?ъ슜?먭? ?곹솴???뺤씤?덇퀬 異붿쟻 硫붾え留??④만 ??|
| 湲덉? | 議곌굔???⑥븘 ?덈뒗??媛뺤젣 HOLDING ?꾪솚 |

### ?щ? 2. OPEN LOT ??10媛?珥덇낵

| ??ぉ | ?ㅻ챸 |
| --- | --- |
| ??諛쒖깮 | current_open_lot_count媛 ?덉슜 踰붿쐞瑜??섏쓬 |
| UI ?꾩튂 | Review ?? Lots, Dashboard risk |
| 媛?ν븳 議곗튂 | PROFIT_TAKE 媛?ν븳 LOT ?뺣━, ?섎룞留ㅻ룄 ??reconciliation |
| recheck ?쒖젏 | OPEN LOT ?섍? ?쒗븳 ?댄븯濡?以꾩뼱????|
| 湲덉? | max_lots留??ㅼ썙 ?먮룞留ㅼ닔瑜?利됱떆 ?ш컻 |

### ?щ? 3. ?ㅻ옒??STALE LOT

| ??ぉ | ?ㅻ챸 |
| --- | --- |
| ??諛쒖깮 | ?먯떎瑜? ?섏씠, 媛寃?愿대━ 議곌굔???ㅻ옒 留뚯” |
| UI ?꾩튂 | Review ?? Lots stale filter |
| 媛?ν븳 議곗튂 | 愿李? ?섏씡沅?LOT ?뺣━, cleanup_enabled ?뺤콉 ?ш???|
| recheck ?쒖젏 | stale 議곌굔 ?댁냼 ?먮뒗 LOT ?뺣━ ??|
| 二쇱쓽 | STALE_LOT? 利됱떆 ?먯젅 ?좏샇媛 ?꾨땲??|

### ?щ? 4. ?섎룞留ㅻ룄 ?꾩뿉??REVIEW_REQUIRED ?좎?

| ??ぉ | ?ㅻ챸 |
| --- | --- |
| ??諛쒖깮 | ?먯떎瑜?LOT ??stale 議곌굔???꾩쭅 ?⑥븘 ?덉쓬 |
| UI ?꾩튂 | Review ??remaining reasons |
| 媛?ν븳 議곗튂 | ?⑥? reason???뺤씤?섍퀬 異붽? 議곗튂 ?먮뒗 acknowledge |
| recheck ?쒖젏 | 泥닿껐怨?reconciliation ?꾨즺 ??|
| 湲덉? | ?쒕ℓ?꾪뻽?쇰땲 臾댁“嫄??댁젣?앸줈 媛뺤젣 蹂寃?|

### ?щ? 5. ?섎룞留ㅻ룄 ??SYNC_REQUIRED濡?蹂寃?

| ??ぉ | ?ㅻ챸 |
| --- | --- |
| ??諛쒖깮 | DB? ?ㅼ젣 KIS ?붽퀬媛 ?쇱튂?섏? ?딆쓬. 泥닿껐 諛섏쁺 ?꾩씠嫄곕굹 ?섎룞留ㅻℓ/partial mismatch 媛??|
| UI ?꾩튂 | Dashboard warning, Review, Reconciliation, Orders/Fills |
| 媛?ν븳 議곗튂 | reconciliation ?꾨즺, fills/lots/positions/KIS ?붽퀬 鍮꾧탳 |
| recheck ?쒖젏 | sync mismatch媛 ?щ씪吏???|
| 湲덉? | SYNC_REQUIRED瑜?臾댁떆?섍퀬 BUY ?ш컻 |

## 34. Config ?ㅼ젣媛???蹂닿컯

### price_lot_bands

| 媛寃?援ш컙 | 1 LOT | 醫낅ぉ??理쒕? | enabled | ?꾪뿕/?곹뼢 |
| --- | ---: | ---: | --- | --- |
| 0~300 | 0 | 0 | false | 珥덉?媛 ?먮룞留ㅼ닔 ?쒖쇅 |
| 301~1,000 | 3,000 | 30,000 | true | ?뚯븸 LOT |
| 1,001~10,000 | 10,000 | 100,000 | true | 以묒?媛 |
| 10,001~30,000 | 30,000 | 300,000 | true | 湲곗〈 3留뚯썝 LOT 湲곗? |
| 30,001~100,000 | 100,000 | 1,000,000 | true | 怨좉? 吏꾩엯??利앷? |
| 100,001~300,000 | 300,000 | 3,000,000 | true | ??LOT, 珥앹븸 ?쒗븳 二쇱쓽 |
| 300,001~1,000,000 | 1,000,000 | 3,000,000 | true, max_lots=3 | 留ㅼ슦 ??LOT |
| 1,000,001 ?댁긽 | 0 | 0 | false | ?먮룞留ㅼ닔 ?쒖쇅/manual only |

### add_buy_lot_bands

| LOT 援ш컙 | drop_rate | add_lot_count | 蹂寃??곹뼢 |
| --- | ---: | ---: | --- |
| 1~2 | 4% | 1 | 珥덈컲 臾쇳?湲?媛꾧꺽 |
| 3~4 | 6% | 1 | 以묎컙 ?몄텧 議곗젅 |
| 5~6 | 8% | 1 | 蹂댁닔??|
| 7~8 | 10% | 1 | ??蹂댁닔??|
| 9~10 | 12% | 1 | 留덉?留??먮룞 異붽?留ㅼ닔 援ш컙 |

### target_profit_lot_bands

| LOT 援ш컙 | target_profit_rate | 蹂寃??곹뼢 |
| --- | ---: | --- |
| 1~2 | 6% | ??? ?몄텧?먯꽌???믪? ?뚯쟾 紐⑺몴 |
| 3~4 | 5% | ?몄텧 利앷? ??紐⑺몴 ?꾪솕 |
| 5~6 | 4% | 湲곗〈 LOT???숈쟻 ?곸슜 |
| 7~8 | 3% | ?ъ???異뺤냼 ?곗꽑 |
| 9~10 | 2% | 怨좊끂異?援ш컙 ?뚯쟾 ?곗꽑 |

### order ?꾪뿕 ?ㅼ젙

| ??ぉ | ?꾩옱媛?| ?섎? | ?꾪뿕 | UI 蹂寃?|
| --- | --- | --- | --- | --- |
| `live_trading` | false | true?대㈃ ?ㅼ젣 二쇰Ц 媛??| 留ㅼ슦 ?믪쓬 | 媛?? ?댁쨷 ?뺤씤 ?꾩슂 |
| `emergency_market_order` | config ?뺤씤 ?꾩슂 | 鍮꾩긽 ?쒖옣媛 愿???ㅼ젙 | 留ㅼ슦 ?믪쓬 | 媛?ν븯??媛뺢꼍怨?|
| `enable_execution_raw_log` | true | raw execution sample logging | 誘쇨컧?뺣낫 二쇱쓽 | 媛?? 寃利???false 沅뚯옣 |
| `cancel_unfilled_on_start` | config ?뺤씤 ?꾩슂 | ?쒖옉 ??誘몄껜寃?痍⑥냼 | ?ㅺ굅???곹뼢 | 媛?ν븯??媛뺢꼍怨?|

### cleanup ?ㅼ젙

| ??ぉ | ?꾩옱媛?| ?섎? | ?꾪뿕 |
| --- | --- | --- | --- |
| `cleanup_enabled` | false | ?먯떎 ?뺣━ ?먮룞留ㅻ룄 ?덉슜 | true ?꾪솚 ???먯떎 ?뺤젙 媛??|
| `cleanup_min_target_rate` | 湲곕낯 -4% 怨꾩뿴 | 理쒕? ?먯떎 ?뺣━ ?덉슜瑜?| ?덈Т ??텛硫??먯떎 ?뺣? |
| `cleanup_profit_offset_ratio` | config ?뺤씤 ?꾩슂 | ?뱀씪 ?ㅽ쁽?섏씡 以?cleanup budget 鍮꾩쑉 | ?먯떎 ?곸뇙 洹쒕え |
| `cleanup_buy_cooldown_days` | config ?뺤씤 ?꾩슂 | cleanup ??BUY cooldown, calendar days | 嫄곕옒??湲곗? ?꾨떂 |
| `cleanup_reentry_cooldown_days` | config ?뺤씤 ?꾩슂 | ?꾨웾 cleanup ??review ?꾪솚 ?湲?| 嫄곕옒??湲곗? ?꾨떂 |

### runtime/manual ?ㅼ젙

| ??ぉ | ?꾩옱媛?| ?섎? | ?꾪뿕 |
| --- | --- | --- | --- |
| `ui_manual_trading_enabled` | false | UI manual request ?앹꽦 ?덉슜 | true?щ룄 UI 吏곸젒 二쇰Ц ?놁쓬 |
| `all_orders_paused` | runtime ?뚯씪 湲곗? | 紐⑤뱺 二쇰Ц ?붿껌 李⑤떒 | ?덉쟾?μ튂 |
| `buy_paused` | runtime ?뚯씪 湲곗? | BUY 李⑤떒 | ?덉쟾?μ튂 |
| `sell_paused` | runtime ?뚯씪 湲곗? | SELL 李⑤떒 | ?섏씡?ㅽ쁽??留됱쓣 ???덉쓬 |
| `cleanup_paused` | runtime ?뚯씪 湲곗? | CLEANUP_SELL 李⑤떒 | ?먯떎 ?뺣━ 以묐떒 |
| `reentry_paused` | runtime ?뚯씪 湲곗? | ?ъ쭊??BUY 李⑤떒 | ?ъ쭊??以묐떒 |

## 35. ???몄뀡 ?묒뾽 ???덈? 吏耳쒖빞 ???ㅽ뻾 湲덉?

??臾몄꽌瑜??섍꺼諛쏆? ??Codex/ChatGPT ?몄뀡? ?ㅼ쓬???ъ슜??紐낆떆 ?뺤씤 ?놁씠 ?ㅽ뻾?섎㈃ ???쒕떎.

- ?ㅺ굅??二쇰Ц
- KIS 二쇰Ц API ?몄텧
- DB reset ?ㅽ뻾
- OPEN LOT/fills/positions 吏곸젒 ??젣 ?먮뒗 ?섎웾 ?섏젙
- config `live_trading=true` ?꾪솚
- archive ?녿뒗 湲곗〈 DB/log/config ??젣
- manual request pending ?곹깭?먯꽌 reset

## 36. 臾몄꽌 ?뺥빀??self-check

理쒖쥌 臾몄꽌 ?뺥빀??湲곗?:

| 泥댄겕 ??ぉ | 湲곗? |
| --- | --- |
| authoritative source | `docs/project_handoff_full.md`媛 理쒖떊 ?꾩껜 湲곗??대떎. ?몃? 李멸퀬臾몄꽌? 異⑸룎?섎㈃ full 臾몄꽌瑜??곗꽑?쒕떎. |
| current state | full/summary/thread prompt 紐⑤몢 `expansion_100_safe`, 100醫낅ぉ, enabled 97, manual_only 3 湲곗??대떎. |
| risk profile | `risk.profile=expansion_100_safe`濡??듭씪?쒕떎. |
| ?듭떖 boolean | `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true`濡??듭씪?쒕떎. |
| reset 李⑤떒 議곌굔 | 吏꾪뻾 以?orders, 吏꾪뻾 以?manual requests, OPEN LOT, SYNC_REQUIRED, lot mismatch, KIS/DB mismatch瑜??ы븿?쒕떎. |
| manual order ?쒗쁽 | ?쏫IS 吏곸젒 二쇰Ц API ?놁쓬 / manual request ?앹꽦 API???덉쓬?앹쑝濡??듭씪?쒕떎. |
| CLI options | `scripts/prepare_new_season.py --help` 湲곗??쇰줈 `--config`, `--archive-root`, `--profile`, `--apply-config`, `--archive`, `--liquidation-plan`, `--create-liquidation-requests`, `--kis-balance-json`, `--liquidation-plan-file`, `--plan-max-age-minutes`, `--reset-db`, `--confirm`, `--dry-run`, `--execute`瑜??뺤씤?덈떎. 臾몄꽌?먯꽌 `--archive`???ㅼ젣 諛깆뾽 ?ㅽ뻾 ?뚮옒洹몄씠怨? archive root 吏?뺤? `--archive-root`媛 留욌떎. |
| API routes | `src/kis_msj/ui_server.py` 湲곗??쇰줈 `GET /api/status`, `/api/stocks`, `/api/lots`, `/api/orders`, `/api/fills`, `/api/manual-order-requests`, `POST /api/manual-orders/preview`, `POST /api/manual-orders`, review API, new-season API媛 議댁옱?⑥쓣 ?뺤씤?덈떎. ??`/api/manual-order-preview` ?쒓린???ъ슜?섏? ?딅뒗?? |
| KIS snapshot ?쒗쁽 | ?꾩옱 援ы쁽? snapshot JSON ?뚯씪???낅젰諛쏆븘 寃利앺븯??援ъ“?? `prepare_new_season.py`?먮뒗 KIS ?붽퀬 snapshot ?먮룞 ?앹꽦 湲곕뒫???놁쑝誘濡??댁쁺?먭? 蹂꾨룄 JSON??以鍮꾪빐???쒕떎. loader??`code/pdno/symbol`, `holding_quantity/hldg_qty/quantity`, `sellable_quantity/ord_psbl_qty/available_quantity`瑜?吏?먰븳?? preview/dry-run?먯꽌??`generated_at` ?먮뒗 `sellable_quantity` ?꾨씫??warning?쇰줈 ?덉슜?????덉?留? ?ㅼ젣 request ?앹꽦 ?④퀎?먯꽌???????꾩닔?? |
| Snapshot strict mode | 理쒖떊 蹂닿컯 湲곗??쇰줈 preview/dry-run? `generated_at` ?꾨씫, `sellable_quantity` ?꾨씫??warning?쇰줈 plan???④만 ???덈떎. ?ㅼ젣 liquidation request ?앹꽦 ?④퀎?먯꽌??`generated_at`怨??ㅼ젣 `sellable_quantity`媛 ?꾩닔?대ŉ, ?꾨씫/?뚯떛 ?ㅽ뙣/age 珥덇낵/留ㅻ룄媛?μ닔??遺議깆? 李⑤떒?쒕떎. |
| 以묐났/援щ쾭??臾몄꽌 | `docs` ?대뜑??二쇱슂 臾몄꽌 7媛쒕? ?뺤씤?덉쑝硫?媛숈? 紐⑹쟻??援щ쾭??以묐났 臾몄꽌??諛쒓껄?섏? 紐삵뻽?? |
| 臾몄꽌 留곹겕 | 二쇱슂 臾몄꽌 ?곷떒???곷? 留곹겕媛 ?ㅼ젣 ?뚯씪紐낃낵 ?쇱튂?⑥쓣 ?뺤씤?덈떎. |
| Runbook 紐낅졊??| ??μ냼 猷⑦듃 `C:\MSJ\KIS-MSJ`, ?꾩슂 ??`$env:PYTHONPATH='src'`, dry-run/execute 援щ텇, confirm text 紐낆떆. |
| ?뚯뒪??理쒖떊??| `155 passed`, pytest cache warning 1媛쒕뒗 湲곕뒫 ?ㅽ뙣 ?꾨떂. ?ㅽ뻾 ?쒖젏?먮뒗 ?ㅼ떆 ?뺤씤 ?꾩슂. |

臾몄꽌 ?묒꽦 踰붿쐞?먯꽌???ㅺ굅??二쇰Ц, KIS 二쇰Ц API, DB reset???ㅽ뻾?섏? ?딆븯??

