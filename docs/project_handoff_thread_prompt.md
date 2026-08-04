# 새 작업 스레드 전달문

아래 내용을 새 작업 요청에 붙여 넣는다.

> C:\MSJ\KIS-MSJ의 KIS 국내주식 LOT 자동매매 봇을 다룬다. 먼저 README.md와 docs/project_handoff_full.md를 읽고, 반드시 현재 코드와 config/lot_auto_trader.json을 실제로 확인해 문서보다 우선하라. 실운영 SQLite와 JSON을 임의 수정하거나 봇을 재시작하지 말라. 진단은 읽기 전용으로 하고, 변경 요청이면 관련 테스트와 전체 pytest를 수행하라. 현재 핵심은 cycle-locked LOT sizing, +3%~+0.5% 이익회전, -40% 진입 후 최저 종가 기반 30일 decay recovery, buy_blocked/retire_after_exit, 일반·trailing 재진입이다. 단순 LOT 나이만으로 REVIEW_REQUIRED가 되는 조건은 제거됐다. UI 이력 그래프는 실제 과거 총자산 원장이 아니라 재구성 데이터다. 운영 변경 전 OPEN 주문·동기화·백업·재시작 영향을 명확히 보고하라.
