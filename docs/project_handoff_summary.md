> **현행 정책 안내 (Adaptive V2)**: 현재 소스의 통합 명세는 [CURRENT_TRADING_POLICY](CURRENT_TRADING_POLICY.md)입니다. 아래 본문은 작성 당시 정책·운영 기록이며, 최저 LOT 앵커·연속 횟수·달력 7일 Decay 설명은 현행 Adaptive 판단에 적용되지 않습니다. 운영 프로세스 적용 여부는 통합 문서의 검증·적용 상태를 확인하세요.

# 인수인계 요약

- 실운영: `config/lot_auto_trader.json`, SQLite `data/lot_auto_trader_real_test.sqlite3`
- 232 활성종목, 72개는 매수차단 및 청산 후 거래중지
- LOT 최대 12개, 추가매수 -4%~-24%, 목표수익 +3%~+0.5%
- 매수 지정가 현재가 +0.3%, 매도 -0.5%, 추정 비용 0.21%
- deep-loss recovery: 완료 종가 -40% 진입, 최저 종가 대비 +10% 반등에서 시작해 30일간 0% decay, 30일 강제매도
- recovery는 -40% 위 회복 후에도 유지, 다수 LOT 일일 제한 없음, 오래된 순서
- 단순 장기보유 `REVIEW_REQUIRED` 조건 제거
- trailing은 매도가 아니라 전량 이익실현 후 재진입 로직
- UI는 11개 탭, 보호된 설정·runtime·수동주문·신규시즌·review 기능 제공
- 그래프 JSON/CSV export는 재구성 데이터이며 실제 과거 계좌 총자산 스냅샷이 아님
- 완료 일봉 bulk cache와 DB bulk save로 루프/UI 성능 개선
- 변경 후 전체 테스트, 백업, 안전 재시작, reconciliation 확인 필수

세부사항은 [전체 인수인계](project_handoff_full.md)와 [전략 문서](strategy_lot_sizing.md)를 본다.
