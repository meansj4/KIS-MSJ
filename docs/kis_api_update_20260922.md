> **현행 정책 안내 (Adaptive V2)**: 현재 소스의 통합 명세는 [CURRENT_TRADING_POLICY](CURRENT_TRADING_POLICY.md)입니다. 아래 본문은 작성 당시 정책·운영 기록이며, 최저 LOT 앵커·연속 횟수·달력 7일 Decay 설명은 현행 Adaptive 판단에 적용되지 않습니다. 운영 프로세스 적용 여부는 통합 문서의 검증·적용 상태를 확인하세요.

# KIS 국내주식 API 연동 업데이트 — 2026-09-22

공식 참고 기준: `koreainvestment/open-trading-api`의 `b4e6249714418aa57833d1cbbbced39cbcc5b125`.
로컬 참고 저장소의 2026-03-18 버전 이후 국내주식 REST 예제 변경은 없었다.
이번 작업은 기존 봇과 최신 공식 예제 사이의 차이 및 연속조회 누락을 보완한다.

## 변경 사항

- 실전 현금 매수/매도 TR을 `TTTC0012U`/`TTTC0011U`, 취소를 `TTTC0013U`, 최근 체결조회를 `TTTC0081R`, 정정취소가능조회를 `TTTC0084R`로 맞췄다.
- 모의 주문/취소/체결에는 대응하는 `V` 접두어 TR을 사용한다. 정정취소가능조회는 모의 TR을 임의 생성하지 않고 당일 일별주문조회 결과의 잔량으로 대체한다.
- 주문·취소·일별주문조회에 `EXCG_ID_DVSN_CD=KRX`를 명시한다. NXT/SOR 거래 지원 추가는 포함하지 않는다.
- 체결·미체결·잔고 조회에서 응답 헤더 `tr_cont=M/F`일 때 조회 키와 요청 헤더 `tr_cont=N`으로 다음 페이지를 읽는다. 종료 헤더가 있으면 남아 있는 조회 키만으로 추가 호출하지 않는다.
- 연속조회 키가 반복되거나 누락되거나 100페이지 상한에 도달하면 예외로 처리한다. 일부 데이터만 성공 결과로 반환하지 않는다. 조회 중 API 오류도 호출자에게 전달한다.
- 3개월 이전 체결은 과거 조회 TR `CTSC9215R`/`VTSC9215R`로 분리한다. 달력상 3개월 경계를 사용하고 월말은 해당 월의 마지막 날로 보정한다.
- 취소 직전 최신 주문정보에서 조직번호와 가능수량을 조회한다. 요청수량과 가능수량 중 작은 수량을 명시하며, 잔량전부취소 대신 `QTY_ALL_ORD_YN=N`을 사용한다.
- 취소 대상/잔량이 없으면 기존 주문관리자의 체결 재확인 경로로 전달한다. 조직번호 누락, 중복 주문번호, KRX 이외 거래소는 취소를 전송하지 않는다.
- hashkey는 공식 예제상 선택 사항이므로 기본 생략한다. 필요하면 `KisClient(use_order_hashkey=True)`로 사용한다.

## 검증 및 적용 범위

네트워크 응답을 대체한 단위 테스트로 연속조회, 오류 차단, 최신 TR, 취소 정보, 모의조회, 과거 날짜 분리, hashkey 선택 동작을 검증한다.
실제 증권사 주문·취소는 전송하지 않는다. 실계좌에서의 API 수용 여부는 별도 운영 확인이 필요하다.
실행 중 봇에는 소스 변경이 자동 적용되지 않으며, 재시작은 이 작업에 포함하지 않는다.
운영 DB·계좌 설정·LOT 전략을 수정하지 않는다.

## 공식 근거

- [현금 주문](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/order_cash/order_cash.py)
- [정정·취소](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py)
- [일별주문체결조회](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py)
- [정정취소가능조회](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py)
- [hashkey 선택 사용 안내](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_user/kis_auth.py)
