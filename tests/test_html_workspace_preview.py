import os
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_app.html_preview import render, static_css

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
    def test_general_html_preserves_semantics_without_report_overrides(self):
        preview = render('''<!doctype html><html><body data-view="cards"><nav><button>메뉴</button></nav>
        <h1>첨부 양식</h1><pre><code>&lt;script&gt;인용&lt;/script&gt;</code></pre>
        <a href="https://outside.invalid">참고 문서</a><form action="https://outside.invalid">
        <fieldset><legend>조건</legend><label>이름<input value="홍길동"></label><button>보내기</button></fieldset></form>
        <script>alert(1)</script></body></html>''')
        for value in ('<h1>첨부 양식</h1>', '<pre><code>&lt;script&gt;인용&lt;/script&gt;</code></pre>',
                      '<a>참고 문서</a>', '<form>', '<legend>조건</legend>', 'value="홍길동"', 'disabled=""'):
            self.assertIn(value, preview)
        self.assertIn('data-view="cards"', preview)
        for value in ('nav button{display:none}', '.mini-window{', '<script', 'alert(1)', 'href=', 'action=', 'outside.invalid'):
            self.assertNotIn(value, preview)

    def test_report_markers_use_parsed_attributes_and_class_tokens(self):
        for markup in (
            "<BODY DATA-STYLE = 'minimalism' DATA-VIEW = 'slides'><MAIN CLASS = 'extra report-main other'>본문</MAIN></BODY>",
            '<body data-style=minimalism data-view=slides><main class=report-main>본문</main></body>',
        ):
            with self.subTest(markup=markup):
                preview = render(markup)
                self.assertIn('data-view="scroll"', preview)
                self.assertIn('nav button{display:none}', preview)
                self.assertIn('본문', preview)

    def test_markers_in_comments_text_or_scripts_do_not_change_general_document(self):
        for extra in (
            '<!-- <body data-style="x"><main class="report-main"><div id="additional-designs"><div id="choice-result"> -->',
            '<script>const sample=\'<body data-style="x"><main class="report-main">\';</script>',
            '<pre>&lt;body data-style="x"&gt;&lt;main class="report-main"&gt;</pre>',
            '<main class="normal" class="report-main">중복 속성</main>',
        ):
            with self.subTest(extra=extra):
                preview = render('<body data-style="plain" data-view="cards">' + extra + '</body>')
                self.assertIn('data-view="cards"', preview)
                self.assertNotIn('nav button{display:none}', preview)
                self.assertNotIn('.mini-window{', preview)

    def test_picker_and_ppt_stay_static_without_leaking_overrides_to_other_html(self):
        picker = render("<SECTION ID = 'additional-designs'><button>선택</button></SECTION><OUTPUT ID='choice-result'>결과</OUTPUT>")
        self.assertIn('.mini-window{', picker)
        self.assertNotIn('nav button{display:none}', picker)
        ppt = render("<BODY DATA-PPT-DRAFT='1'><MAIN CLASS='extra ppt-main'><nav><button>슬라이드</button></nav></MAIN></BODY>")
        self.assertIn('ppt-main', ppt)
        self.assertIn('슬라이드', ppt)
        self.assertNotIn('nav button{display:none}', ppt)

    def test_resource_css_preserves_unrelated_layout_and_inline_svg_fragments(self):
        css = '''@import "https://outside.invalid/theme.css";
        h1{color:navy}body{display:grid;background:url(https://outside.invalid/bg.png);gap:12px}
        @media(min-width:600px){main{padding:20px}}.chart{fill:url(#gradient);clip-path:url('#clip')}
        .url-label::after{content:"url(example) ; @import text"}'''
        cleaned = static_css(css)
        for value in ('color:navy', 'display:grid', 'background:none', 'gap:12px', '@media(min-width:600px)',
                      'padding:20px', 'fill:url(#gradient)', 'clip-path:url(#clip)', 'content:"url(example) ; @import text"'):
            self.assertIn(value, cleaned)
        self.assertNotIn('outside.invalid', cleaned)
        preview = render('<style>' + css + '</style><p style="color:red;background:url(local.png);margin:4px">내용</p>')
        self.assertIn('color:red;background:none;margin:4px', preview)
        self.assertIn('color:navy', preview)
        self.assertNotIn('local.png', preview)

    def test_escaped_css_resource_tokens_and_nested_functions_are_removed(self):
        css = r'''@\69mport url("https://outside.invalid/a.css") screen;
        .one{background:u\72l('https://outside.invalid/(image).png');color:green}
        .two{background:image-set("https://outside.invalid/2.png" 1x,url('/api/private') 2x);padding:8px}
        .three{background:-webkit-image-set(url(file:///C:/private.png) 1x);display:grid}
        .four{background:src('https://outside.invalid/future');opacity:.9}'''
        cleaned = static_css(css)
        for value in ('color:green', 'padding:8px', 'display:grid', 'opacity:.9'):
            self.assertIn(value, cleaned)
        for value in ('outside.invalid', '/api/', 'file:', 'private.png'):
            self.assertNotIn(value, cleaned)

    def test_css_comments_quotes_and_incomplete_resources_do_not_erase_earlier_rules(self):
        css = 'h1{color:red}/* url(https://outside.invalid) */p{content:"a; \\"b";margin:2px}div{background:url("unfinished'
        cleaned = static_css(css)
        self.assertIn('h1{color:red}', cleaned)
        self.assertIn('margin:2px', cleaned)
        self.assertNotIn('outside.invalid', cleaned)
        self.assertNotIn('unfinished', cleaned)
        # CSS escape scanning remains bounded and does not copy a shrinking tail.
        many = r'.\61' * 10000 + '{color:blue}'
        self.assertEqual(many, static_css(many))

    def test_only_raster_data_images_and_fragment_resources_are_retained(self):
        pixel = 'data:image/png;base64,iVBORw0KGgo='
        text = ('<img src="' + pixel + '" alt="그림"><img src="relative.png">'
                '<img src="data:image/svg+xml;base64,PHN2Zz4=">'
                '<style>.good{background:url("' + pixel + '")}.bad{background:url(data:text/html;base64,PHNjcmlwdD4=)}</style>')
        preview = render(text)
        self.assertEqual(2, preview.count(pixel))
        self.assertIn('alt="그림"', preview)
        for value in ('relative.png', 'image/svg', 'text/html;base64'):
            self.assertNotIn(value, preview)

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
        for value in ('<script', '<iframe', '<object', 'action=', 'onload=', 'onerror=', 'href=', '<animate', 'http-equiv="refresh"', '/api/'):
            self.assertNotIn(value, preview)
        self.assertIn('data-view="scroll"', preview)
        self.assertIn('안전한 본문', preview)
        self.assertIn("default-src &#x27;none&#x27;", preview)
        self.assertNotIn('evil.invalid', preview)
        self.assertIn('<form><button disabled="">send</button></form>', preview)
        self.assertLess(preview.index('Content-Security-Policy'), preview.index('<html>'))

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
