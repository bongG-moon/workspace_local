"""Explicit synthetic UX rehearsal, never a substitute for production validation."""
import time
import uuid


def run(app, sid, text):
    app.emit(sid, "connected", {"model": "가상 연결 · AI 미호출", "skills": ["company-agent:html-report (예시)"], "plugins": [], "mcp": []})
    app.emit(sid, "status", {"state": "running", "label": "화면 체험을 준비하고 있어요"})
    time.sleep(.4)
    if app.get(sid)["state"] == "stopped":
        return
    app.emit(sid, "assistant", {"text": "이것은 가상 자료로 보고서를 만드는 화면 체험입니다. 실제 파일은 만들지 않아요.\n먼저 원하는 정리 방식을 선택해 주세요."})
    app.emit(sid, "request", {"id": "demo-question-" + uuid.uuid4().hex, "tool": "AskUserQuestion", "input": {"questions": [{
        "question": "어떤 형태로 정리할까요?", "multiSelect": False,
        "options": [{"label": "핵심 요약", "description": "중요한 내용 위주로 간결하게"}, {"label": "항목별 정리", "description": "항목마다 자세하게"}]}]}})


def respond(app, sid, rid, allow, answers):
    with app.lock:
        item = app.get(sid)
        request = item["requests"].get(rid)
        if not request:
            raise ValueError("이미 처리되었거나 만료된 질문입니다.")
        if allow and request["tool"] == "AskUserQuestion" and not (answers or {}).get("어떤 형태로 정리할까요?"):
            raise ValueError("정리 방식을 선택해 주세요.")
        app.emit(sid, "request_closed", {"id": rid})
        if not allow:
            app.emit(sid, "assistant", {"text": "이번 요청을 취소했어요. 실제 파일이나 권한은 변경하지 않았습니다."})
            app.emit(sid, "result", {})
        elif request["tool"] == "AskUserQuestion":
            app.emit(sid, "request", {"id": "demo-approval-" + uuid.uuid4().hex, "tool": "가상 보고서 파일 생성",
                "title": "승인 화면 체험", "description": "승인해도 실제 파일 생성·AI 호출은 발생하지 않습니다.",
                "input": {"작업": "정리 방식 선택 → 파일 생성 승인 → 결과 확인", "선택": answers}})
        else:
            app.emit(sid, "assistant", {"text": "화면 체험을 마쳤어요.\n\n✓ 질문에 답변하기\n✓ 실행 내용을 보고 이번만 승인하기\n✓ 결과를 확인하고 같은 대화 이어가기\n\n실사용 모드에서는 이 자리에 기존 Claude Code의 실제 응답이 표시됩니다. 결과 파일 생성은 이번 체험에서 검증하지 않았습니다."})
            app.emit(sid, "result", {"durationMs": 0, "usage": {}})
