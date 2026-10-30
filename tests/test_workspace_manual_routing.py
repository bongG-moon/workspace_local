"""The two user-guide entrypoints share the fixed native manual route."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuideLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.dialog = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'dialog':
            self.dialog = values.get('id')
        if tag == 'a' and values.get('href', '').startswith('/manual/'):
            self.links.append((self.dialog, values))

    def handle_endtag(self, tag):
        if tag == 'dialog':
            self.dialog = None


class ManualRoutingTests(unittest.TestCase):
    def test_help_and_settings_use_the_same_token_free_blank_target(self):
        parser = GuideLinks()
        parser.feed((ROOT / 'local_app/web/index.html').read_text(encoding='utf-8'))
        self.assertEqual({'help-dialog', 'settings-dialog'}, {dialog for dialog, _ in parser.links})
        full_guides = [(dialog, values) for dialog, values in parser.links if values['href'] == '/manual/guide']
        self.assertEqual(2, len(full_guides))
        self.assertEqual({'help-dialog', 'settings-dialog'}, {dialog for dialog, _ in full_guides})
        for dialog, values in parser.links:
            with self.subTest(dialog=dialog):
                # Help also links directly to guide sections; all links retain
                # the same fixed, token-free route and safe external target.
                self.assertIn(values['href'].split('#', 1)[0], {'/manual/guide', '/manual/AX_WORKSPACE_STORY.html'})
                self.assertEqual('_blank', values['target'])
                self.assertTrue({'noopener', 'noreferrer'}.issubset(values['rel'].split()))

    def test_help_section_links_resolve_in_shipped_guide(self):
        parser = GuideLinks()
        parser.feed((ROOT / 'local_app/web/index.html').read_text(encoding='utf-8'))
        class AnchorIds(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = set()
            def handle_starttag(self, tag, attrs):
                value = dict(attrs).get('id')
                if value:
                    self.ids.add(value)
        guide = AnchorIds()
        guide.feed((ROOT / 'docs/WORKSPACE_USER_GUIDE.html').read_text(encoding='utf-8'))
        for _, values in parser.links:
            path, _, anchor = values['href'].partition('#')
            if path == '/manual/guide' and anchor:
                with self.subTest(anchor=anchor):
                    self.assertIn(anchor, guide.ids)

    def test_claude_error_guide_has_findable_cases_and_recovery_guidance(self):
        class ErrorGuide(HTMLParser):
            def __init__(self):
                super().__init__(); self.depth = 0; self.cases = []; self.current = None; self.in_summary = False
            def handle_starttag(self, tag, attrs):
                if tag == 'details' and dict(attrs).get('id') == 'claude-error-help':
                    self.depth = 1
                elif self.depth and tag == 'details':
                    self.depth += 1
                    if self.depth == 2: self.current = {'summary': '', 'body': ''}; self.cases.append(self.current)
                elif self.depth == 2 and tag == 'summary': self.in_summary = True
            def handle_endtag(self, tag):
                if self.depth == 2 and tag == 'summary': self.in_summary = False
                elif self.depth and tag == 'details':
                    self.depth -= 1
                    if self.depth == 1: self.current = None
            def handle_data(self, data):
                if self.current is not None:
                    self.current['summary' if self.in_summary else 'body'] += data
        guide = ErrorGuide()
        guide.feed((ROOT / 'docs/WORKSPACE_USER_GUIDE.html').read_text(encoding='utf-8'))
        self.assertEqual(10, len(guide.cases))
        for case in guide.cases:
            with self.subTest(case=case['summary']):
                self.assertIn('가능한 원인:', case['body'])
                self.assertIn('확인할 사항:', case['body'])
        labels = ' '.join(c['summary'] for c in guide.cases)
        for keyword in ('인증', '60초', '403', 'ede_diagnostic', '요청 처리 중'):
            self.assertIn(keyword, labels)
        guide_by_case = {case['summary']: case['body'] for case in guide.cases}
        upgrade = guide_by_case['이 Claude Code 버전에는 필요한 대화 연결 기능이 없습니다']
        for instruction in ('PowerShell', 'claude --version', '최신 버전으로 업데이트 해줘', '완전히 종료', '담당자'):
            self.assertIn(instruction, upgrade)
        auth = guide_by_case['기존 Claude Code의 인증을 확인하지 못했습니다']
        for instruction in ('PowerShell', 'claude', '로그인 또는 KEY 갱신', 'cmd /c "claude -p 안녕하세요"', '실행 정책을 변경하지 않습니다', '모델 접근 권한', '터미널에서도 실패', '연결 상세 정보', '완전히 종료', 'KEY나 토큰 값은 전달하지 말고'):
            self.assertIn(instruction, auth)
        self.assertIn('KEY나 토큰 값은 보내지 마세요', guide_by_case['403 team not allowed to access model'])
        self.assertIn('파일 변경을 먼저 확인', guide_by_case['Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요 (ede_diagnostic)'])


if __name__ == '__main__':
    unittest.main()
