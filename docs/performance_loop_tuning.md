# 봇 루프와 UI 성능

## 현재 구조

기본 루프 간격은 15초다. 장중에는 시세·계좌·주문상태·전략을 평가하지만 API 최소 호출간격과 계좌 스냅샷 최소간격을 둔다. 장외에는 전체 종목을 매 루프 다시 저장하지 않는다.

완료된 일봉 데이터는 하루 한 번 일괄 로드해 메모리에 캐시한다. 심각손실 recovery의 최초 진입과 최저 종가 갱신도 이 캐시를 이용한다. positions/lots 저장은 `executemany` 기반 일괄 저장을 사용한다.

## 주요 설정

- `loop_interval_seconds=15`
- `loop_profiling_enabled=true`
- `loop_interval_warning_min_seconds=60`
- `account_snapshot_min_interval_seconds=30`
- `kis_min_request_interval_seconds=0.25`
- `kis_balance_page_interval_seconds=1.5`

## 진단

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_loop.py --help
```

느려질 때는 로그의 단계별 profile, KIS rate-limit, 계좌 잔고 pagination, SQLite lock, UI의 대형 조회를 구분한다. UI가 느리다고 봇 루프가 반드시 느린 것은 아니며 반대도 마찬가지다. 동일 DB에 쓰기 프로세스를 중복 실행하지 않는다.

성능 개선 시 거래판단을 생략하거나 완료 일봉을 현재가로 대체하지 않는다. 캐시의 날짜 전환, 장 재개, 재시작 시 무효화 여부를 테스트해야 한다.
