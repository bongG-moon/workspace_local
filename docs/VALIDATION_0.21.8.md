# Workspace 0.21.8 검증 기록

검증일: 2026-10-01. 이번 변경은 도움말과 별도로 여는 단축키 안내입니다.

## 변경 범위

- 사이드바 하단에 단축키 메뉴와 접힌 상태의 키보드 아이콘을 추가했습니다.
- 빠른 실행(Ctrl+Shift+P)에서 단축키 안내를 검색해 열 수 있습니다.
- 보내기, 자동완성, 모델·승인, 이전 입력·임시 보관, 글 편집, 작업 제어·화면의 6개 그룹에 27개 항목을 정리했습니다.
- 키 이름과 기능 이름을 기기 안에서 검색하며 결과 수와 빈 결과 안내를 표시합니다.
- 검색 중 Esc는 안내 창을 한 번에 닫고 보이는 실행 버튼으로 초점을 돌려줍니다.
- 현재 앱의 지원 범위와 CLI와 다른 부분을 설명합니다. 새 단축키를 등록하거나 기존 입력·승인·Claude 설정을 변경하지 않습니다.

## 자동 검증

```text
python -X utf8 -m unittest tests.test_workspace_palette_frontend tests.test_workspace_frontend_state tests.test_workspace_layout_frontend tests.test_workspace_startup_health_frontend tests.test_workspace_chat_shortcuts tests.test_workspace_input_keys
114 tests, OK
```

실제 화면에서 검색창의 기본 Esc 동작이 검색어만 지우는 것을 발견한 뒤, 안내 닫기를 명시적으로 처리했습니다. 이 보완 후 palette/startup-health 관련 46개 검사를 다시 실행해 통과했습니다. 작업 중 빠른 실행에서 안내로 이동하고 검색 후 닫아도 활성 업무, 입력 초안, 첨부가 유지되며 API 요청이 발생하지 않는 회귀 검사를 포함합니다.

새 버튼을 준비 상태 검사에 추가한 결과 기존 테스트의 가상 버튼 목록에서 누락된 항목이 발견되어 테스트 구성을 실제 UI와 맞췄습니다. 최종 실행 파일의 실제 화면에서도 준비 상태 오류가 없음을 확인했습니다.

## 실제 Windows 앱 확인

Computer Use로 소스 기반 체험 창과 배포할 EXE의 WebView2 창을 확인했습니다.

- 사이드바 단축키 버튼과 27개 항목 표시
- 기능 이름 `모델` 검색과 해당 그룹 표시
- 일치하지 않는 검색어의 0개 결과 안내
- 최종 EXE에서 Ctrl+Shift+P → 단축키 검색 → Enter로 안내 열기
- 최종 EXE에서 `Ctrl+R` 검색 시 이전 요청 검색 1개 항목 표시
- 검색어가 있는 상태에서 Esc 한 번으로 닫히고 사이드바 버튼에 초점 복원
- 접힌 사이드바의 키보드 아이콘으로 안내 다시 열기
- 최대화 화면과 709×465 캡처 크기의 창에서 제목·검색·닫기·하단 안내 표시 및 본문 스크롤

각 단축키의 실제 Claude 요청을 모두 다시 실행한 검증은 아닙니다. 기존 키 처리 모듈의 자동 검사와 안내 설명의 코드 대조를 수행했습니다. 별도 고대비 설정과 모든 화면 배율 조합은 이번에 직접 변경하여 시험하지 않았습니다.

## 최종 EXE 및 배포 묶음

- EXE만 있는 한글 경로에서 실제 실행 성공: 0.21.8, 전용 WebView2 창
- 기존 설치 Python 3.13.5 및 Claude Code 2.1.286 발견, 기존 CLI 인증 경로 사용
- 재실행 시 같은 캐시와 서버 PID 재사용
- 개인 Claude 설정의 검증 전후 해시 일치, 변경 파일 0개
- 검증용 서버만 정상 종료
- EXE 내부와 VBS ZIP의 117개 파일 일치, Python·설치 프로그램 미포함

| 파일 | SHA-256 |
| --- | --- |
| Company-Workspace-0.21.8.exe | `e75a63efdcd88d2731c8d53d90788e24e7e9ef34a4ad921c53ecef5a71a4e93a` |
| Company-Workspace-0.21.8-vbs.zip | `66e672c17b2cec4eb05934f2d9ea4e9f3154ca8697a23d9dbc26a27f4356b90d` |
| Company-Workspace-0.21.8-exe.zip | `fe5659a73a8cc1f76f6d7e1b77fd81ff6223cfb0ac31025ad48719cb5034d064` |

AI 요청을 새로 전송하지 않았으며 실제 사용자의 열려 있는 구버전 창은 종료하지 않았습니다. 다른 PC의 보안 정책·배율·Claude 배포 환경까지 검증한 결과는 아닙니다.
