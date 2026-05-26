# KOSPI 100 ?뺤옣 ?댁슜 config

> Authoritative source: `docs/project_handoff_full.md` is the latest full baseline. `docs/project_handoff_thread_prompt.md` is for starting a new chat, and `docs/project_handoff_summary.md` is the short summary. `local_ui.md`, `strategy_lot_sizing.md`, `new_season_reset.md`, and `expansion_100_config.md` are detailed references. If a reference doc conflicts with the full handoff, use `project_handoff_full.md` as the source of truth.  
> Last updated: 2026-05-26 / Baseline tests: `155 passed` / Baseline config profile: `expansion_100_safe`. Re-check config, DB, logs, and KIS account state at runtime.


湲곕낯 ?꾨줈?뚯씪? `expansion_100_safe`?낅땲?? ?꾨낫 醫낅ぉ? 100媛쒕줈 愿由ы븯吏留? 泥ル궇遺??紐⑤몢 留ㅼ닔?섏? ?딅룄濡??좉퇋 吏꾩엯 ?섎웾怨?湲덉븸??蹂꾨룄濡??쒗븳?⑸땲??

## ?곸슜 ?꾨줈?뚯씪

| profile | max_total_invested_amount | max_new_buy_per_day | max_new_buy_amount_per_day | max_total_open_lots |
|---|---:|---:|---:|---:|
| expansion_100_safe | 20,000,000 | 10 | 2,000,000 | 300 |
| expansion_100_medium | 30,000,000 | 15 | 3,000,000 | 450 |
| expansion_100_aggressive | 50,000,000 | 20 | 5,000,000 | 700 |

`max_new_buy_amount_per_day`? `max_total_initial_buy_amount_per_day`???섎（ initial buy 二쇰Ц 湲덉븸 ?⑷퀎瑜??쒗븳?⑸땲?? 二쇰Ц ?쒖궗 諛⑹?瑜??꾪빐 泥닿껐 湲곗????꾨땲??二쇰Ц ?붿껌 湲곗??쇰줈 怨꾩궛?⑸땲??

## 媛寃⑸?蹂?LOT sizing

| ?꾩옱媛 援ш컙 | 1 LOT 湲덉븸 | 醫낅ぉ??理쒕?湲덉븸 | ?먮룞留ㅼ닔 |
|---|---:|---:|---|
| 0~300 | 0 | 0 | 鍮꾪솢??|
| 301~1,000 | 3,000 | 30,000 | ?쒖꽦 |
| 1,001~10,000 | 10,000 | 100,000 | ?쒖꽦 |
| 10,001~30,000 | 30,000 | 300,000 | ?쒖꽦 |
| 30,001~100,000 | 100,000 | 1,000,000 | ?쒖꽦 |
| 100,001~300,000 | 300,000 | 3,000,000 | ?쒖꽦 |
| 300,001~1,000,000 | 1,000,000 | 3,000,000 | ?쒖꽦, 理쒕? 3 LOT |
| 1,000,001~3,000,000 | 0 | 0 | 鍮꾪솢??|

LOT sizing? `cycle_locked_by_entry_price` 諛⑹떇?낅땲?? 理쒖큹 吏꾩엯 ???꾩옱媛 湲곗??쇰줈 1 LOT 湲덉븸怨?理쒕?湲덉븸???뺥븯怨? 媛숈? 蹂댁쑀 ?ъ씠???숈븞?먮뒗 二쇨?媛 ?ㅻⅨ 媛寃?援ш컙?쇰줈 ?대룞?대룄 ?ㅼ떆 怨꾩궛?섏? ?딆뒿?덈떎.

## 異붽?留ㅼ닔 LOT band

| ?꾩옱 OPEN LOT ??| 異붽?留ㅼ닔 ?섎씫瑜?| 異붽? LOT |
|---|---:|---:|
| 1~2 | 4% | 1 |
| 3~4 | 6% | 1 |
| 5~6 | 8% | 1 |
| 7~8 | 10% | 1 |
| 9~10 | 12% | 1 |

`max_lots_per_symbol_default=10`??湲곕낯?낅땲?? 媛寃⑸? band??`max_lots`媛 ?덉쑝硫?洹?媛믪쓣 ?곗꽑?⑸땲??

## 紐⑺몴?섏씡瑜?LOT band

紐⑺몴?섏씡瑜좎? 留ㅼ닔 ?뱀떆 怨좎젙媛믪씠 ?꾨땲???꾩옱 OPEN LOT ??湲곗??쇰줈 ?숈쟻?쇰줈 ?ы룊媛?⑸땲??

| ?꾩옱 OPEN LOT ??| 紐⑺몴?섏씡瑜?|
|---|---:|
| 1~2 | 6% |
| 3~4 | 5% |
| 5~6 | 4% |
| 7~8 | 3% |
| 9~10 | 2% |

?댄썑 LOT age decay媛 ?곸슜?⑸땲?? PROFIT_TAKE? CLEANUP_SELL 遺꾨쪟??target???꾨땲???ㅼ젣 ?먯씡 湲곗??낅땲??

## 珥덇린 ?뺤옣 ?댁슜 沅뚯옣媛?

- `cleanup_enabled=false`: ?좉퇋 ?뺤옣 珥덈컲?먮뒗 泥닿껐/?숆린??log ?덉젙?붽? ?곗꽑?낅땲??
- `ui_manual_trading_enabled=false`: ?섎룞 二쇰Ц ?붿껌? ?꾩슂 ??紐낆떆?곸쑝濡?耳?땲??
- `enable_execution_raw_log=true`: 泥??ㅼ껜寃?field mapping ?뺤씤 ??false濡??섎룎由쎈땲??
- `live_trading=false`: ??config ?곸슜 吏곹썑?먮뒗 paper/mock ?뚯뒪?몃? 癒쇱? ?섑뻾?⑸땲??

## ?꾨낫 醫낅ぉ 寃利?硫붾え

?ㅽ겕由쏀듃??肄붾뱶 ?뺤떇, 以묐났, 湲곕낯 ?꾪뿕 ?뚮옒洹몃? 寃利앺빀?덈떎. ?ㅼ떆媛?KRX 嫄곕옒?뺤?/愿由ъ쥌紐??щ???泥?live ?댁슜 ??蹂꾨룄 ?뺤씤???꾩슂?⑸땲??

?꾩옱 ?꾨낫 以??꾨옒 醫낅ぉ? ?먮룞留ㅼ닔瑜?鍮꾪솢?깊솕?⑸땲??

- `005935 ?쇱꽦?꾩옄??: KIS KOSPI master 寃利앹뿉??誘명솗?몃릺??`enabled=false`, `manual_only=true`, `liquidity_warning=true`
- `001230 ?숆뎅??⑹뒪`: KIS KOSPI master 湲곗? `trading_halt_yn=Y`?쇱꽌 `enabled=false`, `manual_only=true`, `trading_halted=true`
- `020560 ?꾩떆?꾨굹??났`: ??쒗빆怨??듯빀 愿???대깽??由ъ뒪?ш? ?덉뼱 `enabled=false`, `manual_only=true`, `administrative_issue=true`

