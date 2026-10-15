; A per-user installer for the verified portable entry point.
; Updates and user data remain managed by the existing application.
Unicode true
RequestExecutionLevel user
ManifestDPIAware true
ManifestLongPathAware true
SetCompressor /SOLID lzma
SetDatablockOptimize on
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WordFunc.nsh"
!include "x64.nsh"

!ifndef PRODUCT_ID
  !define PRODUCT_ID "AX.Workspace.Desktop"
!endif
!ifndef PRODUCT_NAME
  !define PRODUCT_NAME "AX Workspace"
!endif
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_ID}"
Name "${PRODUCT_NAME}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_NAME}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1042 "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey /LANG=1042 "FileDescription" "${PRODUCT_NAME} 설치 프로그램"
VIAddVersionKey /LANG=1042 "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey /LANG=1042 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1042 "LegalCopyright" "AX Workspace contributors"
BrandingText "AX Workspace · 내 업무와 AI가 만나는 공간"
ShowInstDetails show
ShowUninstDetails show
Var InstallerMutex
!macro AcquireInstallerMutex
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\${PRODUCT_ID}.Installer") p.r0 ?e'
  Pop $1
  StrCpy $InstallerMutex $0
  ${If} $0 == 0
  ${OrIf} $1 == 183
    MessageBox MB_ICONSTOP "다른 설치 또는 제거가 진행 중입니다. 완료된 뒤 다시 시도해 주세요." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
!macroend
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "AX Workspace 설치"
!define MUI_WELCOMEPAGE_TEXT "내 업무와 AI가 만나는 공간$\r$\n$\r$\n이 Windows 사용자 계정에 설치하고 시작 메뉴에 바로가기를 만듭니다.$\r$\n$\r$\n최신 Claude Code 설치·인증과 Python 3.11 이상이 필요합니다. 기존 업무 내역과 Claude 설정은 그대로 사용합니다.$\r$\n$\r$\n앱이 실행 중이면 알림 영역 아이콘에서 완전 종료한 뒤 설치해 주세요."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "AX Workspace 설치 완료"
!define MUI_FINISHPAGE_TEXT "시작 메뉴에서 AX Workspace를 열 수 있습니다.$\r$\n$\r$\n기존 업무와 설정은 유지됩니다. 필요한 실행 환경은 앱을 열 때 확인합니다."
!define MUI_FINISHPAGE_RUN "$INSTDIR\AX-Workspace.exe"
!define MUI_FINISHPAGE_RUN_TEXT "AX Workspace 열기"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!define MUI_UNCONFIRMPAGE_TEXT_TOP "앱을 사용 중이면 알림 영역 아이콘에서 완전 종료한 뒤 제거해 주세요.$\r$\n$\r$\n설치한 실행 파일과 바로가기만 제거합니다. 업무 내역, 예약, 설정, 작업 파일과 Claude Code는 삭제하지 않습니다."
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "Korean"

Function .onInit
  !insertmacro AcquireInstallerMutex
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Windows 10/11 64비트 환경에서 설치해 주세요." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "DisplayVersion"
  ${If} $0 != ""
    ${VersionCompare} $0 "${APP_VERSION}" $1
    ${If} $1 == 1
      MessageBox MB_ICONSTOP "더 최신 버전이 설치되어 있습니다. 최신 설치 파일을 사용해 주세요." /SD IDOK
      SetErrorLevel 2
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

Section "AX Workspace (필수)" MainSection
  SectionIn RO
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=InstallerSupport.exe "${SUPPORT_EXE}"
  nsExec::ExecToStack /TIMEOUT=15000 '"$PLUGINSDIR\InstallerSupport.exe" check "$INSTDIR" "${PRODUCT_ID}"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "설치 폴더를 확인하지 못했습니다. 다른 파일이 있는 폴더나 연결된 폴더에는 설치하지 않습니다." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  ClearErrors
  SetOverwrite try
  File /oname=AX-Workspace.exe "${STANDALONE_EXE}"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "실행 파일을 설치하지 못했습니다. AX Workspace를 완전 종료하고 다시 시도해 주세요." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ClearErrors
  FileOpen $0 "$INSTDIR\AX-Workspace.install-id" w
  FileWrite $0 "${PRODUCT_ID}"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "설치 정보를 저장하지 못했습니다. 앱을 완전 종료하고 설치 폴더의 쓰기 권한을 확인해 주세요." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ClearErrors
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "AX Workspace"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\AX-Workspace.exe,0"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${INSTALL_KB}
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "Windows에 앱을 등록하지 못해 설치가 완료되지 않았습니다. 설치 폴더와 계정 권한을 확인한 뒤 다시 설치해 주세요." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  nsExec::ExecToStack /TIMEOUT=15000 '"$PLUGINSDIR\InstallerSupport.exe" shortcut "$SMPROGRAMS\${PRODUCT_NAME}.lnk" "$INSTDIR\AX-Workspace.exe" "${PRODUCT_ID}"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    DetailPrint "시작 메뉴 바로가기를 만들지 못했습니다. 설치 폴더에서 앱을 열 수 있습니다."
  ${EndIf}
SectionEnd

Section "바탕화면 바로가기" DesktopSection
  nsExec::ExecToStack /TIMEOUT=15000 '"$PLUGINSDIR\InstallerSupport.exe" shortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\AX-Workspace.exe" "${PRODUCT_ID}"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    DetailPrint "바탕화면 바로가기를 만들지 못했습니다. 시작 메뉴에서 앱을 열 수 있습니다."
  ${EndIf}
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${MainSection} "현재 사용자에게 앱을 설치합니다. 업무 데이터와 Claude 설정은 기존 위치를 사용합니다."
  !insertmacro MUI_DESCRIPTION_TEXT ${DesktopSection} "바탕화면에서 앱을 바로 열 수 있습니다. 시작 메뉴 바로가기는 항상 만듭니다."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function un.onInit
  !insertmacro AcquireInstallerMutex
  SetShellVarContext current
  SetRegView 64
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 != $INSTDIR
    MessageBox MB_ICONSTOP "설치 위치가 일치하지 않아 제거하지 않았습니다." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
FunctionEnd

Section "Uninstall"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=InstallerSupport.exe "${SUPPORT_EXE}"
  nsExec::ExecToStack /TIMEOUT=15000 '"$PLUGINSDIR\InstallerSupport.exe" check-uninstall "$INSTDIR" "${PRODUCT_ID}"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "설치 파일의 위치와 소유 정보를 확인하지 못해 제거하지 않았습니다." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ClearErrors
  Delete "$INSTDIR\AX-Workspace.exe"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "앱을 완전 종료한 뒤 다시 제거해 주세요. 업무와 설정은 유지했습니다." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  nsExec::ExecToStack /TIMEOUT=15000 '"$PLUGINSDIR\InstallerSupport.exe" remove-shortcut "$SMPROGRAMS\${PRODUCT_NAME}.lnk" "$INSTDIR\AX-Workspace.exe" "${PRODUCT_ID}"'
  Pop $0
  Pop $1
  nsExec::ExecToStack /TIMEOUT=15000 '"$PLUGINSDIR\InstallerSupport.exe" remove-shortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\AX-Workspace.exe" "${PRODUCT_ID}"'
  Pop $0
  Pop $1
  ClearErrors
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "일부 설치 정보를 제거하지 못했습니다. 앱을 완전 종료하고 다시 시도해 주세요. 업무 데이터는 유지했습니다." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  Delete "$INSTDIR\AX-Workspace.install-id"
  ; Never recursively delete this folder or any user data/cache/Claude directory.
  RMDir "$INSTDIR"
SectionEnd
