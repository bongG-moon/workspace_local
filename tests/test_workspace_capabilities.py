"""Capability discovery must remain observational and never start model work."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import ClaudeSession
from local_app.capabilities import MAX_ITEMS, catalog
from local_app.server import LocalApp, Server, workspace_folder


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.skill_inventory.return_value = {'skills': [], 'skillsLimited': False}
        self.item = {'id': 'test-task', 'workspace': 'fixture-folder', 'trusted': True,
                     'connection': {'connected': True, 'model': 'fixture-model',
                                    'reported': {'tools': True, 'skills': True, 'mcp': True},
                                    'tools': ['Read', 'mcp__research__search'],
                                    'skills': ['company-agent:report'],
                                    'mcp': [{'name': 'research', 'status': 'connected'}]}}

    def result(self, item=None, **kwargs):
        return catalog(self.item if item is None else item, client=self.client, **kwargs)

    def test_no_selection_does_not_guess_a_workspace_or_run_discovery(self):
        result = catalog(None, client=self.client)
        self.assertEqual('no-session', result['status'])
        self.assertIsNone(result['workspace'])
        self.assertEqual('not-selected', result['installed']['state'])
        self.client.skill_inventory.assert_not_called()

    def test_untrusted_or_demo_never_read_installed_metadata(self):
        validate = Mock(side_effect=AssertionError('must not resolve this root'))
        for demo, trusted, state in [(False, False, 'needs-trust'), (True, True, 'demo')]:
            with self.subTest(demo=demo):
                result = self.result({**self.item, 'trusted': trusted}, demo=demo, validate_workspace=validate)
                self.assertEqual(state, result['installed']['state'])
        validate.assert_not_called()
        self.client.skill_inventory.assert_not_called()

    def test_live_runtime_tools_mcp_status_and_skills_are_separate(self):
        result = self.result()
        self.assertEqual('live', result['status'])
        groups = result['runtime']['groups']
        self.assertEqual(['builtin', 'mcp'], [row['kind'] for row in groups['tools']['items']])
        self.assertEqual('research', groups['tools']['items'][1]['server'])
        self.assertEqual('connected', groups['mcp']['items'][0]['status'])
        self.assertEqual('/company-agent:report', groups['skills']['items'][0]['invocation'])
        self.assertFalse(groups['commands']['reported'])

    def test_closed_connection_is_last_seen_and_first_request_is_unavailable(self):
        result = self.result({**self.item, 'connection': {**self.item['connection'], 'connected': False}})
        self.assertEqual('last-seen', result['status'])
        self.assertEqual(2, len(result['runtime']['groups']['tools']['items']))
        result = self.result({**self.item, 'connection': None})
        self.assertEqual('awaiting-runtime', result['status'])
        self.assertEqual('discovered', result['installed']['state'])

    def test_reported_empty_differs_from_omitted_and_legacy_default(self):
        self.item['connection'] = {'connected': True, 'skills': [], 'tools': [],
                                   'reported': {'skills': True, 'tools': False}}
        groups = self.result()['runtime']['groups']
        self.assertTrue(groups['skills']['reported'])
        self.assertFalse(groups['tools']['reported'])
        self.item['connection'].pop('reported')
        self.assertFalse(self.result()['runtime']['groups']['skills']['reported'])
        self.item['connection']['tools'] = ['Read']
        self.assertTrue(self.result()['runtime']['groups']['tools']['reported'])

    def test_inventory_failure_or_changed_workspace_retains_runtime_without_error_details(self):
        self.client.skill_inventory.side_effect = ValueError('SECRET-SENTINEL from settings or stderr')
        result = self.result()
        self.assertEqual('unavailable', result['installed']['state'])
        self.assertEqual(2, len(result['runtime']['groups']['tools']['items']))
        self.assertNotIn('SECRET-SENTINEL', json.dumps(result))
        self.client.reset_mock()
        result = self.result(validate_workspace=Mock(side_effect=ValueError('root moved')))
        self.client.skill_inventory.assert_not_called()
        self.assertEqual('live', result['status'])

    def test_installed_metadata_is_bounded_namespaced_and_explicitly_discovered(self):
        self.client.skill_inventory.return_value = {
            'skills': [{'name': 'report', 'invocation': '/company-agent:report', 'source': 'company', 'userInvocable': True,
                        'description': 'First\n description', 'body': 'SECRET-SENTINEL', 'path': 'SECRET-SENTINEL'},
                       {'name': 'report', 'invocation': '/other:report', 'source': 'plugin', 'explicitOnly': True, 'userInvocable': True}],
            'skillsLimited': True, 'skillWarnings': ['SECRET-SENTINEL private path'], 'skillConflicts': 1,
            'policy': {'secret': 'SECRET-SENTINEL'}, 'context': 'SECRET-SENTINEL'}
        result = self.result()
        self.assertEqual('discovered', result['installed']['state'])
        self.assertEqual(2, len(result['installed']['skills']))
        self.assertEqual('company', result['installed']['skills'][0]['scope'])
        self.assertEqual('First description', result['installed']['skills'][0]['description'])
        self.assertTrue(result['installed']['skills'][1]['explicitOnly'])
        self.assertTrue(result['installed']['limited'])
        self.assertEqual(2, len(result['warnings']))
        self.assertNotIn('SECRET-SENTINEL', json.dumps(result))

    def test_only_named_safe_runtime_fields_are_returned(self):
        self.item['connection'].update(
            tools=[{'name': 'Read', 'description': 'x' * 3000, 'input_schema': {'secret': 'SECRET-SENTINEL'}},
                   {'name': {'invalid': 'SECRET-SENTINEL'}}, None],
            mcp=[{'name': 'research', 'status': 'connected', 'url': 'SECRET-SENTINEL',
                  'headers': {'Authorization': 'SECRET-SENTINEL'}, 'env': {'SECRET': 'SECRET-SENTINEL'}}])
        result = self.result()
        self.assertEqual(800, len(result['runtime']['groups']['tools']['items'][0]['description']))
        self.assertEqual(1, len(result['runtime']['groups']['tools']['items']))
        self.assertNotIn('SECRET-SENTINEL', json.dumps(result))

    def test_library_without_invocation_is_not_made_into_slash_command(self):
        self.client.skill_inventory.return_value = {'skills': [{'name': 'library', 'source': 'personal', 'invocation': '', 'userInvocable': True}],
                                        'skillsLimited': True}
        result = self.result()
        self.assertEqual('', result['installed']['skills'][0]['invocation'])
        self.assertEqual('reference', result['installed']['skills'][0]['kind'])
        self.assertTrue(result['installed']['limited'])

    def test_hidden_helpers_and_reference_material_are_counted_separately(self):
        self.client.skill_inventory.return_value = {'skills': [
            {'name': 'helper', 'invocation': 'plugin:helper', 'source': 'plugin',
             'userInvocable': False, 'pluginNamespace': 'plugin', 'storageScope': 'personal'},
            {'name': 'manual', 'invocation': 'company:manual', 'source': 'company',
             'userInvocable': True, 'explicitOnly': True, 'storageScope': 'project'},
            {'name': 'library', 'invocation': '', 'source': 'corporate', 'userInvocable': True}]}
        installed = self.result()['installed']
        self.assertEqual(['internal', 'skill', 'reference'], [x['kind'] for x in installed['skills']])
        self.assertEqual(['personal', 'project', 'company'], [x['scope'] for x in installed['skills']])
        self.assertEqual('plugin:helper', installed['skills'][0]['invocation'])
        self.assertEqual('plugin', installed['skills'][0]['pluginNamespace'])
        self.assertEqual({'skills': 1, 'internal': 1, 'reference': 1, 'total': 3}, installed['counts'])
        self.client.call.assert_not_called()

    def test_unknown_visibility_is_not_silently_shown_as_a_user_skill(self):
        self.client.skill_inventory.return_value = {'skills': [
            {'name': 'unknown', 'invocation': 'plugin:unknown'},
            {'name': 'invalid', 'invocation': 'plugin:invalid', 'userInvocable': 'false'}], 'readFailures': 2}
        result = self.result()
        self.assertEqual([], result['installed']['skills'])
        self.assertEqual(4, result['installed']['readFailures'])
        self.assertTrue(result['installed']['limited'])
        self.assertTrue(result['warnings'])

    def test_runtime_limit_is_disclosed(self):
        self.item['connection']['tools'] = ['Tool' + str(i) for i in range(MAX_ITEMS + 10)]
        result = self.result()
        self.assertTrue(result['runtime']['groups']['tools']['limited'])
        self.assertEqual(MAX_ITEMS, len(result['runtime']['groups']['tools']['items']))
        self.assertTrue(result['warnings'])


class CatalogBridgeTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.bridge = ClaudeSession(['must-not-start'], {}, Path('.'), lambda kind, data: self.events.append((kind, data)))

    def initialize(self, commands=None):
        response = {'commands': commands} if commands is not None else {}
        self.bridge.handle({'type': 'control_response', 'response': {'request_id': self.bridge.initialize_id,
                            'subtype': 'success', 'response': response}})

    def test_missing_lists_and_reported_empty_keep_distinct_flags(self):
        self.initialize()
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'model': 'fixture', 'tools': []})
        data = self.events[-1][1]
        self.assertTrue(data['reported']['tools'])
        self.assertFalse(data['reported']['skills'])
        self.assertFalse(data['reported']['commands'])
        self.assertEqual([], data['tools'])

    def test_system_names_keep_initialize_descriptions_by_exact_name_only(self):
        self.initialize([{'name': 'company:report', 'description': 'Company report'},
                         {'name': 'other:report', 'description': 'Other report'},
                         {'name': 'not-in-init', 'description': 'Must not be reintroduced'}])
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'model': 'fixture',
                            'slash_commands': ['other:report', {'name': 'company:report', 'argumentHint': 'title'}]})
        data = self.events[-1][1]
        self.assertTrue(data['reported']['commands'])
        self.assertEqual(['Other report', 'Company report'], [row['description'] for row in data['slashCommands']])
        self.assertEqual(2, len(data['slashCommands']))

    def test_initialize_empty_and_invalid_name_do_not_invent_commands_or_crash(self):
        self.initialize([])
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'slash_commands': [{'name': []}]})
        data = self.events[-1][1]
        self.assertTrue(data['reported']['commands'])
        self.assertIsNone(self.bridge.process)


class CatalogRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['must-not-start'])
        self.addCleanup(self.app.close)
        self.app.companion.client = Mock()
        self.app.companion.client.skill_inventory.return_value = {'skills': []}
        self.sid = self.app.create(str(self.work), True)['id']
        self.server = Server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def request(self, route, token=True, origin=None):
        headers = {'Authorization': 'Bearer ' + self.app.token} if token else {}
        if origin:
            headers['Origin'] = origin
        with urlopen(Request(self.server.origin + route, headers=headers), timeout=3) as response:
            return json.loads(response.read())

    def test_route_reuses_auth_origin_and_does_not_start_claude(self):
        with patch('local_app.server.ClaudeSession', side_effect=AssertionError('must not start')), \
                patch('local_app.bridge.subprocess.Popen', side_effect=AssertionError('must not start')):
            self.assertEqual('no-session', self.request('/api/capabilities')['status'])
            result = self.request('/api/capabilities?id=' + self.sid)
            self.assertEqual('awaiting-runtime', result['status'])
            for kwargs in ({'token': False}, {'origin': 'https://another-site.example'}):
                with self.subTest(kwargs=kwargs), self.assertRaises(HTTPError) as caught:
                    self.request('/api/capabilities?id=' + self.sid, **kwargs)
                self.assertEqual(403, caught.exception.code)
        self.app.companion.client.skill_inventory.assert_called_once_with(str(self.work))
        self.assertIsNone(self.app.get(self.sid)['bridge'])

    def test_live_state_is_from_actual_bridge_not_stored_connected_flag(self):
        item = self.app.get(self.sid)
        item['connection'] = {'connected': True, 'tools': ['Read'], 'reported': {'tools': True}}
        self.assertEqual('last-seen', self.request('/api/capabilities?id=' + self.sid)['status'])
        item['bridge'] = Mock(closed=False)
        self.assertEqual('live', self.request('/api/capabilities?id=' + self.sid)['status'])
        item['bridge'] = None

    def test_untrusted_session_never_calls_installed_client(self):
        self.app.get(self.sid)['trusted'] = False
        result = self.request('/api/capabilities?id=' + self.sid)
        self.assertEqual('needs-trust', result['installed']['state'])
        self.app.companion.client.skill_inventory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
