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
                self.assertEqual('/manual/guide', values['href'].split('#', 1)[0])
                self.assertEqual('_blank', values['target'])
                self.assertTrue({'noopener', 'noreferrer'}.issubset(values['rel'].split()))


if __name__ == '__main__':
    unittest.main()
