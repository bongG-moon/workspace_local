#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P&T AI SQUARE connectivity and authenticated dry-run check.

Python 3.8+, stdlib only. Default: GET <base>/health. Explicit --post-test:
one signed registration POST with fixed synthetic data and dryRun=True.
No platform imports, MongoDB connection, real registration option, browser
credentials, cookies, redirects, retries or server startup.
"""
import argparse
import getpass
import gzip
import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import ssl
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
    getproxies,
)

VERSION = "1.1.0-python"
TIMEOUT = 7.0
MAX_BODY = 65536
NOTES = [
    "기존 웹의 /health에 GET 한 번만 보냅니다. DB 저장 요청은 보내지 않습니다.",
    "브라우저 쿠키와 사이트 로그인 정보를 사용하지 않으며 이동 응답을 따라가지 않습니다.",
    "집계용 POST와 등록 인증은 미확인입니다. 이 결과만으로 자동 집계 가능 여부를 확정할 수 없습니다.",
    "웹 서버와 회사 인프라의 일반 접속 로그, DB 감사 로그는 남을 수 있습니다.",
    "기본 네트워크 대기시간은 약 7초입니다. DNS/프록시 처리에 따라 더 걸릴 수 있습니다.",
]
REGISTRATION_PATH = "/api/ax-workspace/registrations"
POST_NOTES = [
    "집계 경로에 합성 시험 자료와 dryRun=true를 담은 POST 한 번만 보냅니다.",
    "등록용 키는 HMAC 서명에만 사용합니다. 키 원문, 서명, 실제 사번은 결과에 포함하지 않습니다.",
    "브라우저 로그인과 쿠키를 사용하지 않으며 다른 주소로 이동하거나 자동 재시도하지 않습니다.",
    "정상 결과는 인증과 저장 없는 POST 처리를 확인합니다. 실제 DB 저장과 중복 방지는 별도 검증 범위입니다.",
    "웹 서버와 회사 인프라의 일반 접속 로그는 남을 수 있습니다.",
    "기본 네트워크 대기시간은 약 7초입니다. DNS/프록시 처리에 따라 더 걸릴 수 있습니다.",
]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def health_url(value):
    """Preserve the existing port/prefix; never accept a login/token URL."""
    text = (value or "").strip()
    if not text or len(text) > 2048:
        raise ValueError("기본 웹 주소를 입력해 주세요.")
    if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in text) or "\\" in text:
        raise ValueError("주소 안의 공백과 제어 문자를 제거해 주세요.")
    if "?" in text or "#" in text:
        raise ValueError("? 또는 # 뒤의 값이 없는 기본 웹 주소를 입력해 주세요.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
        decoded = parts.path
        for _ in range(3):
            decoded = unquote(decoded, errors="strict")
        if any(c in decoded for c in "\\?#%") or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
            raise ValueError()
        reserved = {".", "..", "login", "logout", "api", "download", "static"}
        if any(segment.lower() in reserved for segment in decoded.split("/")):
            raise ValueError()
        path = parts.path.rstrip("/")
        if not path.endswith("/health"):
            path += "/health"
        path = quote(path, safe="/%:@!$&'()*+,;=-._~")
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    except (ValueError, UnicodeError):
        raise ValueError("계정이나 로그인·API 경로가 없는 기본 웹 주소를 입력해 주세요.") from None


def registration_url(value):
    """Reuse base validation and preserve a reverse-proxy deployment prefix."""
    parts = urlsplit(health_url(value))
    prefix = parts.path[:-len("/health")]
    return urlunsplit((parts.scheme, parts.netloc, prefix + REGISTRATION_PATH, "", ""))


def registration_headers(body, key, timestamp=None, nonce=None):
    if not isinstance(key, str) or not 32 <= len(key) <= 256:
        raise ValueError("등록용 인증키는 32~256자로 설정해 주세요.")
    if any(ord(character) < 32 or ord(character) == 127 for character in key):
        raise ValueError("등록용 인증키에 제어 문자를 포함할 수 없습니다.")
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    canonical = "\n".join(("AX-REGISTRATION-V1", "POST", REGISTRATION_PATH,
                            timestamp, nonce, hashlib.sha256(body).hexdigest()))
    try:
        encoded_key = key.encode("utf-8")
    except UnicodeError:
        raise ValueError("등록용 인증키를 UTF-8 문자로 입력해 주세요.") from None
    signature = hmac.new(encoded_key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-AX-Timestamp": timestamp,
            "X-AX-Nonce": nonce, "X-AX-Signature": signature}


def proxy_settings():
    """Respect stdlib system/environment proxies, without embedded credentials."""
    clean = {}
    for scheme, value in getproxies().items():
        if scheme == "no":
            clean[scheme] = value
            continue
        if not isinstance(value, str):
            continue
        try:
            parts = urlsplit(value if "://" in value else "http://" + value)
            if not parts.hostname:
                continue
            host = parts.hostname
            if ":" in host:
                host = "[" + host + "]"
            if parts.port is not None:
                host += ":" + str(parts.port)
            clean[scheme] = urlunsplit((parts.scheme, host, "", "", ""))
        except ValueError:
            continue
    return clean


def classify(result, category, title, summary):
    result.update(category=category, title=title, summary=summary)
    return result


def read_body(response, deadline):
    raw = bytearray()
    read = getattr(response, "read1", response.read)
    while len(raw) <= MAX_BODY:
        if time.monotonic() >= deadline:
            raise TimeoutError()
        chunk = read(min(4096, MAX_BODY + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
    if len(raw) > MAX_BODY:
        raise OverflowError()
    encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
    if encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise OverflowError()
    elif encoding not in ("", "identity"):
        raise ValueError("unsupported content encoding")
    return bytes(raw)


def interpret(response, result, deadline):
    status = int(response.code)
    result["httpStatus"] = status
    result["contentType"] = response.headers.get("Content-Type", "")[:160]
    if 300 <= status < 400:
        # Location may include employee IDs/tokens. Do not follow or export it.
        return classify(result, "redirect", "다른 주소로 이동하는 응답입니다",
                        "로그인 또는 주소 변경 안내일 수 있습니다. 이동을 따라가지 않았습니다.")
    messages = {
        401: ("unauthorized", "인증 또는 접근 제한을 확인해 주세요"),
        403: ("unauthorized", "인증 또는 접근 제한을 확인해 주세요"),
        407: ("proxy_auth", "회사 프록시 인증이 필요합니다"),
        404: ("not_found", "상태 확인 경로를 찾지 못했습니다"),
    }
    if status in messages:
        category, title = messages[status]
        if result.get("mode") == "post_test" and status in (401, 403):
            return classify(result, "registration_auth_failed", "등록용 인증을 확인해 주세요",
                            "서버와 시험 파일의 키, PC와 서버의 시간 차이를 확인해 주세요. 회사 앞단 제한 여부는 서버 로그로 구분합니다.")
        summary = ("기본 주소와 하위 배포 경로, 웹 서버 버전을 확인해 주세요. 집계 구현 불가를 뜻하지는 않습니다."
                   if status == 404 else
                   "회사 앞단과 웹 내부 중 어디서 제한했는지는 서버 로그로 확인해야 합니다. 로그인 정보는 전송하지 않았습니다.")
        return classify(result, category, title, summary)
    if status >= 500:
        if result.get("mode") == "post_test" and status == 503:
            return classify(result, "registration_unavailable", "등록 점검 기능을 사용할 수 없습니다",
                            "서버의 집계 기능 활성화 설정과 인증키 설정, 서버 상태를 확인해 주세요.")
        return classify(result, "server_error", "서버가 오류를 반환했습니다",
                        "웹 또는 DB 연결 상태를 서버 담당자가 확인해야 합니다. 응답 코드만으로 원인을 단정할 수 없습니다.")
    if status != 200:
        return classify(result, "http_error", "예상과 다른 HTTP 응답입니다",
                        "정상 응답은 HTTP 200과 이 점검 모드에 맞는 P&T AI SQUARE JSON입니다.")
    length = response.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > MAX_BODY:
        raise OverflowError()
    body = read_body(response, deadline).decode("utf-8-sig", errors="replace").lstrip()
    content_type = result["contentType"].split(";", 1)[0].strip().lower()
    if content_type == "text/html" or body.startswith("<"):
        return classify(result, "html_response", "상태 정보 대신 웹 화면이 반환됐습니다",
                        "로그인 화면 또는 다른 페이지일 수 있습니다. HTML은 실행하거나 표시하지 않았습니다.")
    data = None
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            pass
    if isinstance(data, dict):
        if result.get("mode") == "post_test":
            if (data.get("service") == "P&T AI SQUARE"
                    and data.get("protocol") == "ax-registration-v1"
                    and data.get("status") == "ok"
                    and data.get("dryRun") is True
                    and data.get("persisted") is False
                    and data.get("authenticated") is True):
                result.update(platformConfirmed=True, collectionReadiness="dry_run_verified")
                return classify(result, "post_test_ok", "인증과 저장 없는 POST 시험이 통과했습니다",
                                "기존 포트의 집계 경로에서 등록용 인증과 dry-run 응답을 확인했습니다. DB 저장 요청은 실행하지 않았습니다.")
            return classify(result, "unexpected_response", "POST 응답 확인이 필요합니다",
                            "인증 완료와 dryRun=true, persisted=false 응답이 모두 확인되지 않았습니다. 실제 집계 저장 성공으로 판단하지 않았습니다.")
        result["health"] = {key: data[key][:80] for key in ("status", "service", "storage", "version")
                            if isinstance(data.get(key), str)}
        if data.get("service") == "P&T AI SQUARE" and data.get("status") == "ok":
            if data.get("storage") == "mongodb":
                result.update(platformConfirmed=True, mongoPingConfirmed=True)
                return classify(result, "health_ok", "기본 연결이 확인됐습니다",
                                "로그인 없는 상태 확인 경로와 웹 서버의 MongoDB ping이 정상입니다. 집계 API의 POST와 등록 인증은 아직 미확인입니다.")
            if data.get("storage") == "dummy":
                result["platformConfirmed"] = True
                return classify(result, "dummy_mode", "시험 화면 서버에 연결됐습니다",
                                "더미 모드입니다. 실제 MongoDB 연결과 자동 집계 가능 여부는 확인되지 않았습니다.")
    if result.get("mode") == "post_test":
        return classify(result, "unexpected_response", "POST 응답 확인이 필요합니다",
                        "예상한 집계 API JSON 응답이 아닙니다. 인증과 저장 없는 처리가 확인되지 않았습니다.")
    return classify(result, "unexpected_response", "웹 응답은 받았지만 플랫폼 확인이 필요합니다",
                    "정상 P&T AI SQUARE 상태 JSON과 일치하지 않습니다. HTTP 200만으로 성공이라고 판단하지 않았습니다.")


def probe(value, *, post_test=False, key=None):
    start = time.monotonic()
    result = dict(schemaVersion=1, toolVersion=VERSION,
                  checkedAtUtc=datetime.now(timezone.utc).isoformat(),
                  category="invalid_url", title="주소를 확인해 주세요", summary="",
                  mode="post_test" if post_test else "health",
                  requestUrl="", method="POST" if post_test else "GET", requestCount=0, httpStatus=0,
                  durationMs=0, platformConfirmed=False, mongoPingConfirmed=False,
                  collectionReadiness="not_tested", contentType="", health={},
                  notes=list(POST_NOTES if post_test else NOTES))
    try:
        url = registration_url(value) if post_test else health_url(value)
    except ValueError as error:
        result["summary"] = str(error)
        return result
    result["requestUrl"] = url
    body = None
    headers = {"Accept": "application/json", "Accept-Encoding": "identity",
               "User-Agent": "AI-Square-ReadOnly-Connection-Check/" + VERSION}
    if post_test:
        # Deliberately no caller-controlled employee/version/dryRun option.
        body = json.dumps({"schema": 1, "dryRun": True, "employeeId": "TEST",
                           "version": "0.0.0", "event": "initial_install"},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            headers.update(registration_headers(body, key))
        except ValueError as error:
            return classify(result, "invalid_registration_key", "등록용 인증키를 확인해 주세요", str(error))
    try:
        opener = build_opener(ProxyHandler(proxy_settings()), NoRedirect(),
                              HTTPSHandler(context=ssl.create_default_context()))
        request = Request(url, data=body, headers=headers, method=result["method"])
        result["requestCount"] = 1
        try:
            response = opener.open(request, timeout=TIMEOUT)
        except HTTPError as error:
            response = error
        with response:
            return interpret(response, result, start + TIMEOUT)
    except OverflowError:
        return classify(result, "response_too_large", "상태 응답이 예상보다 큽니다",
                        "메모리 사용을 제한하기 위해 응답을 64KB까지만 읽습니다. 로그인 화면이나 다른 경로인지 확인해 주세요.")
    except (TimeoutError, socket.timeout):
        return classify(result, "timeout", "응답 대기 시간이 지났습니다",
                        "서버와 회사 네트워크 경로를 확인해 주세요. 자동 재시도는 하지 않습니다.")
    except ssl.SSLError:
        return classify(result, "tls_error", "보안 연결을 확인해 주세요",
                        "인증서 또는 TLS 연결을 확인하지 못했습니다. 인증서 검증을 우회하지 않았습니다.")
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return classify(result, "timeout", "응답 대기 시간이 지났습니다", "서버와 회사 네트워크 경로를 확인해 주세요.")
        if isinstance(error.reason, ssl.SSLError):
            return classify(result, "tls_error", "보안 연결을 확인해 주세요", "회사 인증서와 TLS 연결 상태를 확인해 주세요. 검증은 우회하지 않았습니다.")
        return classify(result, "network_error", "웹 서버에 연결하지 못했습니다",
                        "주소와 포트, DNS, 회사 프록시와 네트워크 경로를 확인해 주세요.")
    except Exception:
        # Exception/response text may contain internal URLs or credentials.
        return classify(result, "network_error", "점검을 완료하지 못했습니다",
                        "입력 주소와 네트워크 경로를 확인해 주세요. 서버 응답 본문과 내부 오류 전문은 저장하지 않습니다.")
    finally:
        result["durationMs"] = round((time.monotonic() - start) * 1000)


def report_text(result):
    lines = ["", "P&T AI SQUARE 연결 점검", "=" * 48, result["title"], result["summary"], "",
             "요청 주소: " + result["requestUrl"],
             "HTTP 응답: " + (str(result["httpStatus"]) if result["httpStatus"] else "없음"),
             "판정 코드: " + result["category"],
             "소요 시간: %.1f초" % (result["durationMs"] / 1000)]
    lines += ["서버 %s: %s" % (key, value) for key, value in result["health"].items()]
    if result.get("mode") == "post_test":
        lines += ["", "확인 범위: 등록용 인증과 저장 없는 POST / 실제 DB 저장과 중복 방지는 미확인", ""]
    else:
        lines += ["", "확인 범위: 기본 연결만 확인 / 집계 POST와 등록 인증은 미확인", ""]
    lines += ["- " + note for note in result["notes"]]
    lines += ["", ("웹 반영: 집계 API와 등록용 키를 설정한 서버에서 실행하세요. 이번 시험 자료는 저장하지 않습니다."
                   if result.get("mode") == "post_test" else
                   "웹 수정: 기본 연결 점검에는 필요 없습니다. POST 시험에는 집계 API 반영이 필요합니다.")]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="웹 주소만으로 P&T AI SQUARE /health를 읽기 전용으로 점검합니다.")
    parser.add_argument("--url", help="기본 웹 주소. 생략하면 콘솔에서 입력받습니다.")
    parser.add_argument("--report", type=Path, help="선택한 로컬 JSON 파일에 결과 저장. 생략 시 파일을 만들지 않습니다.")
    parser.add_argument("--json", action="store_true", help="콘솔에 JSON 형식으로 결과 출력")
    parser.add_argument("--post-test", action="store_true",
                        help="합성 자료와 dryRun=true로 등록용 인증 POST 시험. DB에 저장하지 않습니다.")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    interactive = args.url is None
    if interactive:
        print("P&T AI SQUARE 연결 점검 | Python 표준 라이브러리만 사용")
        print("등록용 인증과 저장 없는 POST 시험입니다." if args.post_test else
              "기존 웹의 상태만 확인하며 DB 저장 요청은 보내지 않습니다.")
        print("로그인 화면이 아닌 기본 웹 주소를 입력해 주세요.\n")
        try:
            args.url = input("웹 주소: ")
        except (EOFError, KeyboardInterrupt):
            print("\n입력을 취소했습니다.")
            return 2
    key = None
    if args.post_test:
        key = os.environ.get("AX_REGISTRATION_KEY")
        if key is None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    key = getpass.getpass("등록용 인증키 (입력 내용은 표시되지 않습니다): ")
            except getpass.GetPassWarning:
                print("\n이 실행 환경에서는 키를 숨겨 입력할 수 없습니다. 터미널에서 실행하거나 AX_REGISTRATION_KEY 환경변수를 설정해 주세요.", file=sys.stderr)
                return 2
            except (EOFError, KeyboardInterrupt):
                print("\n인증키 입력을 취소했습니다.")
                return 2
    result = probe(args.url, post_test=args.post_test, key=key)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized if args.json else report_text(result))
    if args.report is not None:
        try:
            args.report.write_text(serialized + "\n", encoding="utf-8")
            if not args.json:
                print("\n지정한 로컬 파일에 결과를 저장했습니다.")
        except OSError:
            print("\n결과 파일을 저장하지 못했습니다. 저장 경로와 권한을 확인해 주세요.", file=sys.stderr)
            return 2
    if interactive and sys.stdin.isatty():
        try:
            input("\n확인이 끝나면 Enter를 눌러 종료하세요.")
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
