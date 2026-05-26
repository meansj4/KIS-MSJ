# KIS LOT ?먮룞嫄곕옒 遊??몄닔?멸퀎 ?붿빟

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

Last updated: 2026-05-26  
湲곗? ?뚯뒪??寃곌낵: `155 passed` (`.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check`)  
湲곗? config profile: `expansion_100_safe`  
二쇱쓽: ?ㅼ젣 媛믪? ?ㅽ뻾 ?쒖젏??config/DB/log/KIS 怨꾩쥖 ?곹깭瑜??ㅼ떆 ?뺤씤?댁빞 ?쒕떎.

???꾨줈?앺듃??`C:\MSJ\KIS-MSJ`??KIS API 湲곕컲 KOSPI LOT ?⑥쐞 ?먮룞留ㅻℓ 遊뉗씠?? ?됯퇏?④?媛 ?꾨땲??媛쒕퀎 LOT 湲곗??쇰줈 留ㅼ닔, 異붽?留ㅼ닔, 留ㅻ룄, ?ъ쭊?? ?먯떎?뺣━, ?섎룞寃?좊? 愿由ы븳?? 媛??以묒슂???먯튃? **二쇰Ц ?붿껌???꾨땲???좉퇋 fill insert ?깃났 ?꾩뿉留?lots/positions瑜?媛깆떊?쒕떎**??寃껋씠??

## ?덈? ?먯튃

- 二쇰Ц ?붿껌留뚯쑝濡?lots/positions瑜?諛붽씀吏 ?딅뒗??
- `store.record_fill(fill)`??true???좉퇋 泥닿껐留?`position_manager.apply_fill()`濡?媛꾨떎.
- duplicate fill, `record_fill_failed`??positions/lots??諛섏쁺?섏? ?딅뒗??
- UI ?쒕쾭??KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딅뒗??
- ?섎룞 二쇰Ц??UI媛 吏곸젒 二쇰Ц?섏? ?딄퀬 `manual_order_requests` ?먮쭔 ?앹꽦?쒕떎.
- Bot Core留?runtime/risk/open-order/live guard ??湲곗〈 `order_manager` 寃쎈줈濡?二쇰Ц ?붿껌??泥섎━?쒕떎.
- DB reset? OPEN LOT 0, 吏꾪뻾 以?order 0, pending manual request 0, sync mismatch ?놁쓬???뚮쭔 媛?ν븯??
- REVIEW_REQUIRED??媛뺤젣 ?댁젣?섏? ?딄퀬 recheck/acknowledge/manual sell/reconciliation ?먮쫫?쇰줈 泥섎━?쒕떎.

## ?꾩옱 ?듭떖 ?곹깭

- config profile: `expansion_100_safe`
- KOSPI ?꾨낫: 100醫낅ぉ
- enabled/manual_only: enabled 97, disabled/manual_only 3
- `max_active_symbols=100`
- `max_total_invested_amount=20,000,000`
- `max_new_buy_per_day=10`
- `max_new_buy_amount_per_day=2,000,000`
- `max_total_open_lots=300`
- `lot_sizing_mode=cycle_locked_by_entry_price`
- `cleanup_enabled=false`
- `ui_manual_trading_enabled=false`
- `live_trading=false`
- `enable_execution_raw_log=true`
- ?꾩옱 DB?먮뒗 OPEN LOT???⑥븘 ?덉쑝誘濡?DB reset 李⑤떒? ?뺤긽?대떎.

## LOT sizing

媛寃⑸?蹂?1 LOT 湲덉븸???ъ슜?섏?留???蹂댁쑀 ?ъ씠?댁뿉?쒕뒗 理쒖큹 吏꾩엯 ??寃곗젙??sizing??怨좎젙?쒕떎. HOLDING 以?二쇨?媛 ?ㅻⅨ 媛寃⑸?濡??대룞?대룄 `lot_unit_amount`, `max_symbol_amount`, `lot_sizing_bucket`? ?ш퀎?고븯吏 ?딅뒗??

| 媛寃⑸? | 1 LOT | 醫낅ぉ??理쒕? | 鍮꾧퀬 |
| --- | ---: | ---: | --- |
| 0~300??| 0 | 0 | disabled |
| 301~1,000??| 3,000 | 30,000 | enabled |
| 1,001~10,000??| 10,000 | 100,000 | enabled |
| 10,001~30,000??| 30,000 | 300,000 | enabled |
| 30,001~100,000??| 100,000 | 1,000,000 | enabled |
| 100,001~300,000??| 300,000 | 3,000,000 | enabled |
| 300,001~1,000,000??| 1,000,000 | 3,000,000 | max_lots=3 |
| 1,000,001???댁긽 | 0 | 0 | disabled/manual only |

異붽?留ㅼ닔??LOT 諛곗닔 band瑜??대떎: 1~2 LOT -4%, 3~4 LOT -6%, 5~6 LOT -8%, 7~8 LOT -10%, 9~10 LOT -12%. 9 LOT?먯꽌 1 LOT 異붽????덉슜?섏뼱 10 LOT源뚯? 媛?ν븯吏留?10 LOT?먯꽌??李⑤떒?쒕떎.

SELL target? 留ㅼ닔 ?뱀떆 怨좎젙媛믪씠 ?꾨땲???꾩옱 OPEN LOT ??湲곗??쇰줈 ?숈쟻 ?곸슜?쒕떎: 1~2 LOT 6%, 3~4 LOT 5%, 5~6 LOT 4%, 7~8 LOT 3%, 9~10 LOT 2%.

## 二쇱슂 ?곹깭

| ?곹깭 | ?섎? | ?뺤콉 |
| --- | --- | --- |
| NEVER_BOUGHT | ?좉퇋 ?꾨낫 | initial_buy 媛?? guard ?꾩슂 |
| HOLDING | OPEN LOT 蹂댁쑀 | add buy/PROFIT_TAKE/CLEANUP 議곌굔遺 媛??|
| WAIT_REENTRY | ?꾨웾 PROFIT_TAKE ???ъ쭊???湲?| initial_buy 湲덉?, NORMAL/TRAILING_REENTRY留?|
| COOLDOWN_AFTER_CLEANUP | cleanup ?꾨웾 醫낅즺 ???湲?| BUY 湲덉?, ?먮룞 ?ъ쭊??湲덉? |
| REVIEW_REQUIRED | ?섎룞寃???꾩슂 | BUY 湲덉?, PROFIT_TAKE ?덉슜, CLEANUP 李⑤떒 |
| RISK_BLOCKED | ?꾪뿕 ?뚮옒洹?| BUY/SELL 紐⑤몢 李⑤떒 |
| SYNC_REQUIRED | DB/KIS 遺덉씪移?| ?좉퇋 二쇰Ц 李⑤떒, reconciliation ?곗꽑 |

## UI/API

- UI: `src/kis_msj/ui_server.py`, service: `src/kis_msj/ui_service.py`
- localhost ?꾩슜 愿??UI?대ŉ ?몃? 怨듦컻 湲덉?.
- UI??KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딅뒗??
- Runtime Control? `config/runtime_control.json`???듯빐 利됱떆 ?곸슜?쒕떎.
- Manual Order Request ??? preview/request ???앹꽦留??쒕떎.
- New Season ??? archive/liquidation plan/manual SELL request/reset guard wizard瑜??쒓났?쒕떎.
- Review ??? REVIEW_REQUIRED 醫낅ぉ??reason, recheck, acknowledge, ?섎룞留ㅻ룄 ?덈궡瑜??쒓났?쒕떎.

## ???쒖쫵 以鍮??먮쫫

1. ?댁쟾 ?쒖쫵 諛깆뾽 archive ?앹꽦.
2. KIS balance snapshot 以鍮?
3. liquidation plan, 利??꾨웾留ㅻ룄 ?덉젙???앹꽦.
4. confirm text `?꾨웾留ㅻ룄 ?붿껌 ?뺤씤` ??manual SELL request ?앹꽦. UI/script??KIS 二쇰Ц API瑜??몄텧?섏? ?딆쓬.
5. Bot Core媛 request瑜?泥섎━?섍퀬 fills/reconciliation ?꾨즺.
6. OPEN LOT 0, pending order 0, pending manual request 0, SYNC_REQUIRED 0 ?뺤씤.
7. confirm text `RESET ?뺤씤` ??DB reset.
8. expansion_100_safe/KOSPI 100 config ?뺤씤 ?????쒖쫵 ?쒖옉.

?꾩옱 OPEN LOT???⑥븘 ?덉쑝誘濡?reset 李⑤떒? ?뺤긽?대떎.

## ?뚯뒪???꾪솴

理쒖떊 ?꾩껜 ?뚭? 湲곗?:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_tmp_final_logic_check
```

寃곌낵??`155 passed`?怨? pytest cache warning 1媛쒕뒗 湲곕뒫 ?ㅽ뙣媛 ?꾨땲??

## ??thread?먯꽌 癒쇱? ?뺤씤??寃?

- ?꾩옱 OPEN LOT ??
- KIS balance snapshot 議댁옱 ?щ?
- liquidation plan ACTIVE/理쒖떊 ?щ?
- pending order/manual request ?щ?
- SYNC_REQUIRED/REVIEW_REQUIRED/RISK_BLOCKED 醫낅ぉ ?щ?
- `live_trading=false` ?좎? ?щ?
- `enable_execution_raw_log=true` ?곹깭? 泥??ㅼ껜寃?raw mapping ?뺤씤 ?щ?
- 理쒖떊 ?뚯뒪???듦낵 ?щ?

## ?꾩옱 ?⑥? ?듭떖 由ъ뒪??

1. ?ㅼ젣 KIS raw execution field mapping? 泥??ㅼ껜寃?row 湲곗? 理쒖쥌 ?뺤씤???꾩슂?섎떎.
2. KIS balance snapshot? ?꾩옱 JSON ?뚯씪 ?낅젰 寃利?援ъ“?? `scripts/prepare_new_season.py`?먮뒗 snapshot ?먮룞 ?앹꽦 湲곕뒫???놁쑝誘濡??댁쁺?먭? 蹂꾨룄 JSON??以鍮꾪빐???쒕떎. ?ㅼ젣 ?꾨웾留ㅻ룄 request ?앹꽦 ?④퀎?먯꽌??理쒖떊 `generated_at`怨??ㅼ젣 `sellable_quantity`媛 ?ы븿??snapshot???ъ슜?댁빞 ?쒕떎.
3. OPEN LOT???⑥븘 ?덉쑝硫?DB reset 李⑤떒???뺤긽?대떎.
4. `live_trading=false`瑜??좎????곹깭?먯꽌 ?뚯븸/?쒗븳 寃利앹쓣 癒쇱? ?댁빞 ?쒕떎.
5. `cleanup_enabled=false`瑜??좎??섍퀬 濡쒓렇/?숆린???덉젙????cleanup ?먮룞?붾? 寃?좏븳??

## 臾몄꽌 ?뺥빀??self-check

- full/summary/thread prompt???꾩옱 ?곹깭 媛믪? `expansion_100_safe`, 100醫낅ぉ, enabled 97, manual_only 3 湲곗??쇰줈 留욎텣??
- `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true` ?쒗쁽???좎??쒕떎.
- manual order ?ㅻ챸? ?쏫IS 吏곸젒 二쇰Ц API ?놁쓬 / manual request ?앹꽦 API???덉쓬?앹쑝濡??듭씪?쒕떎.
- KIS balance snapshot ?ㅻ챸? ?쒗쁽?щ뒗 JSON ?뚯씪 ?낅젰 寃利?援ъ“?대ŉ ?먮룞 ?앹꽦 湲곕뒫? ?놁쓬, ?댁쁺?먭? 蹂꾨룄 JSON??以鍮꾪븳?? ?ㅼ젣 request ?앹꽦 ?④퀎?먯꽌??`generated_at`怨?`sellable_quantity` ?꾩닔?앸줈 ?듭씪?쒕떎.
- reset 李⑤떒 議곌굔? 吏꾪뻾 以?orders/manual requests, OPEN LOT, SYNC_REQUIRED, lot mismatch, KIS/DB mismatch瑜??ы븿?쒕떎.

## 湲덉?

- ?ㅺ굅??二쇰Ц ?꾩쓽 ?ㅽ뻾 湲덉?
- KIS 二쇰Ц API 吏곸젒 ?몄텧 湲덉?
- OPEN LOT ?⑥? ?곹깭 DB reset 湲덉?
- UI?먯꽌 lots/positions/fills 吏곸젒 ?섏젙 湲덉?
- KIS snapshot ?놁씠 ?꾨웾留ㅻ룄 request ?앹꽦 湲덉?
- pending manual request/order媛 ?덉쑝硫?reset 湲덉?

?곸꽭 臾몄꽌??`docs/project_handoff_full.md`瑜??쎌쑝硫??쒕떎.

