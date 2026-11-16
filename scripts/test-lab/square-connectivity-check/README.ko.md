# P&T AI SQUARE 연결 점검

최종 전달 파일은 **`ai_square_connection_check.py` 하나**입니다. Python 3.8 이상에서 실행하며 추가 패키지는 설치하지 않습니다.

1. 파일을 저장한 폴더에서 `python ai_square_connection_check.py`를 실행합니다. VS Code에서 이 파일을 열어 실행해도 됩니다.
2. 콘솔에 P&T AI SQUARE 기본 웹 주소를 입력합니다. 기존 포트와 하위 배포 경로는 포함합니다. 기본 주소 뒤에 `/health`를 붙여 GET 요청 한 번만 보냅니다. 이미 `/health`로 끝나면 그대로 사용합니다.
3. 콘솔의 판정과 안내를 확인합니다. 저장 옵션이 없으면 결과 파일을 생성하지 않습니다.

결과를 파일로 보관하려면 아래처럼 실행한 뒤 웹 주소를 입력합니다. `result.json`은 사용자 PC에 저장하는 로컬 파일이며 DB 기록이 아닙니다.

```shell
python ai_square_connection_check.py --report result.json
```

주소도 명령에 지정할 수 있습니다. HTTP 운영 주소는 `http://`를 그대로 사용합니다.

```shell
python ai_square_connection_check.py --url "http://웹주소:포트/기본경로" --report result.json
```

## 저장 없는 POST 인증 시험

새 집계 API를 웹에 반영하고 등록용 전용 키를 설정한 뒤 아래 명령을 실행합니다. 기존 개인 KEY와는 다른 전용 키입니다. 웹 서버 코드는 별도 [P&T AI SQUARE 저장소](https://github.com/bongG-moon/ai_square)에 있습니다. [집계 API 운영 안내](https://github.com/bongG-moon/ai_square/blob/main/pt_ai_square/docs/APP_REGISTRATION_API.md)를 참고합니다.

```shell
python ai_square_connection_check.py --post-test --report post_result.json
```

웹 주소를 입력한 뒤 서버와 같은 등록용 키를 숨김 입력합니다. 키를 명령줄에 직접 넣는 옵션은 없습니다. 자동화에서는 `AX_REGISTRATION_KEY` 환경변수로 전달합니다. 키를 숨겨 입력할 수 없는 환경에서는 평문 입력으로 전환하지 않고 종료합니다.

이 모드는 `/api/ax-workspace/registrations`에 합성 자료와 `dryRun=true`만 담은 서명 POST를 한 번 보냅니다. 실제 사번은 입력받지 않고 실제 DB 저장 옵션도 없습니다. 먼저 GET을 보내거나 자동으로 로그인하지 않습니다. 정확한 응답이 오면 `post_test_ok`와 `dry_run_verified`를 표시합니다. 운영 앞단까지 통과했는지는 사내 주소에서 이 결과가 나오는지 확인해야 합니다.

로그인 화면의 주소, 계정이 들어간 주소, 쿼리(`?`)와 조각(`#`)은 받지 않습니다. 주소에 프로토콜이 없으면 HTTPS로 확인하며 HTTP로 자동 전환하지 않습니다. 실제 웹이 HTTP로 운영되면 주소에 `http://`를 명시합니다.

## DB 기록과 네트워크 범위

- 현재 웹 소스의 `GET /health`는 이전 상태 조회와 MongoDB `ping`만 실행하며 사용자 생성, 활동 기록, 집계 문서 저장 경로를 사용하지 않습니다.
- 프로그램은 웹 서버를 시작하거나 변경하지 않고, MongoDB에 직접 연결하지 않습니다. 실제 사번, DB URI와 DB 비밀번호는 입력받지 않습니다. `--post-test`에서만 집계 전용 인증키를 숨김 입력합니다.
- 기본 모드는 GET 한 번, `--post-test`는 저장 없는 POST 한 번만 실행합니다. `/`, `/login`, `/api/bootstrap`, 실제 집계 저장, 다운로드, 다른 주소로의 자동 이동과 자동 재시도는 실행하지 않습니다.
- 브라우저 쿠키, 사이트 로그인과 Windows 기본 인증정보를 전송하지 않습니다. Python 표준 라이브러리의 시스템/환경변수 프록시 설정을 사용하고, 프록시 주소에 포함된 계정과 비밀번호는 제거합니다. 자동 프록시 스크립트(PAC)와 Windows 통합 인증을 브라우저와 동일하게 처리한다고 보장하지 않습니다. 프록시가 인증을 요구하면 해당 제한으로 표시합니다.
- Python의 기본 인증서 검증을 유지합니다. 응답과 gzip 해제 결과는 각각 최대 64KB까지만 메모리로 읽습니다. 기본 네트워크 대기시간은 약 7초이며 DNS/프록시 처리와 응답 종류에 따라 전체 종료까지 더 걸릴 수 있습니다.
- 서버 본문 전체와 Set-Cookie 헤더를 저장하지 않습니다. 결과에는 입력한 기본 주소, 응답 코드, 허용된 상태 필드와 판정만 포함합니다. 이동 주소, 전용 키, 서명, 전송 본문과 사번은 결과에 포함하지 않습니다.
- 웹 서버와 회사 인프라의 일반 접속 로그, DB 감사/진단 로그까지 생기지 않는다는 뜻은 아닙니다. 업무 및 사용자 집계 문서의 저장 요청을 보내지 않는 점검입니다.

## 결과 해석

`기본 연결 확인`은 HTTP 200, `service=P&T AI SQUARE`, `status=ok`, `storage=mongodb`가 일치한 경우입니다. 현재 웹 코드 기준 MongoDB ping 성공도 확인됩니다. 실제 등록 POST, 앱 인증과 동시 업데이트 부하는 검사하지 않습니다.

POST 시험 성공은 HTTP 200과 `service=P&T AI SQUARE`, `protocol=ax-registration-v1`, `status=ok`, `authenticated=true`, `dryRun=true`, `persisted=false`가 모두 확인된 경우입니다. 이 모드에서 `mongoPingConfirmed`는 False이며 실제 DB 쓰기 권한과 중복 집계 방지까지 확인했다고 표시하지 않습니다.

더미 모드, 다른 JSON, HTML, 이동 응답, 401/403/407, 404, 서버 오류, 연결·인증서 오류와 시간 초과는 각각 구분합니다. 401/403만으로 회사 앞단과 앱 중 어느 쪽이 막았는지는 단정하지 않습니다. 404도 집계 구현 불가를 뜻하지 않습니다.

## 웹 수정 여부

기본 GET 점검은 기존 `/health`를 사용하므로 웹 수정과 재배포가 필요하지 않습니다. 새 `--post-test`는 이번에 구현한 웹 집계 API 코드와 등록용 키 설정을 반영하고 기존 방식으로 웹을 재시작한 뒤 사용합니다. 기존 웹과 같은 포트를 유지합니다.

## 개발자 검증

`test_python_probe.py`와 `test_registration_probe.py`는 가짜 HTTP 서버만 사용하고 실제 웹/DB/개인 설정에는 연결하지 않습니다. 테스트 실행기만 Python 3.10 이상이 필요하며, 전달 파일의 구문은 Python 3.8 기준으로 확인합니다.


이 폴더에서 다음 명령으로 회귀 검증을 실행합니다. `probe_fixtures.py`는 GET 검증에 사용하는 가짜 HTTP 서버와 공통 사례이며 전달 파일의 실행 의존성이 아닙니다.

```shell
python -X utf8 -B test_python_probe.py --source ai_square_connection_check.py
python -X utf8 -B test_registration_probe.py
```
