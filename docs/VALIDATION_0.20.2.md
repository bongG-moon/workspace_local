# 0.20.2 연결 상태 안내와 시작 창 검증

검증일: 2026-09-30. Windows 개발 PC에서 수행했습니다.

## 변경 결과

- Claude 실행 파일을 찾았지만 선택한 업무의 연결을 아직 열지 않은 상태는 **Claude 실행 준비됨**으로 표시합니다. 실제 연결 중에는 **업무 연결됨**, 연결이 끝난 경우에는 **업무 연결 종료**로 구별합니다. 각 상태의 설명도 함께 제공합니다.
- 도구·연결 서버·명령 목록의 `연결 필요`는 해당 업무의 CLI 연결에서 목록을 아직 받지 못했다는 뜻입니다. 실행 파일 발견을 실제 업무 연결이나 목록 조회 성공으로 표시하지 않습니다. `스킬·도구 → 연결하고 목록 확인`으로 초기화할 수 있습니다.
- 네이티브 앱 창은 처음 생성할 때 최대화됩니다. 전체 화면이 아니라 Windows 작업 영역을 사용하는 최대화이며 작업 표시줄은 유지됩니다.
- 반복 실행은 기존 창을 활성화합니다. 최대화한 창을 최소화했다가 복원하면 최대화로 돌아오고, 사용자가 일반 창으로 조절했다면 그 크기를 보존합니다. 백그라운드 숨김 후 다시 열 때도 같은 창을 사용합니다.

연결·인증·승인 정책과 개인 Claude 설정은 변경하지 않았습니다.

## 자동 검사

다음 범위에서 총 **124개 실행, 123개 통과, 조건부 제외 1개**, 실패·오류 0개입니다.

```powershell
python -m unittest discover -s tests -p test_workspace_native_window.py
```

위 네이티브 창 검사와 함께 `test_workspace_frontend_state`, `test_workspace_capabilities`, `test_workspace_capabilities_frontend`, `test_workspace_catalog_scope_routes`, `test_workspace_standalone_bootstrap`, `test_workspace_diagnostic` 모듈을 실행했습니다. 제외된 항목은 별도 실행 옵션이 필요한 실제 로그인 사용자의 일반 토큰 진단입니다. 아래 최종 EXE 실행 검증은 실제 Windows 사용자 컨텍스트에서 별도로 수행했습니다. 이전 전체 검사 수를 이번 결과에 합산하지 않았습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-lab/test-desktop-window.ps1
```

네이티브 창 검증 도구는 격리된 비대화형 Windows 데스크톱에서 제품의 창 생성·활성화 코드를 실행합니다. WebView와 IPC의 외부 초기화만 억제하며 다음 동작을 확인했습니다.

- 최초 최대화와 작업 영역 내 클라이언트 크기
- 반복 활성화 시 같은 HWND 유지
- 최대화 → 최소화 → 복원 시 최대화 유지
- 일반 창 → 최소화 → 복원 시 크기·위치 유지
- 닫기 버튼의 백그라운드 숨김과 같은 창 다시 열기
- 숨김·최소화가 함께 적용된 일반 창과 최대화 창 복원
- 크기 조절 가능한 일반 창 속성과 비최상위 창 유지

## 최종 EXE와 VBS ZIP

- EXE만 있는 한글 경로에서 0.20.2 실행 성공
- 실제 WebView2 전용 창 1개 확인: `IsZoomed=true`, `showCommand=3`
- 반복 EXE 실행에서 동일 캐시와 서버 PID 재사용
- 기존 Python 3.13.5 사용, Python 설치 프로그램 포함·다운로드 없음
- 기존 Claude Code 2.1.285 자동 탐지와 초기화 성공
- 기존 연결에서 모델 `claude-sonnet-5-5`, effort `medium`, 명령 141개 확인
- AI 질문 전송 0개, 개인 설정 변경 0개, 테스트 서버 정상 종료
- VBS ZIP을 새 격리 폴더에 추출해 시작·인증 API 경계·정적 화면·한글 글꼴·업무·파일·알림 요청·카탈로그·정상 종료 확인
- 소스 86개와 고정 네이티브 빌드 파일 7개, 총 93개 배포 파일의 일치 및 사용자 상태·인증 정보 미포함 확인

| 파일 | SHA-256 |
| --- | --- |
| Company-Workspace-0.20.2.exe | 76569867aa3de78c31d4c8a59a90dfde5f359562a4c54362a3abbc60a29613dc |
| Company-Workspace-0.20.2-exe.zip | a733dfcfe32cd33a51591942630ae503094ffcdf5db1f8d5c4e44c3b6c0a3bba |
| Company-Workspace-0.20.2-vbs.zip | ee12c97a95d63efab978b6c11332271d2a879d4bfd0eaeb5915122b920a97ab7 |

로컬 검증 기록은 `build/qa-window-0.20.2-tests.json`, `build/qa-standalone-0.20.2-maximized/`에 보관합니다. 인증 정보를 포함할 수 있는 런타임 URL·전체 상태는 공개 문서에 포함하지 않습니다.

## 검증 범위와 기존 예약 결함

모든 운영 PC의 혼합 화면 배율·모니터·Windows Script Host 정책 조합을 검증하지는 않았습니다. 네이티브 창 검사는 창 상태와 동작을 확인하는 검사이며 모든 화면의 시각적 검수로 간주하지 않습니다.

이번 버전은 연결 상태 표현과 시작 창 동작을 수정합니다. 이전 예약 검증에서 발견한 재시작 후 폴더 확인 연결, 완료된 일회성 예약의 미래 수정 후 비활성 상태, CLI 연결 한도 도달 시 예약의 확인 필요 상태 고정 문제는 수정하지 않았습니다. 세 결함의 재현 검사와 [기존 예약 검증 기록](VALIDATION_0.20.1_SCHEDULING.md)을 함께 보관합니다. 해당 예상 실패 3개를 이번 버전의 정상 통과 수에 포함하지 않았습니다.
