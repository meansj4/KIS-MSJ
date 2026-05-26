# ???쒖쫵 reset/archive ?덉감

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `155 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


???쒖쫵 以鍮꾨뒗 ?쒓린議??뚯뒪???댁쁺 湲곕줉???덉쟾?섍쾶 蹂닿??섍퀬, 蹂댁쑀/誘몄껜寃??숆린???곹깭瑜?源⑤걮?섍쾶 留뚮뱺 ????config濡??ㅼ떆 ?쒖옉?섎뒗 ?덉감?앹엯?덈떎. 諛붾줈 DB瑜?吏?곕㈃ ?ㅼ젣 怨꾩쥖?먮뒗 二쇱떇???⑥븘 ?덈뒗???대? DB留??щ씪吏????덉쑝誘濡? 諛섎뱶??諛깆뾽, ?꾨웾留ㅻ룄 ?덉젙?? ?ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤, 泥닿껐 ?숆린?? DB 珥덇린???쒖꽌濡?吏꾪뻾?댁빞 ?⑸땲??

?ъ슜??移쒗솕???⑹뼱:

- archive = ?댁쟾 ?쒖쫵 諛깆뾽
- liquidation plan = ?꾨웾留ㅻ룄 ?덉젙??
- KIS balance snapshot = ?ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤 ?먮즺
- manual SELL request = 遊뉗뿉寃??꾨웾留ㅻ룄 ?붿껌
- reset = DB 珥덇린??

## UI ???쒖쫵 以鍮?留덈쾿??

UI???쒖깉 ?쒖쫵 以鍮꾟???? ?꾨옒 ?쒖꽌濡??꾩옱 ?곹깭? ?ㅼ쓬 ?됰룞??蹂댁뿬以띾땲??

1. ?댁쟾 ?쒖쫵 諛깆뾽: DB/config/log瑜?archive濡?蹂닿??⑸땲??
2. ?ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤: DB 蹂댁쑀?섎웾怨?KIS ?ㅼ젣 ?붽퀬 鍮꾧탳??snapshot???꾩슂?⑸땲??
3. ?꾨웾留ㅻ룄 ?덉젙???앹꽦: ?꾩옱 DB? KIS snapshot 湲곗??쇰줈 留ㅻ룄 ???LOT??怨꾩궛?⑸땲??
4. ?꾨웾留ㅻ룄 ?붿껌 ?앹꽦: UI媛 吏곸젒 二쇰Ц?섏? ?딄퀬 `manual_order_requests` ?먯뿉 ?붿껌留?留뚮벊?덈떎.
5. 泥닿껐 諛??숆린???뺤씤: 二쇰Ц 泥닿껐怨?reconciliation ?꾨즺 ?щ?瑜??뺤씤?⑸땲??
6. DB 珥덇린?? OPEN LOT 0媛? 誘몄껜寃?0媛? 誘몄쿂由??섎룞 ?붿껌 0媛? sync mismatch ?놁쓬???뚮쭔 媛?ν빀?덈떎.
7. ??100醫낅ぉ config ?곸슜 ?뺤씤: `expansion_100_safe`? KOSPI 100 ?꾨낫援곗쓣 ?뺤씤?⑸땲??
8. ???쒖쫵 ?쒖옉 以鍮??꾨즺: 紐⑤뱺 李⑤떒 議곌굔???댁냼?섎㈃ 以鍮??꾨즺 ?곹깭媛 ?쒖떆?⑸땲??

?꾩옱 UI?먯꽌???꾨옒 ?묒뾽??吏곸젒 ?ㅽ뻾?????덉뒿?덈떎.

- 諛깆뾽 dry-run / 諛깆뾽 ?앹꽦
- KIS ?붽퀬 snapshot JSON 寃쎈줈瑜??낅젰???꾨웾留ㅻ룄 ?덉젙??dry-run / ?앹꽦
- ?덉젙???뚯씪 寃쎈줈? `?꾨웾留ㅻ룄 ?붿껌 ?뺤씤` 臾멸뎄瑜??낅젰??manual SELL request dry-run / ?앹꽦
- `RESET ?뺤씤` 臾멸뎄瑜??낅젰??reset dry-run / DB 珥덇린???ㅽ뻾

二쇱쓽: UI 踰꾪듉??KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딆뒿?덈떎. ?꾨웾留ㅻ룄 ?붿껌 ?앹꽦? `manual_order_requests` ?먯뿉 `SELL / REQUESTED`瑜??ｋ뒗 ?묒뾽?대ŉ, ?ㅼ젣 二쇰Ц? ?ㅽ뻾 以묒씤 Bot Core媛 湲곗〈 runtime pause, risk guard, open order guard, order_manager 寃쎈줈瑜?嫄곗퀜 泥섎━?⑸땲??

`request_creation_possible=false`???대? ?곹깭媛믪엯?덈떎. UI?먯꽌??????쒖쟾?됰ℓ???붿껌 ?앹꽦 遺덇??? ?쒖쟾?됰ℓ???덉젙?쒓? ?놁뒿?덈떎?? ?쏫IS ?붽퀬 ?뺤씤 ?먮즺媛 留뚮즺?섏뿀?듬땲?ㅲ?媛숈? ?ъ슜?먯슜 臾멸뎄? ?ㅼ쓬 ?됰룞??癒쇱? ?쒖떆?⑸땲??

plan status ?섎?:

- `ACTIVE`: ?꾩옱 ?꾨웾留ㅻ룄 ?덉젙?쒓? ?좏슚?⑸땲??
- `EXPIRED`: ?덉젙?쒓? ?ㅻ옒?섏뼱 ?덈줈 留뚮뱾?댁빞 ?⑸땲??
- `SUPERSEDED`: ??理쒖떊 ?덉젙?쒓? ?덉뼱 ???덉젙?쒕뒗 ?ъ슜?????놁뒿?덈떎.
- `USED`: ?대? ?꾨웾留ㅻ룄 ?붿껌 ?앹꽦???ъ슜???덉젙?쒖엯?덈떎.
- `BLOCKED`: 李⑤떒 ?ъ쑀媛 ?덉뼱 ?ъ슜?????놁뒿?덈떎.

DB 珥덇린??媛??議곌굔:

- OPEN LOT 0媛?
- 誘몄껜寃?二쇰Ц 0媛?
- 誘몄쿂由?manual request 0媛?
- `SYNC_REQUIRED` 0媛?
- lot quantity mismatch 0媛?
- ?ㅼ젣 怨꾩쥖 ?붽퀬? DB ?섎웾 遺덉씪移??놁쓬

???덉감??湲곗〈 ?댁쁺 湲곕줉??蹂댁〈???????꾨낫援곌낵 ??由ъ뒪???쒕룄濡??ㅼ떆 ?쒖옉?섍린 ?꾪븳 ?덉쟾 ?μ튂?낅땲?? ?ㅽ겕由쏀듃 湲곕낯媛믪? dry-run?대ŉ, ?ㅺ굅??二쇰Ц API瑜??몄텧?섏? ?딆뒿?덈떎.

## 湲곕낯 ?먯튃

- 湲곗〈 config, DB, logs????젣?섏? ?딄퀬 `archive/reset_YYYYMMDD_HHMMSS/` ?꾨옒濡?蹂듭궗?⑸땲??
- DB 珥덇린?붾뒗 `RESET ?뺤씤` 臾멸뎄媛 ?덉뼱???섎ŉ, open order ?먮뒗 sync mismatch媛 ?덉쑝硫?李⑤떒?⑸땲??
- ?꾨웾留ㅻ룄??利됱떆 二쇰Ц?섏? ?딄퀬 liquidation plan ?뚯씪留??앹꽦?⑸땲??
- ?꾨웾留ㅻ룄 ?붿껌???꾩슂?섎㈃ 蹂꾨룄 ?뺤씤 ??manual order request 寃쎈줈濡쒕쭔 泥섎━?댁빞 ?⑸땲??
- manual SELL request媛 ?앹꽦?섎뜑?쇰룄 ?ㅼ젣 fill ?꾩뿉??lots/positions媛 諛붾뚮㈃ ???⑸땲??

## dry-run ?먭?

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --apply-config --liquidation-plan --profile expansion_100_safe --dry-run
```

??紐낅졊? ?대뼡 ?뚯씪????젣?섍굅??蹂寃쏀븯吏 ?딄퀬, archive/config/liquidation 怨꾪쉷??JSON?쇰줈 誘몃━ 蹂댁뿬以띾땲??

## archive + ??config ?곸슜

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --apply-config --profile expansion_100_safe --execute
```

?숈옉:

- ?꾩옱 config/DB/log瑜?archive ?대뜑??諛깆뾽?⑸땲??
- config???꾨낫 醫낅ぉ??KOSPI 100 ?꾨낫援곗쑝濡?援먯껜?⑸땲??
- `risk.profile=expansion_100_safe`瑜??곸슜?⑸땲??
- `live_trading=false`, `cleanup_enabled=false`, `ui_manual_trading_enabled=false`, `enable_execution_raw_log=true`濡??쒖옉?⑸땲??

## DB 珥덇린??

DB 珥덇린?붾뒗 湲곗〈 蹂댁쑀/誘몄껜寃??숆린???곹깭媛 ?꾩쟾???뺣━???ㅼ뿉留??섑뻾?댁빞 ?⑸땲??

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET ?뺤씤" --execute
```

李⑤떒 議곌굔:

- orders??`REQUESTED`, `PARTIAL`, `SUBMITTED`, `ACCEPTED`, `PENDING`, `OPEN`, `NEW` 媛숈? 吏꾪뻾 以?二쇰Ц???⑥븘 ?덉쓬
- manual_order_requests??`REQUESTED`, `PROCESSING`, `ACCEPTED`, `SUBMITTED`, `PENDING`, `OPEN`, `NEW`, `CREATED`, `RETRYING` 媛숈? 吏꾪뻾 以??붿껌???⑥븘 ?덉쓬
- OPEN LOT???⑥븘 ?덉쓬
- positions??`SYNC_REQUIRED` ?곹깭媛 ?덉쓬
- positions??lot quantity mismatch媛 ?덉쓬
- KIS/DB balance mismatch媛 ?덉쓬

## ?꾨웾留ㅻ룄 怨꾪쉷

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --execute
```

??紐낅졊? `exports/liquidation_plan_YYYYMMDD_HHMMSS.json` ?뚯씪??留뚮벊?덈떎. 二쇰Ц ?붿껌? 留뚮뱾吏 ?딆뒿?덈떎.

怨꾪쉷 ?뺤씤 ??ぉ:

- 醫낅ぉ 肄붾뱶
- LOT ID
- DB ?붿뿬?섎웾
- ?꾩옱媛 湲곗? ?덉긽 留ㅻ룄湲덉븸
- ?덉긽 ?먯씡

?ㅼ젣 留ㅻ룄 ???뺤씤:

- KIS ?ㅼ젣 ?붽퀬? DB OPEN LOT ?섎웾???쇱튂?섎뒗吏 ?뺤씤
- 誘몄껜寃?二쇰Ц???녿뒗吏 ?뺤씤
- ?섎룞留ㅻ룄 ?붿껌? Bot Core/manual_order_requests 寃쎈줈濡쒕쭔 ?앹꽦
- 泥닿껐 reconciliation ??lots remaining quantity媛 0?몄? ?뺤씤

## 泥??댁쁺 ??泥댄겕由ъ뒪??

- DB 諛깆뾽 ?꾨즺
- 湲곗〈 logs archive ?꾨즺
- 湲곗〈 config archive ?꾨즺
- 湲곗〈 蹂댁쑀 ?꾨웾留ㅻ룄 ?꾨즺 ?щ? ?뺤씤
- KIS ?붽퀬? DB positions/lots 遺덉씪移??놁쓬
- manual_order_requests 誘몄쿂由?`REQUESTED` ?놁쓬
- orders 以?`REQUESTED`/`PARTIAL` ?놁쓬
- fills 以?誘몃컲????ぉ ?놁쓬
- `enable_execution_raw_log=true`
- `live_trading=false` ?곹깭?먯꽌 paper/mock ?뚯뒪???듦낵
- live trading ?꾪솚 ???ъ슜??紐낆떆 ?뺤씤
## Liquidation plan latestness guard

?꾨웾留ㅻ룄 ?덉젙?쒕뒗 怨좎젙 臾몄꽌媛 ?꾨땲???쒖깮???쒖젏??DB OPEN LOT ?곹깭 + KIS ?붽퀬 snapshot?앹엯?덈떎. ?곕씪???덉쟾??留뚮뱺 plan???섏쨷??洹몃?濡??ъ궗?⑺븯硫????⑸땲??

plan ?뚯씪?먮뒗 ?ㅼ쓬 硫뷀??곗씠?곌? ??λ맗?덈떎.

- `plan_id`, `created_at`
- `db_snapshot_at`, `kis_balance_snapshot_at`
- `source_db_path`, `source_kis_snapshot_path`
- `db_open_lot_hash`, `kis_snapshot_hash`
- `open_lot_count`
- `pending_order_count`
- `pending_manual_request_count`
- `sync_required_count`
- `lot_mismatch_count`
- `status`: `ACTIVE`, `EXPIRED`, `SUPERSEDED`, `USED`, `BLOCKED`
- `expires_at`, `max_age_minutes`

??plan???앹꽦?섎㈃ 湲곗〈 `ACTIVE` plan? `SUPERSEDED`濡?諛붾앸땲?? ?꾨웾留ㅻ룄 manual SELL request瑜?留뚮뱾湲?吏곸쟾?먮뒗 ?꾨옒瑜??ㅼ떆 寃利앺빀?덈떎.

1. confirm text媛 `?꾨웾留ㅻ룄 ?붿껌 ?뺤씤`?몄? ?뺤씤
2. plan??議댁옱?섍퀬 `ACTIVE`?몄? ?뺤씤
3. ?꾩옱 DB OPEN LOT hash媛 plan??`db_open_lot_hash`? 媛숈?吏 ?뺤씤
4. KIS balance snapshot hash媛 plan??`kis_snapshot_hash`? 媛숈?吏 ?뺤씤
5. plan??留뚮즺?섏? ?딆븯?붿? ?뺤씤
6. plan ?앹꽦 ??誘몄껜寃?order??pending manual request媛 ?앷린吏 ?딆븯?붿? ?뺤씤
7. `SYNC_REQUIRED` ?먮뒗 lot quantity mismatch媛 ?녿뒗吏 ?뺤씤
8. DB ?섎웾怨?KIS snapshot ?섎웾, sellable quantity媛 紐⑤몢 異⑸텇?쒖? ?뺤씤

寃利??ㅽ뙣 ??`manual_order_requests`瑜?留뚮뱾吏 ?딆쑝硫? KIS 二쇰Ц API???몄텧?섏? ?딆뒿?덈떎. 李⑤떒 ?ъ쑀??`liquidation_plan_db_changed`, `liquidation_plan_snapshot_expired`, `liquidation_plan_pending_work_created` 媛숈? `block_reason`?쇰줈 ?④퉩?덈떎.

?꾨웾留ㅻ룄 request ?앹꽦 ?꾩뿉??DB reset? 諛붾줈 ?덉슜?섏? ?딆뒿?덈떎. 紐⑤뱺 ?섎룞 SELL request? orders媛 醫낃껐?섍퀬, OPEN LOT 0媛? KIS/DB mismatch ?놁쓬, `SYNC_REQUIRED` 0媛쒓? ?뺤씤?섏뼱??reset??媛?ν빀?덈떎.
## UI 留덈쾿??諛⑹떇?쇰줈 吏꾪뻾?섍린

UI??`???쒖쫵 New Season` ??? 媛쒕컻?먯슜 ?대? flag瑜?洹몃?濡?蹂댁뿬二쇰뒗 ?붾㈃???꾨땲?? ?ъ슜?먭? ?ㅼ쓬 ?됰룞???????덇쾶 ?④퀎??留덈쾿?щ줈 援ъ꽦?⑸땲??

媛???ъ슫 ?ъ슜踰뺤? `???쒖쫵 以鍮?怨꾩냽 吏꾪뻾` 踰꾪듉???꾨Ⅴ??寃껋엯?덈떎. ??踰꾪듉? ?꾩옱 ?곹깭瑜??뺤씤???????④퀎?⑸쭔 吏꾪뻾?⑸땲??

1. **?댁쟾 ?쒖쫵 諛깆뾽**: 踰꾪듉??泥섏쓬 ?꾨Ⅴ硫?config/DB/log archive 諛깆뾽 ?앹꽦???뺤씤?⑸땲??
2. **?ㅼ젣 怨꾩쥖 ?붽퀬 ?뺤씤**: OPEN LOT???덉쑝硫?KIS ?붽퀬 snapshot JSON 寃쎈줈媛 ?꾩슂?⑸땲?? ???④퀎??二쇰Ц???꾨땲???붽퀬 鍮꾧탳 ?먮즺 以鍮꾩엯?덈떎.
3. **?꾨웾留ㅻ룄 ?덉젙???앹꽦**: ?꾩옱 DB OPEN LOT怨?KIS ?붽퀬 snapshot??湲곗??쇰줈 ??plan??留뚮벊?덈떎. 湲곗〈 ACTIVE plan? ??plan ?앹꽦 ?????댁긽 ?ъ슜?섏? ?딄쾶 ?⑸땲??
4. **?꾨웾留ㅻ룄 ?붿껌 ?앹꽦**: plan???좏슚?섍퀬 DB/KIS ?섎웾??留욎쑝硫?誘몄껜寃?誘몄쿂由??붿껌???놁쓣 ?뚮쭔 `manual_order_requests`??SELL ?붿껌??留뚮벊?덈떎. ?뺤씤 臾멸뎄??`?꾨웾留ㅻ룄 ?붿껌 ?뺤씤`?낅땲??
5. **泥닿껐 諛??숆린???뺤씤**: UI??二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딆뒿?덈떎. ?ㅽ뻾 以묒씤 Bot Core媛 manual request瑜?湲곗〈 order_manager 寃쎈줈濡?泥섎━?섍퀬, fill/reconciliation???앸굹???ㅼ쓬 ?④퀎濡?媛????덉뒿?덈떎.
6. **DB 珥덇린??*: OPEN LOT 0媛? 吏꾪뻾 以?二쇰Ц 0媛? 吏꾪뻾 以?manual request 0媛? sync mismatch ?놁쓬???뚮쭔 媛?ν빀?덈떎. ?뺤씤 臾멸뎄??`RESET ?뺤씤`?낅땲??
7. **??config ?곸슜 ?뺤씤**: `expansion_100_safe` profile怨?100醫낅ぉ ?꾨낫援곗씠 ?곸슜?섏뼱 ?덉쑝硫????쒖쫵 以鍮??꾨즺濡??쒖떆?⑸땲??

踰꾪듉??鍮꾪솢?깊솕?섍굅??吏꾪뻾??留됲옄 ?뚮뒗 ?대? 媛믩낫???ъ슜?먯슜 ?덈궡瑜?癒쇱? 遊낅땲??

- `liquidation_plan_missing`: ?꾨웾留ㅻ룄 ?덉젙?쒕? ?앹꽦?댁빞 ?⑸땲??
- `liquidation_plan_db_changed`: ?덉젙???앹꽦 ??蹂댁쑀 LOT??諛붾뚯뿀?쇰?濡??덉젙?쒕? ?ㅼ떆 留뚮뱾?댁빞 ?⑸땲??
- `liquidation_plan_snapshot_expired`: KIS ?붽퀬 ?뺤씤 ?먮즺媛 ?ㅻ옒?섏뿀?쇰?濡?snapshot???ㅼ떆 以鍮꾪빐???⑸땲??
- `liquidation_plan_pending_work_created`: 誘몄껜寃?二쇰Ц ?먮뒗 誘몄쿂由?manual request媛 ?덉뼱 癒쇱? ?꾨즺瑜?湲곕떎?ㅼ빞 ?⑸땲??
- `reset_open_lot_exists`: ?꾩쭅 OPEN LOT???⑥븘 ?덉뼱 DB 珥덇린?붽? 遺덇??ν빀?덈떎.
- `reset_pending_order_exists`: 誘몄껜寃?二쇰Ц???덉뼱 DB 珥덇린?붽? 遺덇??ν빀?덈떎.
- `reset_pending_manual_request_exists`: 誘몄쿂由?manual request媛 ?덉뼱 DB 珥덇린?붽? 遺덇??ν빀?덈떎.
- `reset_sync_required`: DB? ?ㅼ젣 怨꾩쥖 ?숆린???뺤씤??癒쇱? ?꾩슂?⑸땲??

??UI ?먮쫫? KIS 二쇰Ц API瑜?吏곸젒 ?몄텧?섏? ?딆쑝硫? ?꾨웾留ㅻ룄??Bot Core媛 湲곗〈 runtime pause, risk guard, open order guard, order_manager 寃쎈줈瑜??듦낵???ㅼ뿉留?泥섎━?⑸땲??



## Current reset guard and Runbook ??

This section is the canonical wording for reset guards and PowerShell commands in this document. If older text above is less specific, use this section and `docs/project_handoff_full.md` as the source of truth.

### Reset-blocking order statuses

The DB reset must be blocked if any order is still in one of these in-progress statuses:

- `REQUESTED`
- `PARTIAL`
- `SUBMITTED`
- `ACCEPTED`
- `PENDING`
- `OPEN`
- `NEW`

Terminal order statuses do not block reset by themselves:

- `FILLED`
- `CANCELED`
- `REJECTED`
- `FAILED`
- `EXPIRED`
- `PARTIAL_CANCELED`
- `NONE`

### Reset-blocking manual_order_requests statuses

The DB reset must be blocked if any manual request is still in one of these in-progress statuses:

- `REQUESTED`
- `PROCESSING`
- `ACCEPTED`
- `SUBMITTED`
- `PENDING`
- `OPEN`
- `NEW`
- `CREATED`
- `RETRYING`

Terminal manual request statuses do not block reset by themselves:

- `FILLED`
- `CANCELED`
- `REJECTED`
- `FAILED`
- `BLOCKED`
- `EXPIRED`

Additional reset blockers:

- OPEN LOT exists
- `SYNC_REQUIRED` exists
- `lot_quantity_mismatch` exists
- KIS/DB balance mismatch
- pending liquidation/manual request exists

### PowerShell command convention

Run commands from repository root `C:\MSJ\KIS-MSJ`. Include `$env:PYTHONPATH='src'` when running local modules/scripts. Dry-run and execute commands must be separated. Commands below do not call KIS order APIs.

Archive dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --dry-run
```

Archive execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --archive --execute
```

Liquidation plan dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --dry-run
```

Liquidation plan execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --liquidation-plan --kis-balance-json exports\kis_balance_snapshot.json --execute
```

Manual SELL request dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "?꾨웾留ㅻ룄 ?붿껌 ?뺤씤" --dry-run
```

Manual SELL request execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --create-liquidation-requests --liquidation-plan-file exports\liquidation_plan_...json --kis-balance-json exports\kis_balance_snapshot.json --confirm "?꾨웾留ㅻ룄 ?붿껌 ?뺤씤" --execute
```

This creates `manual_order_requests` only. It does not call KIS order APIs. The running Bot Core must consume the requests through the existing guard and `order_manager` path.

DB reset dry-run:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET ?뺤씤" --dry-run
```

DB reset execute:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\prepare_new_season.py --config config\lot_auto_trader.json --reset-db --confirm "RESET ?뺤씤" --execute
```

DB reset execute is allowed only when OPEN LOT is 0, in-progress orders are 0, in-progress manual requests are 0, `SYNC_REQUIRED` is 0, lot mismatch is 0, and KIS/DB balance mismatch is absent.

## KIS balance snapshot current implementation status

The current implementation validates a KIS balance snapshot JSON file path supplied by the operator/UI/script. `scripts/prepare_new_season.py` does not currently auto-create this snapshot from KIS. The operator must prepare or select a fresh JSON snapshot before creating liquidation requests. Do not create liquidation requests without a fresh snapshot.

Supported JSON shape:

- A top-level list of position rows, or
- An object with `positions: [...]`

Fields accepted by the current loader:

- code: `code`, `pdno`, or `symbol`
- holding quantity: `holding_quantity`, `hldg_qty`, or `quantity`
- sellable quantity: `sellable_quantity`, `ord_psbl_qty`, or `available_quantity`

Validation mode is intentionally split:

- Preview / dry-run: missing `generated_at` is allowed with `snapshot_generated_at_missing_warning`. Missing sellable quantity is allowed with holding-quantity fallback plus `snapshot_sellable_quantity_fallback_warning`. The plan can be shown, but `request_creation_allowed` must be false.
- Create request: `generated_at` is required, must parse as an ISO timestamp, and must be within `--plan-max-age-minutes`. Sellable quantity is required from the snapshot. Missing/invalid/stale snapshot metadata blocks manual SELL request creation.

Request-mode block reasons:

- `liquidation_kis_balance_snapshot_missing_generated_at`
- `liquidation_kis_balance_snapshot_invalid_generated_at`
- `liquidation_kis_balance_snapshot_stale`
- `liquidation_kis_sellable_quantity_missing`
- `liquidation_sellable_quantity_insufficient`

`sellable_quantity` falls back to holding quantity only for preview/dry-run. Do not use fallback sellable quantity to create actual liquidation requests. Plan expiration also checks the liquidation plan creation time and `--plan-max-age-minutes`.

If the snapshot is missing, stale by plan age, unparsable, or inconsistent with DB quantities, liquidation request creation must be blocked.

