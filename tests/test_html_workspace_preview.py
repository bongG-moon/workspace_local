import os
import sys
from html import escape
from html.parser import HTMLParser
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

    def test_fixed_slide_has_bounded_numeric_fit_hint_without_executable_content(self):
        preview = render('<style>*{box-sizing:border-box}.slide{width:1920px;height:1080px;padding:56px 76px}</style>'
                         '<section class="slide"><h1>카드 보고서</h1></section>')
        self.assertIn('<meta name="ax-preview-width" content="1952">', preview)
        self.assertIn('width:1920px', preview)
        self.assertIn("script-src &#x27;none&#x27;", preview)
        self.assertNotIn('<script', preview)
        self.assertLess(preview.index('Content-Security-Policy'), preview.index('ax-preview-width'))

    def test_fixed_content_box_padding_and_borders_fit_without_overcounting_border_box(self):
        source = '<style>.slide{width:1920px;padding:40px 76px;border:2px solid gray}</style><div class="slide">보고서</div>'
        self.assertIn('content="2108"', render(source))
        self.assertIn('content="1952"', render('<style>*{box-sizing:border-box}</style>'+source))

    def test_responsive_and_conditional_layouts_do_not_get_a_fixed_viewport(self):
        for css in ('.slide{max-width:1920px;width:100%}', '.slide{width:1920px;max-width:100%}',
                    '.slide{width:1920px}@media(max-width:900px){.slide{width:100%}}',
                    '@media(min-width:1920px){.slide{width:1920px}}',
                    '@media print{.slide{width:1920px}}',
                    '.unused{width:9000px}', '.slide::after{content:"width:9999px"}'):
            with self.subTest(css=css):
                self.assertNotIn('ax-preview-width', render('<style>'+css+'</style><main class="slide">보고서</main>'))

    def test_fit_hint_ignores_source_metadata_code_strings_and_extreme_widths(self):
        for content in ('<meta name="ax-preview-width" content="99999"><p>본문</p>',
                        '<pre>.slide{width:1920px}</pre><div class="slide">본문</div>',
                        '<style>.slide{content:";width:1920px"}</style><div class="slide">본문</div>',
                        '<div style="width:999999999px">본문</div>',
                        '<div style="width:300px">본문</div>'):
            with self.subTest(content=content):
                self.assertNotIn('ax-preview-width', render(content))
        self.assertIn('content="1312"', render('<main style="width:1280px">본문</main>'))
        self.assertIn('content="992"', render('<style>.slide{width:1920px;max-width:960px}</style><div class="slide">본문</div>'))

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


class HtmlDeckPreviewTests(unittest.TestCase):
    """Synthetic offline slides; no supplied business document is embedded."""

    @staticmethod
    def deck(style='--deck-w:960px;--deck-h:540px'):
        return ('<!doctype html><html><head><style>'
                '.ppt-page{display:none;position:absolute}.ppt-page.active{display:block}'
                '#deck-stage{transform:scale(.1)}.ppt-slide{width:960px;height:540px}'
                '</style></head><body data-ppt-deck="1" class="extra deck-shell" style="' + escape(style, quote=True) + '">'
                '<nav id="deck-toolbar"><button>가상 메뉴</button></nav>'
                '<main id="deck-workspace"><div id="deck-stage"><div id="deck-holder"><div id="deck-slides">'
                '<section id="synthetic-page-one" class="ppt-page active" aria-hidden="false">'
                '<article id="synthetic-slide-one" class="ppt-slide" style="--pw:960;--ph:540"><h1>가상 첫 장</h1></article></section>'
                '<section id="synthetic-page-two" class="ppt-page" hidden aria-hidden="true">'
                '<article id="synthetic-slide-two" class="ppt-slide" hidden aria-hidden="true"><h1>가상 둘째 장</h1></article></section>'
                '</div></div></div></main>'
                '<aside id="unrelated-hidden" hidden aria-hidden="true">별도 숨긴 내용</aside>'
                '<script>document.querySelector("#deck-stage").style.transform="scale(1)"</script>'
                '</body></html>')

    @staticmethod
    def attributes(markup):
        class ParsedAttributes(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_id = {}
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if values.get('id'):
                    self.by_id[values['id']] = values
        parser = ParsedAttributes()
        parser.feed(markup)
        return parser.by_id

    def test_known_deck_shows_all_saved_pages_without_running_scripts(self):
        preview = render(self.deck())
        self.assertIn('/* ax-static-ppt-deck */', preview)
        self.assertIn('container-type:inline-size', preview)
        self.assertIn('max-width:960px!important', preview)
        self.assertIn('aspect-ratio:960/540!important', preview)
        self.assertIn('position:static!important;display:block!important', preview)
        for title in ('가상 첫 장', '가상 둘째 장'):
            self.assertIn(title, preview)
        nodes = self.attributes(preview)
        for name in ('synthetic-page-one', 'synthetic-page-two', 'synthetic-slide-one', 'synthetic-slide-two'):
            self.assertNotIn('hidden', nodes[name])
            self.assertNotIn('aria-hidden', nodes[name])
        self.assertEqual('ppt-page active', nodes['synthetic-page-one']['class'])
        self.assertIn('hidden', nodes['unrelated-hidden'])
        self.assertEqual('true', nodes['unrelated-hidden']['aria-hidden'])
        self.assertNotIn('<script', preview)
        self.assertNotIn('document.querySelector', preview)

    def test_every_format_marker_is_required_before_revealing_hidden_content(self):
        source = self.deck()
        changes = [
            ('data-ppt-deck="1"', 'data-ppt-deck="2"'),
            ('data-ppt-deck="1"', ''),
            ('extra deck-shell', 'extra deck-shell-other'),
            ('class="ppt-page', 'class="ordinary-page'),
            ('class="ppt-slide"', 'class="ordinary-slide"'),
        ] + [(f'id="{name}"', f'id="other-{name}"') for name in (
            'deck-workspace', 'deck-stage', 'deck-holder', 'deck-slides')]
        for old, new in changes:
            with self.subTest(missing=old):
                preview = render(source.replace(old, new))
                self.assertNotIn('/* ax-static-ppt-deck */', preview)
                self.assertIn('hidden', self.attributes(preview)['synthetic-page-two'])

    def test_duplicate_attributes_cannot_replace_the_first_format_marker(self):
        for old, new in (
            ('data-ppt-deck="1"', 'data-ppt-deck="0" data-ppt-deck="1"'),
            ('class="extra deck-shell"', 'class="ordinary" class="deck-shell"'),
            ('id="deck-stage"', 'id="ordinary-stage" id="deck-stage"'),
        ):
            with self.subTest(first=old):
                self.assertNotIn('/* ax-static-ppt-deck */', render(self.deck().replace(old, new)))

    def test_markers_inside_comments_scripts_templates_and_quoted_source_do_not_activate(self):
        fake = self.deck()
        for extra in ('<!--' + fake + '-->', '<script>' + fake + '</script>',
                      '<template>' + fake + '</template>', '<pre>' + escape(fake) + '</pre>'):
            with self.subTest(container=extra[:12]):
                preview = render('<body><p>일반 문서</p>' + extra + '</body>')
                self.assertIn('일반 문서', preview)
                self.assertNotIn('/* ax-static-ppt-deck */', preview)

    def test_only_bounded_literal_dimensions_enter_generated_rules(self):
        for style, maximum, ratio in (
            ('--deck-w:100px;--deck-h:10000px', '100px', '100/10000'),
            ('--deck-w:10000px;--deck-h:100px', '10000px', '10000/100'),
            ('--DECK-W:960.5PX!important; --deck-h:540.25px !important', '960.5px', '960.5/540.25'),
        ):
            with self.subTest(style=style):
                preview = render(self.deck(style))
                fix = preview.split('/* ax-static-ppt-deck */')[1]
                self.assertIn('max-width:' + maximum + '!important', fix)
                self.assertIn('aspect-ratio:' + ratio + '!important', fix)

    def test_invalid_or_executable_dimension_values_use_safe_numeric_fallbacks(self):
        invalid = ('0px', '99px', '10001px', '-960px', '1e3px', 'NaNpx',
                   'Infinitypx', '960%', 'calc(960px)', 'var(--unknown)',
                   '960px){}body{background:red}', 'url(https://outside.invalid/size)')
        for value in invalid:
            with self.subTest(value=value):
                preview = render(self.deck('--deck-w:' + value + ';--deck-h:540px'))
                fix = preview.split('/* ax-static-ppt-deck */')[1]
                self.assertIn('max-width:1160px!important', fix)
                self.assertIn('aspect-ratio:var(--pw,16)/var(--ph,9)!important', fix)
                for injected in ('outside.invalid', 'background:red', 'calc(', 'var(--unknown)'):
                    self.assertNotIn(injected, fix)

    def test_absent_or_partial_dimensions_preserve_slide_ratio_fallback(self):
        for style, width in (('', '1160px'), ('--deck-w:800px', '800px'), ('--deck-h:600px', '1160px')):
            with self.subTest(style=style):
                preview = render(self.deck(style))
                self.assertIn('max-width:' + width + '!important', preview)
                self.assertIn('aspect-ratio:var(--pw,16)/var(--ph,9)!important', preview)
                self.assertEqual('--pw:960;--ph:540', self.attributes(preview)['synthetic-slide-one']['style'])

    def test_deck_uses_responsive_container_instead_of_fixed_canvas_scaling(self):
        preview = render(self.deck())
        self.assertNotIn('ax-preview-width', preview)
        ordinary = render(self.deck().replace('data-ppt-deck="1"', 'data-ppt-deck="0"'))
        self.assertNotIn('/* ax-static-ppt-deck */', ordinary)
        self.assertIn('ax-preview-width', ordinary)
        self.assertIn('hidden', self.attributes(ordinary)['synthetic-page-two'])

    def test_deck_fix_keeps_csp_and_blocks_active_elements_events_and_resources(self):
        source = self.deck().replace('<body ', '<body onload="window.unsafe=1" ')
        hostile = ('<meta http-equiv="refresh" content="0;url=https://outside.invalid">'
                   '<style>@import "https://outside.invalid/theme.css";p{background:url(file:///C:/private.png)}</style>'
                   '<iframe src="/api/bootstrap"></iframe><object data="/api/bootstrap"></object>'
                   '<img src="https://outside.invalid/leak" onerror="alert(1)">'
                   '<a href="javascript:alert(1)">참고</a><button onclick="fetch(\'/api/quit\')">실행</button>')
        preview = render(source.replace('</body>', hostile + '</body>'))
        self.assertIn('/* ax-static-ppt-deck */', preview)
        self.assertIn("default-src &#x27;none&#x27;", preview)
        self.assertIn("script-src &#x27;none&#x27;", preview)
        self.assertLess(preview.index('Content-Security-Policy'), preview.index('<html>'))
        for value in ('<script', '<iframe', '<object', 'onload=', 'onerror=', 'onclick=',
                      'href=', 'outside.invalid', 'file:', 'private.png', '/api/', 'http-equiv="refresh"'):
            self.assertNotIn(value, preview)
        self.assertIn('<button disabled="">실행</button>', preview)


if __name__ == '__main__':
    unittest.main()
