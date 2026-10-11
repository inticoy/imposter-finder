# 운영 모니터링

게임별 API·등록 계정·Discord 목적지·Gemini 평가를 보고합니다. TFT는 승인 대기로 운영 목록에서 의도적으로 꺼져 있습니다.

공통 알림 서비스는 private 레포 `inticoy/monitoring`입니다. 서비스별 인증으로 상태를 보고하고 별도 D1에서 중복·복구·하루 리마인드를 관리해 개인 Agents 서버의 고정 포럼 스레드로 보냅니다.

## Healthchecks

15분 / grace 10분. 실행 결과를 공통 Worker에 보고한 완료 처리에서 핑을 보냅니다. 기능 장애가 있어도 보고 완료 핑은 보낼 수 있으므로 Healthchecks Up과 기능 정상은 별개입니다. 실행·보고가 끊기면 독립 Down/Up 알림이 감지합니다.

Actions Secrets: `MONITOR_ENDPOINT`, 서비스별 `MONITOR_TOKEN`, `HEALTHCHECK_URL`. 앱 ENV_FILE과 별도로 두어 설정 파일 생성 전 실패도 최종 단계에서 보고합니다. `if: always()` 단계가 최종 workflow 상태와 실행 링크를 전달합니다. runner 강제 종료·checkout 실패는 Healthchecks 미수신으로 감지합니다.

오류 알림은 HTTP 상태·오류 클래스·구조적 traceback과 전체 로그 링크를 담습니다. API 키·플레이어 정보·사용자 프롬프트·비공개 일정/URL은 보내지 않습니다. 미점검 기능은 정상/복구로 처리하지 않습니다.

운영 절차·스레드 연결·제한사항: [monitoring 운영 문서](https://github.com/inticoy/monitoring/blob/main/docs/operations.md) (레포 접근 권한 필요). Secret 값은 문서에 기록하지 않습니다.
