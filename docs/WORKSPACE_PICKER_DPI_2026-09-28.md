# Workspace 0.11.3 파일·폴더 선택 창 개선

## 원인과 구현

0.11.2의 Pick-Path.ps1은 고해상도 배율 인식을 초기화하지 않았고 Windows 시각 스타일도 활성화하지 않았습니다. 폴더 선택에는 Windows Forms의 FolderBrowserDialog를 사용했습니다. 실제 설치본을 Windows PowerShell 5.1의 새 STA 프로세스에서 초기화한 뒤 창을 띄우지 않고 검사했을 때 GetAwarenessFromDpiAwarenessContext는 0(DPI unaware)이었습니다.

0.11.3은 새 WorkspacePicker.cs를 포함합니다. 선택 창과 임시 부모 창을 생성하기 전에 Per-Monitor V2를 우선 적용하며, 이전 Windows에서는 지원되는 DPI API로 단계적으로 대체합니다. 파일·폴더를 IFileOpenDialog 공통 항목 선택 창으로 통일하고 폴더 모드에서는 FOS_PICKFOLDERS를 사용합니다. 실제 파일 시스템 경로만 반환하고 여러 파일 선택을 유지합니다.

앱이 지정하는 창 제목, 선택 버튼, 파일 형식 안내는 한국어로 표시합니다. Windows 탐색 영역의 언어·테마는 OS 설정을 따릅니다. Windows 전체 화면 배율, 호환성 레지스트리, 사용자 계정, Claude 설정은 변경하지 않습니다.

기존의 임시 부모 창 소유 관계와 한 번만 앞으로 표시하는 동작을 보존합니다. 실제 Workspace 브라우저 창을 직접 모달 창의 부모로 사용해 강제 비활성화하지 않습니다. 취소는 빈 배열을 반환하고 실패는 선택 완료로 처리하지 않습니다. 네이티브 경로 메모리와 COM 객체는 정리합니다.

## 근거

- [Microsoft: DPI awareness contexts](https://learn.microsoft.com/en-us/windows/win32/hidpi/dpi-awareness-context): DPI unaware 창의 시스템 확대와 Per-Monitor V2의 대화상자·비클라이언트 영역 배율 처리.
- [Microsoft: SetThreadDpiAwarenessContext](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext): 스레드의 창 생성 배율 모드 설정.
- [Microsoft: Common Item Dialog](https://learn.microsoft.com/en-us/windows/win32/shell/common-file-dialog): IFileOpenDialog의 폴더 선택·다중 선택·취소 HRESULT·부모 창 계약.

## 확인 범위

선택 창의 실제 렌더링을 여러 모니터에서 시각적으로 확인한 결과와 네이티브 API 테스트를 구분합니다. 이 환경에서는 Windows 네이티브 화면 자동화가 제공되지 않아 실제 팝업 캡처나 125/150/200% 각각의 시각적 성공을 주장하지 않습니다. 최종 테스트·적용 결과는 아래에 기록합니다.

## 검증·적용 결과

- 선택 창 테스트 15개 통과: 기존 창 생명주기 7개, 실제 네이티브 API 3개, 서버의 부모 창·STA·동시 선택 경계 5개.
- 별도 버전·시작 진단·재실행·서버 경계 묶음 24개 중 23개 통과, 환경 조건 1개 건너뜀. 위 선택 창 경계 테스트 5개가 중복 포함됩니다.
- 창을 표시하지 않은 실제 API 검사: threadMode=PerMonitorV2, windowV2=true, windowDpi=192(현재 200% 화면 배율), visualStyles=true, font=Segoe UI, scaling=Dpi, apartment=STA. 이는 실제 렌더링 스크린샷 검증은 아닙니다.
- 실제 COM 옵션을 조회해 폴더=6248(0x1868), 파일=6728(0x1A48)을 확인했습니다. 공통 선택 창이 폴더에서도 기존 항목 확인 플래그를 유지하는 Windows 동작을 반영했습니다. 실제 ShellItem에서 한글·공백·대괄호·이모지 폴더 및 파일 경로를 정확히 반환했습니다.
- 기존 폴더 `dist/company-workspace-preview-0.11.1-20260928-214617/Company-Workspace`의 수정 파일 6개를 교체했습니다. 활성 선택 창과 작업 프로세스가 없음을 확인하고 정상 종료한 뒤 같은 실행기로 재시작했습니다. 실행기 종료 코드 0, 실제 API 버전 0.11.3 및 동일 실행 폴더를 확인했습니다.
- 설치한 폴더에서 별도로 초기화·COM 생성 검사를 수행해 PerMonitorV2, 시각 스타일 활성화, 폴더/파일 옵션, 다중 선택을 재확인했습니다. 대화상자는 표시하지 않았습니다. Claude 요청과 신뢰 설정 변경은 수행하지 않았습니다.
- 최신 ZIP `dist/company-workspace-preview-0.11.3-20260928-223829.zip`: 42개 파일 소스·적용 폴더 일치, 시작 진단 해시 일치, 실행 상태·자격증명 제외. SHA-256 `6faf35bbcb3f7360e3de9727f3ef909c6ea4f987fe212195c9c1780165377cf8`.
