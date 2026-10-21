# 이어 할 일 클릭과 드래그 검증

## 원인과 수정

- 행 전체의 native draggable 설정 때문에 삭제/지금 반영 버튼을 누른 채 마우스가 7px 움직이면 dragstart 대상은 버튼이 아닌 행이 됩니다. 기존 버튼 제외 조건이 작동하지 않아 pointercancel이 발생하고 click/API 호출이 누락되는 것을 실제 Edge에서 재현했습니다.
- 손잡이만 draggable로 지정하고 dragstart 대상도 손잡이로 제한했습니다. 행의 grab 커서도 제거했습니다.
- 드래그 도중 전체 dispatch revision 변경만으로 취소하던 조건을 실제 queued 항목 ID/순서 비교로 좁혔습니다. 업무 전환, 잠금, 항목 구성/순서 변경 시 보호 조건은 유지합니다.

## 확인

- 관련 Python/Node 회귀 테스트 179개 통과.
- 실제 배포 HTML/JS와 합성 HTTP 응답을 사용하는 scripts/test-lab/verify-workspace-queue-pointer.cjs 추가.
- 출력 이벤트 없음/20ms 간격 assistant_delta 및 상태 갱신 조건 각각에서 삭제/지금 반영 클릭 중 마우스 이동 0, 4, 7, 10px 모두 한 번에 처리: 16개 조건.
- 두 조건에서 손잡이 순서 변경 2개 조건 추가: 총 18개 통과, 페이지 오류 없음.
- 기존 브라우저 검증에서 초안 보존, 줄바꿈 전송, 입력창 지금 반영, 삭제 및 순서 변경 통과.
- 실제 Claude 프로세스나 사내 환경은 호출하지 않았습니다. 이 결과는 클릭 이벤트 충돌을 입증하며 사용자 PC의 모든 부하 요인을 배제하는 측정은 아닙니다.

## 재검증

저장소 루트에서 Playwright가 있는 node_modules를 NODE_PATH로 지정한 뒤 실행합니다.

```text
node scripts/test-lab/verify-workspace-queue-pointer.cjs
```

버전 0.23.29 유지. 동일 버전 릴리즈 갱신에 포함합니다.
