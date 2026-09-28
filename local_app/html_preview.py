"""Static, isolated previews of the supported offline report/picker format.

Format recognition is not a trust boundary. Every rendered document is stripped
of active elements and served in an opaque-origin, scriptless sandbox with CSP.
Other attached HTML stays source text. No source file is modified.
"""
from html import escape
from html.parser import HTMLParser
import re

ALLOWED = set('html head body title style main header footer section article aside nav div span p h1 h2 h3 h4 h5 h6 ul ol li table thead tbody tfoot tr th td caption colgroup col figure figcaption img br hr strong em b i small label details summary textarea button select option svg desc g path rect circle ellipse line polyline polygon text tspan defs lineargradient radialgradient stop clippath'.split())
VOID = {'img', 'br', 'hr', 'col'}
DROP = {'script', 'iframe', 'object', 'embed', 'form', 'template', 'noscript', 'audio', 'video'}
CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"


def static_css(value):
    # Shipped report CSS has no resource URLs/imports/escapes. Drop an entire
    # foreign block, rather than rewriting arbitrary CSS or triggering a fetch.
    # Local SVG fragment references are inert and used for chart fills/clipping.
    if re.fullmatch(r'url\(#[A-Za-z0-9_-]+\)', value):
        return value
    return '' if re.search(r'url|@import|image-set|image\s*\(|\\', value, re.I) else value


class StaticPreview(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.dropped = []
        self.styling = False

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            if tag != 'embed':
                self.dropped.append(tag)
            return
        if self.dropped or tag not in ALLOWED:
            return
        safe = []
        for key, value in attrs:
            value = value or ''
            if key in {'class', 'id', 'style', 'title', 'role', 'lang', 'dir', 'colspan', 'rowspan', 'scope',
                       'width', 'height', 'viewbox', 'd', 'x', 'y', 'dx', 'dy', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'hidden', 'tabindex',
                       'fill', 'stroke', 'stroke-width', 'points', 'transform', 'opacity', 'offset', 'stop-color',
                       'preserveaspectratio', 'clip-path', 'text-anchor', 'font-size', 'font-weight'} or key.startswith(('aria-', 'data-')):
                if key == 'data-view':
                    value = 'scroll'  # Every slide remains visible without executing report JS.
                if key in {'style','fill','stroke','clip-path'}:
                    value = static_css(value)
                safe.append((key, value))
            elif tag == 'img' and key == 'src' and re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+', value):
                safe.append((key, value))
            elif tag == 'img' and key == 'alt':
                safe.append((key, value))
        if tag in {'button', 'select', 'textarea'}:
            safe.append(('disabled', ''))
        self.parts.append('<'+tag+''.join(' '+k+'="'+escape(v, quote=True)+'"' for k,v in safe)+'>')
        self.styling = tag == 'style'

    def handle_endtag(self, tag):
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        if tag in ALLOWED and tag not in VOID:
            self.parts.append('</'+tag+'>')
        if tag == 'style':
            self.styling = False

    def handle_data(self, data):
        if not self.dropped:
            self.parts.append(static_css(data) if self.styling else escape(data))


def render(text: str) -> str | None:
    report = 'class="report-main"' in text and re.search(r'<body\b[^>]*\bdata-style=', text)
    picker = 'id="additional-designs"' in text and 'id="choice-result"' in text
    ppt = 'data-ppt-draft="1"' in text and 'class="ppt-main"' in text
    if not (report or picker or ppt):
        return None
    parser = StaticPreview()
    parser.feed(text)
    parser.close()
    # The CSP must precede all source content. Sandbox is also applied by the UI.
    return ('<!doctype html><meta http-equiv="Content-Security-Policy" content="'+escape(CSP, quote=True)+'">'
            + ''.join(parser.parts) + '<style>body[data-view] main>.section{display:block!important}nav button{display:none}'
            '.mini-window{height:380px!important}.mini-window .style-preview{transform:scale(.4)!important;transform-origin:top left}'
            'details.design-options{display:block}details.design-options>*{content-visibility:visible}</style>')
