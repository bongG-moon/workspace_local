# 선택 설치형 확장 API 1

공통 앱은 설치된 확장의 메뉴, 격리된 화면, 제한된 요청 전달만 제공합니다. 조직별 서버 주소·인증·업무 기능은 별도 확장 패키지에 둡니다. 확장이 없으면 추가 화면 자산, 네트워크 요청, 작업 프로세스, 반복 조회를 시작하지 않습니다. 설치 목록은 첫 조회에만 확인하며 명시적인 새로고침 외에는 캐시합니다.

## 설치 위치와 호환성

앱 상태 디렉터리를 `state`라고 할 때 다음 위치를 사용합니다.

```text
state.parent/
  extensions/
    example.tools/
      extension.json
      packages/<64자리 소문자 SHA-256 형식 식별자>/
        worker.py
        ui.html
        vendor/...
  extension-data/
    example.tools/       # 인증 설정·다운로드·설치 기록 등 확장 소유 데이터
```

프로그램 파일과 사용자 데이터를 분리합니다. 앱 실행 자료 폴더나 압축 해제 캐시에 확장 또는 개인 데이터를 설치하지 않습니다. 앱 업데이트 후에도 이 사용자 데이터 위치가 같아야 확장 설치를 유지할 수 있습니다. 폴더를 자동으로 옮기거나 다른 Windows 계정의 설치를 가져오지 않습니다.

**앱 표시 버전만으로 확장 지원 여부를 판단하지 마세요.** 동일 버전으로 재빌드할 수 있습니다. 최소 호환성 확인은 실행 중인 앱의 정상 인증 경로로 `GET /api/extensions`를 호출하여 `extensions`와 `warnings` 배열이 반환되는지 확인하는 것입니다. 해당 경로가 없는 구형 앱은 확장 연결부를 포함한 공통 앱 빌드로 먼저 업데이트해야 합니다. 파일 기반 설치 도구라면 공통 앱의 검증된 배포 목록에 `local_app/extensions.py`, `local_app/web/extensions.js`, `local_app/web/extensions.css`가 포함되어 있는지도 확인하세요. API 토큰을 개인 설정이나 확장에 복사하여 보관하지 않습니다.

현재 지원하는 `schemaVersion`과 `hostApi`는 모두 정수 `1`입니다. 다른 버전이나 손상된 manifest는 불러오지 않으며 기존 채팅은 계속 동작합니다. 설정창의 추가 기능 복구 안내가 나오면 해당 확장의 설치 프로그램으로 다시 설치하고 앱을 다시 여세요. 호환되지 않는 앱 업데이트 후에도 같은 절차로 호환 확장 패키지를 다시 설치합니다.

## Manifest

예시는 모든 `files` 값에 실제 파일 바이트의 SHA-256을 넣어야 합니다. 경로 구분자는 `/`입니다.

```json
{
  "schemaVersion": 1,
  "id": "example.tools",
  "name": "추가 기능",
  "version": "1.0.0",
  "hostApi": 1,
  "entrypoint": "packages/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/worker.py",
  "ui": "packages/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/ui.html",
  "actions": ["status", "prepare", "apply"],
  "files": {
    "packages/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/worker.py": "실제 파일의 64자리 소문자 SHA-256",
    "packages/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/ui.html": "실제 파일의 64자리 소문자 SHA-256"
  }
}
```

- ID는 영문 소문자·숫자·`.`·`-`로 구성한 1~64자이며 처음과 마지막은 영문 소문자 또는 숫자입니다. `..`와 Windows 예약 이름을 사용할 수 없습니다.
- 표시 이름은 60자, 버전은 50자 이내입니다. 버전은 `1.0.0` 형태를 사용합니다.
- 실행 파일은 `.py`, 화면은 UTF-8 `.html`이어야 하며 모두 `files`에 있어야 합니다. `extension.json` 자체는 `files`에 넣지 않습니다.
- 동작 이름은 영문 소문자로 시작하는 1~64자의 영문·숫자·`_`·`.`·`-`이며 1~48개를 선언합니다. 미선언 동작은 실행하지 않습니다.
- 버전별 설치 방식에서는 **모든 파일이 같은 `packages/<64자리 식별자>/` 하위**에 있어야 합니다. 호스트는 해당 디렉터리의 파일 목록과 각 SHA-256을 확인합니다. 이전 버전 디렉터리와 루트의 설치 소유권 표식은 실행 대상이 아닙니다. 폴더 이름 자체를 파일 무결성 증명으로 사용하지 않습니다.
- 단순 패키지는 `worker.py`, `ui.html`, `vendor/...`를 루트에 둘 수도 있습니다. 이 경우 루트의 `extension.json` 외 모든 파일이 선언되어야 합니다.
- 설치 수 16개, manifest 128 KiB, 선언 파일 512개, 전체 패키지 64 MiB, 화면 1 MiB를 상한으로 사용합니다. 링크·junction·경로 이탈·대소문자 중복·검증되지 않은 추가 파일을 거부합니다.

업데이트할 때 새 버전 디렉터리를 완성하고 검증한 다음 `extension.json`만 원자적으로 교체하세요. 실행 중인 이전 패키지 파일을 수정하거나 삭제하지 마세요. 새 목록은 앱을 다시 열거나 인증된 목록 새로고침으로 확인합니다. 설치 제거 도구는 자신이 설치한 파일만 처리해야 하며, 사용자 데이터 삭제는 별도의 명시적 선택이어야 합니다.

## 화면과 요청

공통 앱은 설치된 확장이 있을 때만 UI 모듈과 범용 메뉴 CSS를 불러오고, 확장 메뉴를 선택했을 때만 실제 화면을 엽니다. 일반 대화에서도 설치된 확장 메뉴는 표시하며 기존 업무 전용 메뉴 정책은 유지합니다. 확장은 스킬과 기능처럼 주 메뉴에서 전환하는 본문 페이지이며 전체 화면을 막는 팝업이 아닙니다. 선택한 업무·진행 중인 응답·입력 초안은 유지합니다. 업무·대화·스킬 화면으로 이동하면 iframe과 수신기를 정리합니다. 화면은 앱과 같은 로컬 서버가 발급한 **30초·1회용 티켓**으로 엽니다. 티켓 URL은 저장하거나 공유하지 않습니다.

```text
GET  /api/extensions
POST /api/extensions/{id}/view      {}
POST /api/extensions/{id}/action    {action, payload, sessionId?, directoryToken?}
POST /api/extensions/{id}/directory {}  # 사용자 네이티브 폴더 선택
GET  /extension-ui/{id}?ticket=...  # 발급된 티켓의 최초 소비만 허용
```

`/api/` 요청은 앱의 실행별 인증과 origin 검사를 통과해야 합니다. 확장은 앱 인증 토큰을 받지 않습니다. iframe은 `sandbox="allow-scripts"`로 열리며 `allow-same-origin`은 부여하지 않습니다. 화면에는 다음 제약을 적용합니다.

- JavaScript와 CSS는 HTML 안에 포함합니다. 별도 스크립트·스타일·폰트·iframe 로드는 허용하지 않습니다.
- iframe의 직접 네트워크 연결, 폼 전송, 앱 DOM/API 접근은 허용하지 않습니다. 이미지가 필요하면 `data:` 이미지를 사용합니다.
- 특정 폴더 선택은 `host.pickDirectory`에 빈 payload로 요청합니다. 호스트가 네이티브 선택 창을 열고 `{cancelled:false, path, directoryToken}` 또는 `{cancelled:true}`를 반환합니다. iframe이 입력한 임의 경로는 사용하지 않습니다. 후속 작업 payload의 `directoryToken`은 부모가 분리하여 서버 최상위 인자로 보내며, worker는 서버가 재검증한 `context.selectedDirectory`만 사용해야 합니다. 토큰은 확장에 귀속되고 15분 후 또는 같은 확장에서 다른 폴더를 선택하면 만료됩니다. 설치 범위·실제 대상 확인은 별도로 필요합니다.
- 외부 웹 링크는 부모에게 `host.openExternal` 요청으로 전달합니다. HTTP(S)만 허용하며 인증정보·민감한 쿼리·로컬 앱 주소는 거부합니다. 새 창이 열리지 않으면 부모가 명시적인 링크를 보여줍니다.

메시지 흐름은 다음과 같습니다.

```javascript
// 1. 화면이 준비되었을 때
parent.postMessage({type: "ax-extension-ready"}, "*");

// 2. 부모의 초기화 메시지
// {type:"ax-extension-init", channel:"무작위 채널", hostApi:1,
//  theme:"light"|"dark", context:{workspace, title, kind}}

// 3. 받은 channel로 요청하며 requestId를 재사용하지 않습니다.
parent.postMessage({
  type: "ax-extension-request", channel, requestId: "request-1",
  action: "status", payload: {}
}, "*");

// 4. 부모 응답
// {type:"ax-extension-response", channel, requestId:"request-1",
//  ok:true, result:{...}}
// 또는 {ok:false, error:"안내 문구", ...}
```

확장 화면은 `event.source === parent`와 채널을 확인해야 합니다. 부모 역시 해당 iframe의 Window·opaque origin·채널·동작 allowlist를 확인합니다. 화면을 닫으면 메시지 수신기, 테마 관찰자와 iframe을 제거합니다. 같은 `requestId`의 변경 작업은 재실행하지 않습니다. 테마 변경은 `ax-extension-theme` 메시지로 전달합니다.

## 유한 작업 프로세스

명시적인 동작마다 현재 앱의 Python으로 다음 명령을 실행합니다. OS shell은 사용하지 않습니다.

```text
python -I -B -X utf8 <검증한 패키지의 worker.py>
```

`-I` 때문에 패키지 의존 모듈 경로가 필요하면 worker가 자기 파일 위치를 기준으로 검증된 `vendor` 경로를 직접 추가해야 합니다. `-B`로 설치 디렉터리에 bytecode 캐시를 남기지 않습니다. 필요한 의존 모듈은 별도 패키지에 포함하며 공통 앱에 조직별 패키지를 추가하지 않습니다.

stdin으로 UTF-8 JSON 하나를 전달합니다.

```json
{
  "protocol": 1,
  "action": "status",
  "payload": {},
  "dataDirectory": "확장 소유 사용자 데이터 폴더의 절대 경로",
  "context": {"workspace": null}
}
```

업무가 명시적으로 선택되어 있고 기존 폴더 신뢰가 유효한 경우에만 서버가 확인한 해당 업무의 `workspace`를 전달합니다. 일반 대화·미선택·신뢰 미확인은 `null`입니다. 임의 요청 경로, 대화 본문, Claude 인증정보, 앱 토큰을 전달하지 않습니다. 화면의 표시용 context만 믿고 설치 경로를 정하지 말고 **worker stdin의 서버 확인 context**를 사용하세요.

stdout은 다음 중 하나의 JSON 객체만 출력합니다.

```json
{"ok": true, "result": {"ready": true}}
```

```json
{"ok": false, "error": "사용자가 조치할 수 있는 오류 안내"}
```

진단 출력·진행 로그·비밀 값을 stdout에 섞지 않습니다. 호스트는 stderr 원문을 화면으로 전달하지 않습니다. 입력 payload는 240 KiB, 캡처 출력은 스트림별 2 MiB, 실행 시간은 120초로 제한합니다. 환경변수는 실행에 필요한 OS 항목만 넘기며 Claude·앱·임의 서비스 토큰을 상속하지 않습니다. 사용자 KEY 저장·회수·마스킹은 확장 책임이며 Windows에서는 사용자 범위 DPAPI 같은 저장 방식을 사용하세요.

같은 확장에는 한 번에 하나의 작업만 실행됩니다. 다른 확장과 채팅의 공용 잠금을 보유한 채 기다리지 않습니다. Windows 작업 프로세스는 숨김 실행하고 소유 Job으로 자손을 관리합니다. **정상 반환 때도 자손을 정리하므로 외부 설치기나 상시 서버를 시작한 뒤 즉시 반환하는 방식은 지원하지 않습니다.** 외부 EXE/MSI는 다운로드와 직접 실행 안내를 분리하거나 별도 설계를 사용하세요.

화면을 닫는 것만으로 이미 시작한 파일 변경 작업을 취소하지 않습니다. 앱 전체 종료는 진행 중 동작을 감지해 기존 종료 확인에 포함하고, 확인된 종료 시 소유 프로세스를 정리합니다. 종료·시간 초과·결과 불확실 시 `verificationRequired: true` 오류를 반환하며 요청을 자동 재전송하지 않습니다. 확장은 내구성 있는 설치 기록·준비안·상태 확인과 복구 절차를 제공해야 합니다. 로컬 파일이나 설정을 변경할 때는 범위와 대상, 변경할 설정을 먼저 보여주고 확인받으세요.

## 신뢰 경계

iframe과 RPC의 격리는 **Python worker에 대한 OS 샌드박스가 아닙니다.** 설치한 worker는 앱을 실행한 Windows 계정 권한으로 동작합니다. SHA-256 검사는 설치 파일이 manifest와 같은지 확인할 뿐 작성자 신뢰나 전자서명을 대신하지 않습니다. 검토하고 신뢰하는 배포자가 만든 확장만 명시적으로 설치해야 합니다. 공통 앱은 확장 설치를 이유로 관리자 권한·기존 Claude 승인 모드·보안 정책을 자동 변경하지 않습니다.

## 확인 항목

공통 회귀 테스트:

```powershell
python -X utf8 -B -m unittest tests.test_workspace_extensions tests.test_workspace_extensions_frontend
```

새 확장은 별도 임시 상태와 임시 사용자 홈에서 다음을 확인하세요: 미설치 영향 없음, 다른 업무의 응답성, 새 창/닫기, 허용되지 않은 요청 차단, 파일 검증 실패, 설치 범위 확인, 중복 요청, 버전 교체, 앱 종료·타임아웃 후 상태 확인, 기존 설정 보존 및 복구. 실제 사용자 프로필 설치나 운영 서버 변경은 검증의 기본 조건으로 삼지 않습니다.
