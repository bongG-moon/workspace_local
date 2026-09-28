"""A deterministic local child process for transport tests. No network or LLM."""
import json
import os
from pathlib import Path
import sys
import uuid

if "--version" in sys.argv:
    print("test-cli 1.0")
    sys.exit(0)
if "--help" in sys.argv:
    print('--input-format --output-format --setting-sources --permission-prompt-tool --include-partial-messages "manual"')
    sys.exit(0)
if "--print" in sys.argv and "--verbose" not in sys.argv:
    print("Missing --verbose: shell wrapper consumed a CLI argument", file=sys.stderr)
    sys.exit(2)

session = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--resume=")), str(uuid.uuid4()))
turn = 0
model = "fake-only"


def emit(data):
    sys.stdout.buffer.write((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


for raw in sys.stdin.buffer:
    value = json.loads(raw)
    if value["type"] == "control_request":
        subtype = value["request"]["subtype"]
        if subtype == "set_model":
            requested = value["request"].get("model")
            if requested == "fake-rejected":
                emit({"type": "control_response", "response": {"subtype": "error", "request_id": value["request_id"], "error": "Model not available"}})
                continue
            if requested == "fake-timeout":
                continue
            model = requested
        detail = ({"models": [{"value": "fake-only", "displayName": "Local test model"},
                               {"value": "fake-alternative", "displayName": "Alternative test model"}],
                   "commands": [{"name": "test-skill", "description": "Local fixture command"}]}
                  if subtype == "initialize" else {})
        emit({"type": "control_response", "response": {"subtype": "success", "request_id": value["request_id"], "response": detail}})
        if subtype == "interrupt":
            break
    elif value["type"] == "user":
        turn += 1
        catalog = {}
        if os.environ.get('WORKSPACE_FAKE_CATALOG') == '1':
            catalog = {
                'skills': [{'name': 'company-agent:html-report', 'description': '가상 목록: 자료를 정리해 HTML 보고서를 만드는 스킬'},
                           {'name': 'my-fixture', 'description': '가상 목록: 개인 업무에 맞춘 문서 검토 스킬'}],
                'tools': [{'name': 'Read', 'description': '가상 목록: 선택한 파일의 내용을 읽습니다.'},
                          {'name': 'Write', 'description': '가상 목록: 업무 결과를 파일로 저장합니다.'},
                          {'name': 'mcp__fixture_docs__search', 'description': '가상 목록: 연결한 문서에서 필요한 정보를 검색합니다.'}],
                'mcp_servers': [{'name': 'fixture_docs', 'status': 'connected'},
                                {'name': 'fixture_offline', 'status': 'failed'}],
            }
        emit({"type": "system", "subtype": "init", "session_id": session, "model": model,
              "skills": ["company-agent:html-report"], "plugins": [{"name": "company-agent"}], "mcp_servers": [], **catalog})
        text = value["message"]["content"]
        if text == "STREAM_PROTOCOL_TEST":
            mid = "fixture-message-" + str(turn)
            if "--include-partial-messages" in sys.argv:
                emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid}}})
                emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}}})
                for chunk in ["부분 ", "응답 ", "확인"]:
                    emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": chunk}}})
            emit({"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": "부분 응답 확인"}]}})
            emit({"type": "result", "session_id": session, "is_error": False, "result": "부분 응답 확인"})
            continue
        if text == "AUTH_PARITY_CHECK" and os.environ.get("WORKSPACE_FAKE_AUTH") == "1":
            valid = (Path(os.environ["CLAUDE_CONFIG_DIR"]) / "fake-auth.txt").read_text(encoding="utf-8") == "valid"
            emit({"type": "result", "session_id": session, "is_error": not valid,
                  "result": "기존 CLI 인증 사용" if valid else "Failed to authenticate: OAuth session expired and could not be refreshed"})
            continue
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "요청 확인: " + text}]}})
        emit({"type": "control_request", "request_id": "q" + str(turn), "request": {
            "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [{
                "question": "정리 방식?", "multiSelect": False, "options": [{"label": "간단히"}, {"label": "자세히"}]}]}}})
    elif value["type"] == "control_response":
        response = value["response"]
        detail = response["response"]
        if response["request_id"].startswith("q") and detail["behavior"] == "allow":
            emit({"type": "control_request", "request_id": "p" + str(turn), "request": {
                "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "FAKE_ONLY", "description": "테스트"}}})
        else:
            emit({"type": "result", "subtype": "success", "session_id": session, "is_error": False,
                  "result": "처리 완료: " + detail["behavior"], "duration_ms": 3, "usage": {"input_tokens": 0, "output_tokens": 0}})
