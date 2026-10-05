"""Attention uses synthetic windows only; no live window is flashed or focused."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from local_app.attention import AttentionNotifier, WindowBinding, WindowsAttention, clean_summary, snapshot


def task(sid='a', **extra):
    return {'id': sid, 'title': '업무 ' + sid, 'state': 'approval', 'requests': {}, **extra}


class AttentionSnapshotTests(unittest.TestCase):
    def test_all_sessions_count_only_current_actionable_requests_and_ready_choice(self):
        sessions = {
            'a': task(requests={'r': {'id': 'r', '_attentionId': 'life-a', 'tool': 'Bash', 'input': {'command': 'SECRET'}}}),
            'b': task('b', state='question', requests={'r': {'id': 'r', '_attentionId': 'life-b', 'tool': 'AskUserQuestion'}}),
            'c': task('c', state='done', choice={'id': 'choice-c', 'options': [{'label': 'SECRET OPTION'}]}),
            'd': task('d', state='running', choice={'id': 'not-ready'}),
            'e': task('e', state='approval'),  # A status label is not a request.
            'f': task('f', state='stopped', requests={'r': {'id': 'old'}}),
            'g': task('g', bridge=SimpleNamespace(closed=True), requests={'r': {'id': 'old'}}),
        }
        value = snapshot(sessions)
        self.assertEqual((3, 1, 1, 1), tuple(value[key] for key in ('total', 'approvalCount', 'questionCount', 'choiceCount')))
        self.assertEqual({'a', 'b', 'c'}, {row['sessionId'] for row in value['items']})
        self.assertTrue(all(set(row) == {'id', 'sessionId', 'title', 'kind', 'summary'} for row in value['items']))
        self.assertNotIn('SECRET', str(value))
        self.assertEqual(value, snapshot(dict(reversed(list(sessions.items())))))

    def test_request_summaries_project_question_or_description_without_other_tool_arguments(self):
        sessions = {
            'a': task(requests={'r': {'id': 'r', 'tool': 'AskUserQuestion', 'input': {
                'questions': [{'question': '보고서 형식을 골라 주세요', 'options': [{'label': 'PRIVATE OPTION'}]}]}}}),
            'b': task('b', requests={'r': {'id': 'r', 'tool': 'Bash', 'input': {
                'description': '검증 스크립트 실행', 'command': 'PRIVATE COMMAND'}}}),
            'c': task('c', requests={'r': {'id': 'r', 'tool': 'Write', 'input': {'content': 'PRIVATE BODY'}}}),
            'd': task('d', state='done', choice={'id': 'choice', 'question': '디자인을 선택해 주세요',
                                               'options': [{'label': 'PRIVATE OPTION'}]}),
        }
        rows = {row['sessionId']: row for row in snapshot(sessions)['items']}
        self.assertEqual('보고서 형식을 골라 주세요', rows['a']['summary'])
        self.assertEqual('검증 스크립트 실행', rows['b']['summary'])
        self.assertEqual('파일 저장 승인이 필요해요', rows['c']['summary'])
        self.assertEqual('디자인을 선택해 주세요', rows['d']['summary'])
        self.assertNotIn('PRIVATE', str(rows))

    def test_summary_redacts_before_truncating_and_is_stable_for_storage_and_native_transport(self):
        secret = 'glpat-' + 'x' * 160
        summary = clean_summary('  인증 token=' + secret + '\n확인\u202e' + '가' * 150)
        self.assertLessEqual(len(summary), 100)
        self.assertTrue(summary.endswith('…'))
        self.assertNotIn('glpat-', summary)
        self.assertNotRegex(summary, r'[\x00-\x1f\u202e]')
        self.assertEqual(summary, clean_summary(summary))
        self.assertEqual('기본 안내', clean_summary('python private-script.py', '기본 안내'))
        self.assertEqual('기본 안내', clean_summary('가' * 4097, '기본 안내'))

    def test_description_equal_to_command_is_never_used_as_summary(self):
        request = {'id': 'r', 'tool': 'Bash', 'description': 'git status', 'input': {'command': 'git status'}}
        value = snapshot({'a': task(requests={'r': request})})
        self.assertEqual('명령 실행 승인이 필요해요', value['items'][0]['summary'])

    def test_cli_metadata_fallback_uses_meaningful_description_or_title_not_raw_command(self):
        requests = {
            'blank': {'id': 'blank', 'tool': 'Bash', 'description': '  ',
                      'input': {'description': '결과 파일 검사', 'command': 'PRIVATE COMMAND'}},
            'title': {'id': 'title', 'tool': 'Read', 'title': '보고서 참고 자료 확인',
                      'input': {'file_path': 'PRIVATE FILE'}},
            'unsafe': {'id': 'unsafe', 'tool': 'Bash', 'title': 'git status',
                       'input': {'command': '  git status  '}},
            'question': {'id': 'question', 'tool': 'AskUserQuestion',
                         'title': '보고서 출력 형식을 골라 주세요', 'input': {'questions': []}},
        }
        rows = {row['sessionId']: row for row in snapshot({
            key: task(key, requests={'r': value}) for key, value in requests.items()})['items']}
        self.assertEqual('결과 파일 검사', rows['blank']['summary'])
        self.assertEqual('보고서 참고 자료 확인', rows['title']['summary'])
        self.assertEqual('명령 실행 승인이 필요해요', rows['unsafe']['summary'])
        self.assertEqual('보고서 출력 형식을 골라 주세요', rows['question']['summary'])
        self.assertNotIn('PRIVATE', str(rows))

    def test_same_request_id_in_new_lifetime_gets_new_identity_and_revision(self):
        request = {'id': 'cli-reused', '_attentionId': 'life-1', 'tool': 'Bash'}
        sessions = {'a': task(requests={'r': request})}
        first = snapshot(sessions)
        self.assertEqual(first, snapshot(sessions))
        request['_attentionId'] = 'life-2'
        second = snapshot(sessions)
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertNotEqual(first['revision'], second['revision'])
        sessions['b'] = task('b', requests={'r': request})
        self.assertEqual(2, snapshot(sessions)['total'])

    def test_business_choice_remains_actionable_after_connection_retirement(self):
        value = snapshot({'a': task(state='done', bridge=SimpleNamespace(closed=True),
            requests={'old': {'id': 'expired-native-request'}}, choice={'id': 'reconnect-choice'})})
        self.assertEqual((1, 0, 1), (value['total'], value['approvalCount'], value['choiceCount']))

    def test_malformed_and_repeated_rows_do_not_expose_content_or_double_count(self):
        same = {'id': 'one', 'tool': 'Read'}
        sessions = [None, {}, task(title=None, requests={'a': same, 'b': same, 'c': {'id': []}, 'd': None}),
                    task('b', state='error', requests={'e': same}),
                    task('c', state='done', choice={'id': 'x' * 257})]
        value = snapshot(sessions)
        self.assertEqual(1, value['total'])
        self.assertEqual('새 업무', value['items'][0]['title'])
        self.assertEqual(0, snapshot([])['total'])


class FakeNative:
    def __init__(self):
        self.binding = 'window-a'
        self.calls = []
        self.result = 'requested'

    def bind(self, title):
        return self.binding

    def flash(self, binding, title, *, stop=False):
        self.calls.append((binding, title, stop))
        return self.result


class AttentionNotifierTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.notifier = AttentionNotifier(native=self.native)
        self.assertEqual({'supported': True, 'bound': True}, self.notifier.bind())
        self.native.calls.clear()

    def pending(self, identity='one', **extra):
        return snapshot({'a': task(requests={'r': {'id': 'r', '_attentionId': identity, 'tool': 'Bash'}}, **extra)})

    def test_new_request_flashes_once_same_revision_or_title_change_does_not(self):
        value = self.pending()
        self.notifier.update(value)
        self.notifier.update(value)
        self.notifier.update(self.pending(title='새 제목'))
        self.assertEqual(1, len(self.native.calls))
        self.assertFalse(self.native.calls[0][2])
        self.notifier.update(self.pending('new-lifetime'))
        self.assertEqual(2, len(self.native.calls))

    def test_resolution_focus_binding_and_close_stop_only_verified_binding(self):
        self.notifier.update(self.pending())
        self.notifier.update(snapshot({}))
        self.assertTrue(self.native.calls[-1][2])
        self.notifier.bind()
        self.assertTrue(self.native.calls[-1][2])
        self.notifier.close()
        count = len(self.native.calls)
        self.notifier.close()
        self.notifier.update(self.pending('later'))
        self.notifier.bind()
        self.assertEqual(count, len(self.native.calls))
        self.assertFalse(self.notifier.native_state['bound'])

    def test_rebinding_stops_old_verified_window_without_client_handle(self):
        self.native.binding = 'window-b'
        self.notifier.bind()
        self.assertEqual([('window-a', True), ('window-b', True)], [(call[0], call[2]) for call in self.native.calls])
        self.native.binding = None  # User switched away during capture.
        self.notifier.bind()
        self.assertEqual(2, len(self.native.calls))

    def test_invalid_or_inaccessible_window_is_unbound_and_not_retried_on_every_delta(self):
        self.native.result = 'invalid'
        self.notifier.update(self.pending())
        self.assertFalse(self.notifier.native_state['bound'])
        self.notifier.update(self.pending('second'))
        self.assertEqual(1, len(self.native.calls))
        self.notifier.bind()
        self.assertFalse(self.notifier.native_state['bound'])

    def test_native_absent_is_metadata_only_and_title_is_not_auth_token(self):
        notifier = AttentionNotifier(enabled=False, native=self.native)
        self.assertTrue(notifier.window_title.startswith('Company Workspace · '))
        self.assertNotEqual(notifier.window_title, self.notifier.window_title)
        self.assertEqual({'supported': False, 'bound': False}, notifier.bind())
        self.assertEqual({'supported': False, 'bound': False}, notifier.update(self.pending()))
        notifier.close()
        self.assertEqual([], self.native.calls)


class FakeWindows:
    def __init__(self):
        self.hwnd = 0x100000002
        self.foreground = self.hwnd
        self.title = 'Company Workspace · fixed-marker'
        self.pid = 77
        self.visible = True
        self.exists = True
        self.ancestor = self.hwnd
        self.calls = []

    def GetForegroundWindow(self): return self.foreground
    def IsWindow(self, hwnd): return self.exists
    def IsWindowVisible(self, hwnd): return self.visible
    def GetAncestor(self, hwnd, flag): return self.ancestor
    def GetWindowTextLengthW(self, hwnd): return len(self.title)
    def GetWindowTextW(self, hwnd, text, length):
        text.value = self.title
        return len(self.title)
    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = self.pid
        return 10
    def FlashWindowEx(self, value):
        info = value._obj
        self.calls.append((info.hwnd, info.dwFlags, info.uCount, info.dwTimeout))
        return 0  # An inactive caption is a normal return, not failure.


class WindowsAttentionContractTests(unittest.TestCase):
    def setUp(self):
        self.native = object.__new__(WindowsAttention)
        self.native.user = FakeWindows()
        self.native.wt = wintypes
        self.native.own_sid, self.native.own_session = b'current-user', 2
        self.native.allowed_images = {'verified-edge.exe'}
        self.identity = (1234567, 2, b'current-user', 'verified-edge.exe')
        self.native._process = lambda pid: self.identity
        class FlashInfo(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('hwnd', wintypes.HWND), ('dwFlags', wintypes.DWORD),
                        ('uCount', wintypes.UINT), ('dwTimeout', wintypes.DWORD)]
        self.native.FlashInfo = FlashInfo
        self.title = self.native.user.title

    def test_binding_requires_exact_title_user_session_binary_and_top_level(self):
        expected = WindowBinding(self.native.user.hwnd, 77, *self.identity)
        self.assertEqual(expected, self.native.bind(self.title))
        self.assertIsNone(self.native.bind('Company Workspace'))
        for identity in ((1234567, 3, b'current-user', 'verified-edge.exe'),
                         (1234567, 2, b'another-user', 'verified-edge.exe'),
                         (1234567, 2, b'current-user', 'another-edge.exe'), None):
            self.identity = identity
            self.assertIsNone(self.native.bind(self.title))
        self.identity = (1234567, 2, b'current-user', 'verified-edge.exe')
        for name, value in (('visible', False), ('exists', False), ('ancestor', 100)):
            before = getattr(self.native.user, name)
            setattr(self.native.user, name, value)
            self.assertIsNone(self.native.bind(self.title))
            setattr(self.native.user, name, before)

    def test_three_taskbar_flashes_preserve_pointer_width_and_never_focus(self):
        binding = self.native.bind(self.title)
        self.native.user.foreground = 999
        self.assertEqual('requested', self.native.flash(binding, self.title))
        self.assertEqual([(0x100000002, 2, 3, 0)], self.native.user.calls)
        self.assertEqual(999, self.native.user.foreground)
        self.native.user.foreground = binding.hwnd
        self.assertEqual('foreground', self.native.flash(binding, self.title))
        self.assertEqual((binding.hwnd, 0, 0, 0), self.native.user.calls[-1])

    def test_handle_pid_creation_or_title_reuse_never_flashes_replacement(self):
        binding = self.native.bind(self.title)
        self.native.user.foreground = 999
        for field in ('pid', 'created', 'title'):
            original_pid, original_title, original_identity = self.native.user.pid, self.native.user.title, self.identity
            if field == 'pid': self.native.user.pid += 1
            if field == 'title': self.native.user.title = 'Company Workspace · unrelated'
            if field == 'created': self.identity = (9999999, *self.identity[1:])
            self.assertEqual('invalid', self.native.flash(binding, self.title))
            self.native.user.pid, self.native.user.title, self.identity = original_pid, original_title, original_identity
        self.assertEqual([], self.native.user.calls)


class WindowsAttentionReadOnlyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows process identity contract only')
    def test_current_process_identity_can_be_queried_without_window_capture(self):
        candidates = [Path(os.environ[name]) / 'Microsoft/Edge/Application/msedge.exe'
                      for name in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA') if os.environ.get(name)]
        if not any(path.is_file() for path in candidates):
            self.skipTest('No installed Edge candidate; native attention uses badge fallback')
        # Constructor reads only this process token and executable metadata.
        # Never call bind/flash or inspect another application's window here.
        native = WindowsAttention()
        identity = native._process(os.getpid())
        self.assertIsNotNone(identity)
        self.assertTrue(identity[0] > 0)
        self.assertTrue(identity[1] == native.own_session)
        self.assertTrue(identity[2] == native.own_sid)
        self.assertTrue(bool(native.allowed_images))


if __name__ == '__main__':
    unittest.main()
