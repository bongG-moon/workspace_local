"""Static, isolated previews of local HTML documents.

Format recognition only enables presentation fixes for known offline reports.
Every document is stripped of active elements and served in an opaque-origin,
scriptless sandbox with CSP. CSS filtering improves presentation; CSP is the
resource/network boundary. No source file is modified.
"""
from html import escape
from html.parser import HTMLParser
import re

ALLOWED = set('html head body title style main header footer section article aside nav address div span p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd table thead tbody tfoot tr th td caption colgroup col figure figcaption img br hr strong em b i small s del ins mark sub sup abbr cite q blockquote pre code kbd samp time a label details summary form fieldset legend input output progress meter textarea button select option optgroup svg desc g path rect circle ellipse line polyline polygon text tspan defs lineargradient radialgradient stop clippath'.split())
VOID = {'img', 'br', 'hr', 'col', 'input'}
DROP = {'script', 'iframe', 'object', 'embed', 'template', 'noscript', 'audio', 'video'}
CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
RASTER_DATA = re.compile(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+', re.I)
CSS_ESCAPE = re.compile(r'[0-9a-fA-F]{1,6}(?:\r\n|[ \t\r\n\f])?')


def _css_escape(value, index):
    """Return one decoded CSS escape and the next source offset."""
    index += 1
    match = CSS_ESCAPE.match(value, index)
    if match:
        digits = match.group().strip()
        codepoint = int(digits, 16)
        return (chr(codepoint) if 0 < codepoint <= 0x10ffff else '\ufffd', match.end())
    if index < len(value):
        return value[index], index + 1
    return '', index


def _css_identifier(value, index):
    decoded = []
    while index < len(value):
        char = value[index]
        if char == '\\':
            char, index = _css_escape(value, index)
            decoded.append(char)
        elif char.isalnum() or char in '_-':
            decoded.append(char)
            index += 1
        else:
            break
    return ''.join(decoded).lower(), index


def _css_end(value, index, *, function=False):
    """Skip a resource function or at-rule, honoring strings and nesting."""
    depth, quote = (1 if function else 0), None
    while index < len(value):
        char = value[index]
        if char == '\\':
            _, index = _css_escape(value, index)
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif value.startswith('/*', index):
            end = value.find('*/', index + 2)
            index = len(value) if end < 0 else end + 2
            continue
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if function and depth == 0:
                return index + 1
        elif char == ';' and not depth and not function:
            return index + 1
        index += 1
    return len(value)


def _safe_css_url(value):
    target = value.strip()
    if len(target) >= 2 and target[0] == target[-1] and target[0] in "\"'":
        target = target[1:-1]
    # No decoding of arbitrary resource references, filesystem lookup, or fetch.
    if re.fullmatch(r'#[A-Za-z0-9_-]+', target):
        return 'url(' + target + ')'
    if RASTER_DATA.fullmatch(target):
        return 'url("' + target + '")'
    return 'none'


def static_css(value):
    """Remove resource expressions without discarding unrelated layout rules.

    This is deliberately not a complete CSS validator. The independent CSP and
    opaque-origin sandbox also block escaped/future syntax and external loads.
    """
    parts, index = [], 0
    while index < len(value):
        char = value[index]
        if value.startswith('/*', index):
            end = value.find('*/', index + 2)
            parts.append(' ')
            index = len(value) if end < 0 else end + 2
        elif char in "\"'":
            start, quote = index, char
            index += 1
            while index < len(value):
                if value[index] == '\\':
                    _, index = _css_escape(value, index)
                elif value[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            parts.append(value[start:index])
        elif char == '@':
            identifier, end = _css_identifier(value, index + 1)
            if identifier in {'import', 'namespace'}:
                index = _css_end(value, end)
            else:
                parts.append(value[index:end])
                index = end
        elif char.isalpha() or char in '_-\\':
            identifier, end = _css_identifier(value, index)
            opening = end
            while opening < len(value) and value[opening].isspace():
                opening += 1
            if identifier in {'url', 'image-set', '-webkit-image-set', 'image', 'src', 'expression'} and value[opening:opening + 1] == '(':
                closing = _css_end(value, opening + 1, function=True)
                complete = closing > opening and value[closing - 1:closing] == ')'
                parts.append(_safe_css_url(value[opening + 1:closing - 1]) if identifier == 'url' and complete else 'none')
                index = closing
            else:
                parts.append(value[index:end])
                index = end
        else:
            parts.append(char)
            index += 1
    return ''.join(parts)


def _starttag(tag, attrs):
    return '<' + tag + ''.join(' ' + key + '="' + escape(value, quote=True) + '"' for key, value in attrs) + '>'


class StaticPreview(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.dropped = []
        self.styling = False
        self.report_body = self.report_main = False
        self.picker_ids = set()
        self.report_views = []
        self.layout_nodes = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            if tag != 'embed':
                self.dropped.append(tag)
            return
        if self.dropped or tag not in ALLOWED:
            return
        attributes = {}
        for key, value in attrs:
            attributes.setdefault(key, value)  # Like HTML, the first duplicate wins.
        classes = set((attributes.get('class') or '').split())
        if len(self.layout_nodes) < 256 and tag in {'body', 'main', 'article', 'section', 'div', 'table', 'svg', 'img'}:
            self.layout_nodes.append((tag, attributes, classes))
        if tag == 'body':
            self.report_body |= 'data-style' in attributes
        if tag == 'main':
            self.report_main |= 'report-main' in classes
        if attributes.get('id') in {'additional-designs', 'choice-result'}:
            self.picker_ids.add(attributes['id'])
        safe = []
        for key, value in attributes.items():
            value = value or ''
            if key in {'class', 'id', 'style', 'title', 'role', 'lang', 'dir', 'colspan', 'rowspan', 'scope',
                       'width', 'height', 'viewbox', 'd', 'x', 'y', 'dx', 'dy', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'hidden', 'tabindex',
                       'fill', 'stroke', 'stroke-width', 'points', 'transform', 'opacity', 'offset', 'stop-color',
                       'preserveaspectratio', 'clip-path', 'text-anchor', 'font-size', 'font-weight',
                       'type', 'value', 'placeholder', 'checked', 'selected', 'open', 'start', 'reversed',
                       'min', 'max', 'step', 'low', 'high', 'optimum', 'datetime'} or key.startswith(('aria-', 'data-')):
                if key in {'style','fill','stroke','clip-path'}:
                    value = static_css(value)
                safe.append((key, value))
            elif tag == 'img' and key == 'src' and RASTER_DATA.fullmatch(value):
                safe.append((key, value))
            elif tag == 'img' and key == 'alt':
                safe.append((key, value))
        if tag in {'button', 'select', 'textarea', 'input', 'fieldset'}:
            safe.append(('disabled', ''))
        if tag == 'body' and 'data-view' in attributes:
            self.report_views.append((len(self.parts), tag, safe))
        self.parts.append(_starttag(tag, safe))
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
            value = static_css(data) if self.styling else escape(data)
            if self.styling:
                self.styles.append(value)
            self.parts.append(value)



def _layout_rules(css, conditional=False, level=0):
    """Yield bounded ordinary rules; ignore print and animation declarations."""
    if level > 5:
        return
    index = 0
    while index < len(css):
        opening = css.find('{', index)
        if opening < 0:
            return
        selector = css[index:opening].strip()
        cursor, depth, quote = opening + 1, 1, None
        while cursor < len(css) and depth:
            char = css[cursor]
            if char == '\\':
                cursor += 2
                continue
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
            cursor += 1
        body = css[opening + 1:cursor - 1]
        if selector.startswith('@'):
            if re.match(r'@(media|supports|layer|container)\b', selector, re.I) and not (
                    re.match(r'@media\b', selector, re.I) and re.search(r'\bprint\b', selector, re.I)
                    and not re.search(r'\bscreen\b|\ball\b', selector, re.I)):
                yield from _layout_rules(body, conditional or not selector.startswith('@layer'), level + 1)
        else:
            yield selector, body, conditional
        index = cursor


def _layout_declarations(css):
    # Quoted CSS content is not a layout declaration. Strip it before splitting.
    cleaned, index = [], 0
    while index < len(css):
        char = css[index]
        if char in "\"'":
            quote = char
            index += 1
            while index < len(css):
                if css[index] == '\\':
                    index += 2
                elif css[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
        else:
            cleaned.append(char)
            index += 1
    result = {}
    for declaration in ''.join(cleaned).split(';'):
        name, separator, value = declaration.partition(':')
        if separator and name.strip().lower() in {'width', 'max-width', 'box-sizing', 'padding', 'padding-left', 'padding-right', 'margin', 'margin-left', 'margin-right', 'border', 'border-width', 'border-left-width', 'border-right-width'}:
            result[name.strip().lower()] = re.sub(r'\s*!important\s*$', '', value.strip(), flags=re.I)
    return result



def _horizontal_space(values, name):
    def pixels(value):
        match = re.fullmatch(r'(\d+(?:\.\d+)?)(?:px)?', value or '', re.I)
        return min(4096, float(match[1])) if match else 0
    tokens = values.get(name, '').split()
    if name == 'border-width' and not tokens:
        tokens = values.get('border', '').split()[:1]
    right = tokens[1] if len(tokens) > 1 else (tokens[0] if tokens else '')
    left = tokens[3] if len(tokens) > 3 else right
    prefix = 'border' if name == 'border-width' else name
    suffix = '-width' if name == 'border-width' else ''
    return pixels(values.get(prefix + '-left' + suffix, left)) + pixels(values.get(prefix + '-right' + suffix, right))


def _fixed_canvas_width(parser):
    """Conservative numeric display hint, never a CSS/HTML fragment.

    Only literal widths on present elements qualify. Conditional/responsive
    layouts keep their normal viewport. Work is bounded even for large reports.
    """
    rules = []
    for index, (selectors, body, conditional) in enumerate(_layout_rules(''.join(parser.styles))):
        if index >= 512:
            break
        values = _layout_declarations(body)
        if not values:
            continue
        for selector in selectors.split(',')[:32]:
            # Complex ancestry and pseudo states cannot be resolved statically.
            match = re.fullmatch(r'([a-z][\w-]*|\*)?((?:[.#][\w-]+)*)', selector.strip(), re.I)
            if match:
                rules.append((match[1], re.findall(r'([.#])([\w-]+)', match[2]), values, conditional))
        if len(rules) >= 256:
            break
    maximum, seen = 0, set()
    for tag, attrs, classes in parser.layout_nodes:
        signature = (tag, attrs.get('id'), tuple(sorted(classes)), attrs.get('style'), attrs.get('width'))
        if signature in seen:
            continue
        seen.add(signature)
        values, responsive = {}, False
        for rule_tag, tokens, declarations, conditional in rules:
            if rule_tag and rule_tag.lower() not in {tag, '*'}:
                continue
            if all(name in classes if prefix == '.' else attrs.get('id') == name for prefix, name in tokens):
                if conditional:
                    responsive |= bool({'width', 'max-width'} & declarations.keys())
                else:
                    values.update(declarations)
        values.update(_layout_declarations(static_css(attrs.get('style') or '')))
        width = values.get('width', str(attrs.get('width') or ''))
        if width.isdigit():
            width += 'px'
        fixed = re.fullmatch(r'(\d+(?:\.\d+)?)px', width, re.I)
        max_width = values.get('max-width', '')
        responsive |= bool(re.search(r'%|vw|vmin|clamp\(|min\(', max_width, re.I))
        if fixed and not responsive:
            size = float(fixed[1])
            maximum_fixed = re.fullmatch(r'(\d+(?:\.\d+)?)px', max_width, re.I)
            if maximum_fixed:
                size = min(size, float(maximum_fixed[1]))
            if 640 <= size <= 16384:
                if values.get('box-sizing') != 'border-box':
                    size += _horizontal_space(values, 'padding') + _horizontal_space(values, 'border-width')
                size += _horizontal_space(values, 'margin')
                if size <= 16384:
                    maximum = max(maximum, size)
    # Reserve the browser's ordinary body margin and vertical scroll bar.
    return int(maximum + 32) if maximum else 0


def render(text: str) -> str:
    parser = StaticPreview()
    parser.feed(text)
    parser.close()
    fixes = []
    if parser.report_body and parser.report_main:
        for index, tag, attrs in parser.report_views:
            parser.parts[index] = _starttag(tag, [(key, 'scroll' if key == 'data-view' else value) for key, value in attrs])
        fixes.append('body[data-view] main>.section{display:block!important}nav button{display:none}')
    if parser.picker_ids == {'additional-designs', 'choice-result'}:
        fixes.append('.mini-window{height:380px!important}.mini-window .style-preview{transform:scale(.4)!important;transform-origin:top left}'
                     'details.design-options{display:block}details.design-options>*{content-visibility:visible}')
    width = _fixed_canvas_width(parser)
    fit = f'<meta name="ax-preview-width" content="{width}">' if width else ''
    # The CSP must precede all source content. Sandbox is also applied by the UI.
    return ('<!doctype html><meta http-equiv="Content-Security-Policy" content="'+escape(CSP, quote=True)+'">'
            + fit + ''.join(parser.parts) + ('<style>' + ''.join(fixes) + '</style>' if fixes else ''))
