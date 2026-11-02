# 대화 확인 경고, XLS 첨부, WS-47 개선 검증

기능 검증 기준: 0.23.40 수정본. 해당 변경을 0.23.41 릴리즈에 포함한다.

## 자동 이어하기 중 대화 확인 경고

같은 폴더의 다른 Claude 세션을 가져오는 문제는 재현되지 않았다. 조회는 연결된 세션 UUID를 기준으로 파일과 레코드를 확인한다. 재현된 문제는 완료 이벤트와 다음 예약 요청의 시작 사이에 자동 조회가 겹치면, 정상적인 상태 변경 거부를 화면에서 외부 도구의 문제로 표시하는 경쟁 상태였다.

- 자동 확인에서 실행 중, 요청 전환 중, 기록 쓰기 중인 경우는 `deferred`로 반환한다. 새 기록이 없다는 결과로 캐시하지 않는다.
- 같은 업무라도 새 요청이나 화면 상태로 바뀐 뒤 도착한 이전 조회 결과와 오류는 적용하지 않는다.
- 완료 이벤트의 순번 반영 후 조회를 시작해 정상적인 결과까지 버리는 문제를 방지한다.
- 명시적인 불러오기 검증, 세션 UUID 격리와 초안 및 권한은 유지한다. 타이머나 반복 조회를 추가하지 않는다.

검증: `test_workspace_external_sync`, `test_workspace_session_sync_frontend`, `test_workspace_session_import`, `test_workspace_frontend_state` 총 84개 실행, 83개 통과. Windows symlink 권한을 요구하는 기존 테스트 1개는 건너뛰었다. 같은 폴더의 다른 세션, 예약 전환, 늦은 응답, 실제 외부 추가 대화 발견과 가져오기를 포함한다.

## XLS 첨부

일반 대화와 업무에서 `.xls`를 첨부할 수 있도록 파일 정책, 파일 선택기, 업로드와 문서 분류를 연결했다. 일반 대화는 XLS를 읽어 시트 이름과 셀 값을 전달한다. Excel이나 매크로를 실행하지 않으며 수식은 파일에 저장된 결과만 사용한다. 서식, 차트와 이미지는 추출하지 않는다.

일반 대화의 XLS 한도는 8 MiB이며 출력 글자 수, 내부 레코드, 문자열 및 셀 할당도 제한한다. 손상 또는 암호화 파일은 해당 첨부의 실패로 안내한다. 판독기는 XLS를 읽을 때만 불러오고 배포본에 고정 버전과 라이선스를 포함하므로 사용자의 Python 패키지 설치에 의존하지 않는다.

검증은 실제 바이너리 XLS의 한글, 숫자, 날짜, 여러 시트, 읽기 범위, 캐시, 손상 및 암호화 파일, HTTP 업로드와 오프라인 번들의 판독기 포함 여부를 대상으로 한다. 관련 명령:

```powershell
python -X utf8 -B -m unittest tests.test_workspace_xls_attachments tests.test_workspace_chat_attachments tests.test_workspace_drop_upload -q
```

기존 `test_workspace_picker_native`의 전체 필터 일치 검사는 HEAD에서도 `.axextension` 차이로 실패한다. 이번 XLS 추가와 별개이며 해당 기존 필터 동작은 변경하지 않았다.

## WS-47 화면 초기화

제공받은 LTSC 2019 / Windows build 17763 / WebView2 156.0.4314.58 교차 검증은 DPI 선언만 바꾼 경우의 성공과 실패를 제시했다. 이를 근거로 공용 실행기 매니페스트의 `PerMonitorV2` 선언을 제거하고 `dpiAware=true`를 적용했다. Windows 설정이나 호환성 레지스트리는 변경하지 않는다.

정식 `New-WorkspaceDesktop.ps1`로 호스트를 재빌드하고 `desktop-build.json`의 원본 및 결과물 해시를 확인했다. 실패 시 고정된 단계 이름, 정수 HRESULT와 계산된 16진수 표현을 기록하며 예외 원문, 파일 경로와 인증정보는 기록하지 않는다. 서버 종료 직전에도 최종 오류를 저장한다. WS-47 설명도 화면 표시 호환성을 포함하도록 수정했다.

`0x8007139F` 자체는 일반적인 상태 오류이므로 모든 WS-47을 DPI 문제로 단정하지 않는다. 브라우저 창으로 자동 전환하는 동작은 추가하지 않았다.

- `test_workspace_ui_health`, `test_workspace_native_window`, `test_workspace_startup`: 73개 통과.
- `test_workspace_standalone_bootstrap`, `test_workspace_window_display`: 36개 통과. 실제 컴파일된 검사 프로세스의 DPI 인식 수준이 system-aware(1)임을 확인했다.
- Windows 11 build 26300 / WebView2 156.0.4314.58에서 새 프로필 2개로 네이티브 창과 앱 준비에 성공했다. 각 화면 요청 35개가 모두 HTTP 200이며 native 오류가 없었다. 테스트 소유 프로세스는 종료했다.
- 문제의 LTSC PC에서 직접 재실행한 결과는 아직 없다. 제공된 현장 결과와 이 PC의 검증을 구분해야 한다.

배율이 다른 모니터 사이에서는 system-aware 앱에 Windows 확대가 적용되어 PMv2보다 덜 선명할 수 있다. [Microsoft DPI 안내](https://learn.microsoft.com/en-us/windows/win32/hidpi/high-dpi-desktop-application-development-on-windows)

네이티브 검증 자료는 Hermes 작업 폴더의 `outputs/ws47-dpi-20261102`에 보관했다. 0.23.41 배포 빌드는 버전 및 소스 무결성 확인을 거쳐 별도로 생성한다. 공개 릴리즈 검증에는 원격 커밋 및 태그 일치와 자산의 익명 다운로드 SHA-256 대조를 사용한다.
