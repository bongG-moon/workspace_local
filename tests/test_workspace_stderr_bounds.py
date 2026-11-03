"""CLI diagnostics stay bounded while the existing reader drains every byte."""
import io
import tracemalloc
from types import SimpleNamespace
import unittest

from local_app.bridge import ClaudeSession


class RepeatedBytes(io.RawIOBase):
    """Produce a large newline-free stream without allocating the whole input."""
    def __init__(self, size):
        super().__init__()
        self.remaining = size

    def readable(self):
        return True

    def readinto(self, target):
        count = min(len(target), self.remaining)
        target[:count] = b'x' * count
        self.remaining -= count
        return count


class StderrBoundsTests(unittest.TestCase):
    def bridge(self, stream):
        bridge = ClaudeSession(['fixture'], {'help': ''}, '.', lambda *_: None)
        bridge.process = SimpleNamespace(stderr=stream)
        return bridge

    def test_no_newline_stream_is_fully_drained_with_bounded_peak_memory(self):
        source = RepeatedBytes(16 * 1024 * 1024)
        stream = io.BufferedReader(source)
        bridge = self.bridge(stream)
        tracemalloc.start()
        try:
            bridge._read_stderr()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(0, source.remaining)
        self.assertTrue(stream.closed)
        self.assertEqual(['x' * 2000], list(bridge.stderr))
        self.assertLess(peak, 256 * 1024)

    def test_long_line_is_truncated_once_and_following_lines_are_preserved(self):
        bridge = self.bridge(io.BytesIO(b'x' * 20000 + b'\nnext\n\nlast'))
        bridge._read_stderr()
        self.assertEqual(['x' * 2000, 'next\n', '\n', 'last'], list(bridge.stderr))

    def test_utf8_character_crossing_chunk_boundary_is_not_replaced(self):
        text = '\uac00' * 1600 + '\n'
        bridge = self.bridge(io.BytesIO(text.encode('utf-8')))
        bridge._read_stderr()
        self.assertEqual([text], list(bridge.stderr))

    def test_partial_utf8_at_eof_keeps_existing_replacement_behavior(self):
        bridge = self.bridge(io.BytesIO(b'last\xe2\x82'))
        bridge._read_stderr()
        self.assertEqual(['last\ufffd'], list(bridge.stderr))

    def test_many_long_lines_retain_at_most_thirty_diagnostics(self):
        bridge = self.bridge(io.BytesIO(b'x' * 3000 + b'\n' + b'y\n' * 35))
        bridge._read_stderr()
        self.assertEqual(['y\n'] * 30, list(bridge.stderr))

    def test_read_error_closes_the_owned_stream(self):
        class FailingStream(io.BytesIO):
            def readline(self, size=-1):
                raise OSError('fixture read failure')

        stream = FailingStream()
        bridge = self.bridge(stream)
        with self.assertRaisesRegex(OSError, 'fixture read failure'):
            bridge._read_stderr()
        self.assertTrue(stream.closed)


if __name__ == '__main__':
    unittest.main()
