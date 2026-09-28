import os
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.html_preview import render

# Core-generated examples are optional; sanitizer contracts always run.
CORE = Path(os.environ.get('COMPANY_AGENT_SOURCE', '')).expanduser().resolve() if 'COMPANY_AGENT_SOURCE' in os.environ else ROOT / 'company-agent-plugin'
if not all((CORE / name).is_file() for name in ('scripts/harness_cli.py', 'scripts/company_agent/workspace_api.py', 'scripts/company_agent/skill_registry.py')):
    if 'COMPANY_AGENT_SOURCE' in os.environ:
        raise RuntimeError('COMPANY_AGENT_SOURCE must point to a complete Company Agent plugin source root (containing scripts/harness_cli.py).')
    CORE = None
CORE_SKIP = 'Optional Company Agent integration: set COMPANY_AGENT_SOURCE to its plugin source root.'
if CORE is not None:
    sys.path.insert(0, str(CORE / 'scripts'))
    from company_agent.report_styles import picker_html


class HtmlWorkspacePreviewTests(unittest.TestCase):
    def test_arbitrary_attachments_remain_source(self):
        self.assertIsNone(render('<html><script>alert(1)</script><body>첨부 양식</body></html>'))

    @unittest.skipUnless(CORE is not None, CORE_SKIP)
    def test_picker_has_static_cards_without_active_content(self):
        preview = render(picker_html())
        self.assertIn('글래스모피즘', preview)
        self.assertNotIn('<script', preview)
        self.assertIn('script-src &#x27;none&#x27;', preview)
        self.assertIn('disabled=', preview)

    def test_forged_report_format_cannot_execute_or_navigate(self):
        text = '''<html><head><meta http-equiv="refresh" content="0;url=https://evil.invalid">
        <base href="https://evil.invalid"><style>body{background:url(https://evil.invalid/bg)}</style></head>
        <body data-style="minimalism" data-view="slides" onload="parent.document.body.remove()">
        <main class="report-main"><section class="section">안전한 본문</section></main>
        <script>fetch('/api/quit')</script><iframe src="/api/bootstrap"></iframe><object data="/api/bootstrap"></object>
        <a href="https://evil.invalid">link</a><img src="https://evil.invalid/leak" onerror="alert(1)">
        <svg><a xlink:href="https://evil.invalid"><animate attributeName="href" values="https://evil.invalid"/></a></svg>
        <form action="https://evil.invalid"><button>send</button></form></body></html>'''
        preview = render(text)
        for value in ('<script', '<iframe', '<object', '<form', 'onload=', 'onerror=', 'href=', '<animate', 'http-equiv="refresh"', '/api/'):
            self.assertNotIn(value, preview)
        self.assertIn('data-view="scroll"', preview)
        self.assertIn('안전한 본문', preview)
        self.assertIn("default-src &#x27;none&#x27;", preview)
        self.assertNotIn('evil.invalid', preview)

    @unittest.skipUnless(CORE is not None, CORE_SKIP)
    def test_ppt_draft_and_saved_template_are_isolated_static_previews(self):
        from company_agent import business_artifacts, presentation_design, ppt_html
        spec = {'slides':[{'title':'PPT 초안','body':'가상 본문'}]}
        data = business_artifacts._normalize(spec)
        presentation_design.prepare(spec,data)
        data['presentationPlan'] = presentation_design.plan(data)
        original = ppt_html.render(data,metadata={'schema':'test'})
        preview = render(original)
        self.assertIn('PPT 초안',preview)
        self.assertIn('class="ppt-slide"',preview)
        self.assertNotIn('<script',preview)
        hostile = original.replace('</body>','<script>alert(1)</script><img src="https://evil.invalid/leak"></body>')
        self.assertNotIn('evil.invalid',render(hostile))


if __name__ == '__main__':
    unittest.main()
