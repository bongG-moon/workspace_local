"""Closing the app cancels only its owned native picker before draining requests."""
from contextlib import nullcontext
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_app.owned_process import CancelledError
from local_app.server import LocalApp


@unittest.skipUnless(os.name == 'nt', 'Windows picker lifecycle')
class PickerShutdownTests(unittest.TestCase):
    def test_app_close_cancels_picker_and_drains_its_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = LocalApp(root / 'state', demo=True)
            entered = threading.Event()
            errors = []

            def wait_for_close(args, *, cancel_event, **kwargs):
                entered.set()
                if not cancel_event.wait(4):
                    raise AssertionError('Shutdown did not cancel the picker')
                raise CancelledError()

            def pick():
                try:
                    with app.operation():
                        app.pick('folder')
                except Exception as error:
                    errors.append(error)

            with patch('local_app.server.private_picker_directory', return_value=nullcontext(root)), \
                    patch('local_app.server.workspace_window_handle', return_value=0), \
                    patch('local_app.server.run_owned', side_effect=wait_for_close) as run:
                worker = threading.Thread(target=pick)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(app.close())
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(app._active_operations, 0)
                    self.assertFalse(app.dialog_lock.locked())
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], ValueError)
                    self.assertIn('파일 선택 창', str(errors[0]))
                    with self.assertRaises(ValueError):
                        app.pick('folder')
                    self.assertEqual(run.call_count, 1)
                finally:
                    app._picker_cancel.set()
                    worker.join(5)
                    app.close()
