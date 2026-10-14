"""Virtual task folders preserve workspace files and lazy CLI conversation history."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.history import HistoryStore
from local_app.server import LocalApp, Server
from local_app.session_groups import GroupRevisionConflict, MAX_BYTES, MAX_GROUPS, SessionGroups


class SessionGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionGroups(self.root)
        self.ids = {str(uuid.uuid4()), str(uuid.uuid4())}

    def change(self, action, **values):
        return self.store.change({'action': action, **values}, self.ids)

    def create(self, name='보고서'):
        return self.change('create', name=name)['groups'][-1]['id']

    def test_create_move_rename_collapse_and_reload(self):
        self.assertFalse(self.store.path.exists())
        gid = self.create()
        sid = next(iter(self.ids))
        self.change('move', id=sid, groupId=gid)
        self.change('rename', groupId=gid, name='  주간 보고서  ')
        result = self.change('collapse', groupId=gid, collapsed=True)
        self.assertEqual({'id': gid, 'name': '주간 보고서', 'collapsed': True}, result['groups'][0])
        self.assertEqual({sid: gid}, result['assignments'])
        self.assertEqual(4, result['revision'])
        self.assertEqual(result, SessionGroups(self.root).snapshot())
        result['groups'][0]['name'] = 'outside mutation'
        result['assignments'].clear()
        self.assertEqual('주간 보고서', self.store.groups[0]['name'])
        self.assertEqual(gid, self.store.group_for(sid))

    def test_duplicate_names_get_bounded_suffix_and_rename_excludes_self(self):
        first = self.create('Folder')
        second = self.create('folder')
        third = self.create('FOLDER')
        self.assertEqual(['Folder', 'folder (2)', 'FOLDER (3)'], [row['name'] for row in self.store.groups])
        self.change('rename', groupId=first, name='folder')
        self.assertEqual('folder', self.store.groups[0]['name'])
        self.create('가' * 80)
        self.create('가' * 80)
        self.assertEqual(80, len(self.store.groups[-1]['name']))
        self.assertTrue(self.store.groups[-1]['name'].endswith(' (2)'))

    def test_delete_nonempty_folder_unassigns_only_its_members(self):
        gid = self.create()
        other = self.create('다른 폴더')
        sid, second = tuple(self.ids)
        self.change('move', id=sid, groupId=gid)
        self.change('move', id=second, groupId=other)
        after = self.change('delete', groupId=gid)
        self.assertEqual([other], [group['id'] for group in after['groups']])
        self.assertEqual({second: other}, after['assignments'])
        self.assertIsNone(self.store.group_for(sid))
        self.assertEqual(after, SessionGroups(self.root).snapshot())

    def test_move_to_root_remains_idempotent(self):
        gid = self.create()
        sid = next(iter(self.ids))
        self.change('move', id=sid, groupId=gid)
        self.change('move', id=sid, groupId=None)
        before = self.store.snapshot()
        self.assertEqual(before, self.change('move', id=sid, groupId=None))
        after = self.change('delete', groupId=gid)
        self.assertEqual([], after['groups'])
        self.assertEqual({}, after['assignments'])

    def test_collapsed_folder_delete_unassigns_all_members(self):
        gid = self.create()
        for sid in self.ids:
            self.change('move', id=sid, groupId=gid)
        self.change('collapse', groupId=gid, collapsed=True)
        result = self.change('delete', groupId=gid, revision=self.store.revision)
        self.assertEqual([], result['groups'])
        self.assertEqual({}, result['assignments'])
        self.assertEqual(result, SessionGroups(self.root).snapshot())

    def test_new_empty_folder_can_be_deleted(self):
        gid = self.create()
        result = self.change('delete', groupId=gid, revision=self.store.revision)
        self.assertEqual([], result['groups'])
        self.assertEqual({}, result['assignments'])
        self.assertEqual(result, SessionGroups(self.root).snapshot())

    def test_stale_delete_is_rejected_then_current_revision_dissolves_folder(self):
        gid = self.create()
        empty_revision = self.store.revision
        sid = next(iter(self.ids))
        self.change('move', id=sid, groupId=gid, revision=empty_revision)
        before, snapshot = self.store.path.read_bytes(), self.store.snapshot()
        with self.assertRaises(GroupRevisionConflict):
            self.change('delete', groupId=gid, revision=empty_revision)
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(snapshot, self.store.snapshot())
        self.assertEqual(gid, self.store.group_for(sid))
        result = self.change('delete', groupId=gid, revision=self.store.revision)
        self.assertEqual([], result['groups'])
        self.assertEqual({}, result['assignments'])
        self.assertEqual(snapshot['revision'] + 1, result['revision'])

    def test_move_to_another_folder_allows_deleting_now_empty_folder(self):
        first, second = self.create('보고서'), self.create('분석')
        sid = next(iter(self.ids))
        self.change('move', id=sid, groupId=first)
        self.change('move', id=sid, groupId=second)
        after = self.change('delete', groupId=first)
        self.assertEqual([second], [group['id'] for group in after['groups']])
        self.assertEqual({sid: second}, after['assignments'])

    def test_invalid_fields_ids_values_and_unknown_sessions_do_not_write(self):
        gid = self.create()
        sid = next(iter(self.ids))
        before, snapshot = self.store.path.read_bytes(), self.store.snapshot()
        invalid = [
            {'action': 'create', 'name': ''}, {'action': 'create', 'name': 1},
            {'action': 'create', 'name': 'x' * 81}, {'action': 'create', 'name': 'a\nb'},
            {'action': 'create', 'name': 'a', 'workspace': 'elsewhere'},
            {'action': 'create', 'name': 'a', 'revision': True},
            {'action': 'collapse', 'groupId': gid, 'collapsed': 1},
            {'action': 'delete', 'groupId': []}, {'action': 'delete', 'groupId': str(uuid.uuid4())},
            {'action': 'move', 'id': str(uuid.uuid4()), 'groupId': gid},
            {'action': 'move', 'id': sid, 'groupId': ''},
            {'action': 'move', 'id': sid}, {'action': []}, {'action': 'reorder'},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.change(data, self.ids)
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(snapshot, self.store.snapshot())

    def test_maximum_groups_prevents_growth(self):
        self.store.groups = [{'id': str(uuid.uuid4()), 'name': str(index), 'collapsed': False}
                             for index in range(MAX_GROUPS)]
        with self.assertRaisesRegex(ValueError, '100'):
            self.create()
        self.assertFalse(self.store.path.exists())

    def test_failed_atomic_replace_preserves_disk_memory_and_removes_temp_file(self):
        gid = self.create()
        before, snapshot = self.store.path.read_bytes(), self.store.snapshot()
        with patch.object(Path, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.change('rename', groupId=gid, name='different')
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(snapshot, self.store.snapshot())
        self.assertEqual([], list(self.root.glob('*.tmp')))

    def test_revision_serializes_two_concurrent_actions(self):
        self.create()
        gate = threading.Barrier(3)
        results = []

        def mutate(name):
            gate.wait()
            try:
                results.append(self.change('create', name=name, revision=1))
            except GroupRevisionConflict as error:
                results.append(error)

        threads = [threading.Thread(target=mutate, args=(name,)) for name in ('A', 'B')]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, sum(isinstance(row, GroupRevisionConflict) for row in results))
        self.assertEqual(2, self.store.revision)
        self.assertEqual(2, len(self.store.groups))

    def test_damaged_and_oversized_files_remain_untouched_and_lock_mutations(self):
        gid, sid = str(uuid.uuid4()), next(iter(self.ids))
        good = {'schemaVersion': 1, 'groups': [{'id': gid, 'name': 'folder', 'collapsed': False}],
                'assignments': {sid: gid}, 'revision': 1}
        bad = [b'broken', b' ' * (MAX_BYTES + 1), b'[' * 20000 + b'0' + b']' * 20000]
        bad.extend(json.dumps(value).encode() for value in (
            good | {'revision': True}, good | {'groups': good['groups'] * 2},
            good | {'assignments': {sid: str(uuid.uuid4())}},
            good | {'groups': [{'id': gid, 'name': 'folder', 'collapsed': 'false'}]},
        ))
        for raw in bad:
            with self.subTest(size=len(raw)):
                self.store.path.write_bytes(raw)
                broken = SessionGroups(self.root)
                self.assertTrue(broken.warning)
                self.assertEqual([], broken.groups)
                with self.assertRaises(ValueError):
                    broken.change({'action': 'create', 'name': 'new'}, self.ids)
                self.assertEqual(raw, broken.path.read_bytes())


class SessionGroupRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.ids = [self.app.create(str(self.root), True, title='업무 ' + str(i))['id'] for i in range(3)]
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, route='/api/session-groups', data=None, auth=True):
        headers = {'Content-Type': 'application/json', 'Origin': self.server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + route, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def create_group(self, name='폴더'):
        code, result = self.request(data={'action': 'create', 'name': name})
        self.assertEqual(200, code, result)
        return result['groupId']

    def move(self, sid, gid):
        return self.request(data={'action': 'move', 'id': sid, 'groupId': gid})

    def test_auth_bootstrap_conflict_and_shutdown_guards(self):
        body = {'action': 'create', 'name': 'folder', 'revision': 0}
        self.assertEqual(403, self.request(data=body, auth=False)[0])
        self.assertEqual(403, self.request(auth=False)[0])
        self.assertFalse(self.app.session_groups.path.exists())
        code, result = self.request(data=body)
        self.assertEqual(200, code, result)
        self.assertEqual(result['sessionGroups'], self.request()[1])
        self.assertEqual(result['sessionGroups'], self.request('/api/bootstrap')[1]['sessionGroups'])
        code, result = self.request(data=body)
        self.assertEqual(409, code, result)
        self.assertEqual('session_groups_changed', result['code'])
        with patch.object(self.app, '_shutdown_state', 'draining'):
            self.assertEqual(409, self.request(data={'action': 'create', 'name': 'second'})[0])
        self.assertEqual(1, len(self.app.session_groups.groups))

    def test_folder_changes_do_not_touch_history_workspace_or_native_files(self):
        native = self.root / '.claude' / 'settings.local.json'
        native.parent.mkdir()
        native.write_text('{"keep":true}')
        marker = self.root / 'result.txt'
        marker.write_text('keep')
        paths = list((self.app.state / 'history-sessions').glob('*.json'))
        paths += [self.app.state / 'history-index.json', native, marker]
        before = {path: path.read_bytes() for path in paths if path.exists()}
        workspace = [item['workspace'] for item in self.app.sessions.values()]
        gid = self.create_group()
        self.assertEqual(200, self.move(self.ids[0], gid)[0])
        self.assertEqual(200, self.request(data={'action': 'delete', 'groupId': gid})[0])
        self.assertIsNone(self.app.session_groups.group_for(self.ids[0]))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(workspace, [item['workspace'] for item in self.app.sessions.values()])
        self.assertEqual(self.ids, list(self.app.sessions))

    def test_archived_assignment_survives_restore_and_reload(self):
        gid, sid = self.create_group(), self.ids[0]
        self.move(sid, gid)
        self.app.hide_session(sid, confirmed=True)
        self.assertEqual(400, self.move(sid, None)[0])
        self.assertEqual(gid, self.app.session_groups.group_for(sid))
        reloaded = LocalApp(self.app.state, demo=True)
        self.addCleanup(reloaded.close)
        reloaded.restore_session(sid)
        self.assertEqual(gid, reloaded.bootstrap()['sessionGroups']['assignments'][sid])
        self.assertFalse(reloaded.sessions[sid]['trusted'])
        self.assertIsNone(reloaded.sessions[sid]['bridge'])

    def test_group_delete_also_unassigns_archived_without_deleting_it(self):
        gid, sid = self.create_group(), self.ids[0]
        self.move(sid, gid)
        self.move(self.ids[1], gid)
        self.app.hide_session(sid, confirmed=True)
        self.assertEqual(200, self.request(data={'action': 'delete', 'groupId': gid})[0])
        self.assertIsNone(self.app.session_groups.group_for(sid))
        self.assertIsNone(self.app.session_groups.group_for(self.ids[1]))
        self.assertTrue(self.app.session_visibility.contains(sid))
        self.assertEqual(self.ids, list(self.app.sessions))
        self.app.restore_session(sid)
        self.assertIsNone(self.app.session_groups.group_for(sid))
        self.assertIn(sid, {item['id'] for item in self.app.visible_sessions()})

    def test_restored_task_remains_visible_after_folder_delete(self):
        gid, sid = self.create_group(), self.ids[0]
        self.move(sid, gid)
        self.app.hide_session(sid, confirmed=True)
        revision = self.app.session_groups.revision
        self.app.restore_session(sid)
        code, result = self.request(data={'action': 'delete', 'groupId': gid, 'revision': revision})
        self.assertEqual(200, code, result)
        self.assertIsNone(self.app.session_groups.group_for(sid))
        self.assertIn(sid, {item['id'] for item in self.app.visible_sessions()})

    def test_grouping_and_bootstrap_never_hydrate_unopened_conversations(self):
        gid = self.create_group()
        reloaded = LocalApp(self.app.state, demo=True)
        self.addCleanup(reloaded.close)
        self.assertTrue(all(item.get('_historyUnloaded') for item in reloaded.sessions.values()))
        with patch.object(reloaded.history, 'hydrate', side_effect=AssertionError('must stay lazy')):
            reloaded.change_session_groups({'action': 'move', 'id': self.ids[0], 'groupId': gid})
            reloaded.bootstrap()
            reloaded.change_session_groups({'action': 'delete', 'groupId': gid})
        self.assertTrue(all(item.get('_historyUnloaded') for item in reloaded.sessions.values()))

    def test_reorder_checks_virtual_folder_and_existing_pin_boundaries(self):
        a, b, c = self.ids
        gid = self.create_group()
        self.move(a, gid)
        self.assertEqual(400, self.request('/api/session/reorder', {'id': a, 'targetId': b, 'position': 'before'})[0])
        self.move(b, gid)
        self.assertEqual(200, self.request('/api/session/reorder', {'id': a, 'targetId': b, 'position': 'before'})[0])
        self.app.update_session(a, {'pinned': True})
        self.assertEqual(400, self.request('/api/session/reorder', {'id': a, 'targetId': b, 'position': 'before'})[0])
        self.app.update_session(b, {'pinned': True})
        self.assertEqual(200, self.request('/api/session/reorder', {'id': b, 'targetId': a, 'position': 'before'})[0])
        self.assertEqual(400, self.request('/api/session/reorder', {'id': c, 'targetId': a, 'position': 'after'})[0])

    def test_write_failure_reports_error_and_preserves_saved_snapshot(self):
        gid = self.create_group()
        before = self.app.session_groups.snapshot()
        with patch.object(HistoryStore, '_write', side_effect=OSError('synthetic failure')):
            self.assertEqual(400, self.move(self.ids[0], gid)[0])
        self.assertEqual(before, self.app.session_groups.snapshot())
        self.assertEqual(before, SessionGroups(self.app.state).snapshot())


if __name__ == '__main__':
    unittest.main()
