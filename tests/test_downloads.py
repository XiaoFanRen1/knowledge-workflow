import hashlib
import io
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from knowledge_workflow.downloads import download_file, fetch_json


class Interrupted(io.BytesIO):
    def read(self, size=-1):
        if self.tell():
            raise ConnectionResetError(10054, "synthetic connection reset")
        return super().read(size)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.sleep = patch("knowledge_workflow.downloads.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_reset_metadata_connection_retries(self):
        reset = urllib.error.URLError(ConnectionResetError(10054, "synthetic"))
        with patch("knowledge_workflow.downloads.urllib.request.urlopen",
                   side_effect=[reset, io.BytesIO(b'{"version":"1"}')]) as request:
            self.assertEqual(fetch_json("https://example.invalid/data", label="metadata"), {"version": "1"})
        self.assertEqual(request.call_count, 2)
        self.sleep.assert_called_once_with(1)

    def test_midstream_reset_restarts_without_duplicate_bytes_or_partial_file(self):
        payload = b"complete verified artifact"
        with tempfile.TemporaryDirectory(prefix="kw-download-test-") as directory:
            target = Path(directory) / "artifact.whl"
            with patch("knowledge_workflow.downloads.urllib.request.urlopen",
                       side_effect=[Interrupted(b"partial"), io.BytesIO(payload)]) as request:
                download_file("https://example.invalid/file", target, hashlib.sha256(payload).hexdigest(), expected_bytes=len(payload))
            self.assertEqual(request.call_count, 2)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_exhausted_download_preserves_previous_file_and_removes_partial(self):
        with tempfile.TemporaryDirectory(prefix="kw-download-test-") as directory:
            target = Path(directory) / "artifact.whl"
            target.write_bytes(b"previous")
            with patch("knowledge_workflow.downloads.urllib.request.urlopen",
                       side_effect=ConnectionResetError(10054, "synthetic")) as request:
                with self.assertRaises(ConnectionResetError):
                    download_file("https://example.invalid/file", target, "0" * 64)
            self.assertEqual(request.call_count, 4)
            self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2, 4])
            self.assertEqual(target.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_hash_mismatch_is_fatal_without_retry(self):
        with tempfile.TemporaryDirectory(prefix="kw-download-test-") as directory:
            target = Path(directory) / "artifact.whl"
            with patch("knowledge_workflow.downloads.urllib.request.urlopen", return_value=io.BytesIO(b"wrong")) as request:
                with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                    download_file("https://example.invalid/file", target, "0" * 64)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.sleep.assert_not_called()

    def test_truncated_body_retries_but_oversized_body_is_fatal(self):
        payload = b"abcdef"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory(prefix="kw-download-test-") as directory:
            target = Path(directory) / "artifact.whl"
            with patch("knowledge_workflow.downloads.urllib.request.urlopen",
                       side_effect=[io.BytesIO(b"abc"), io.BytesIO(payload)]) as request:
                download_file("https://example.invalid/file", target, expected, expected_bytes=6)
            self.assertEqual(request.call_count, 2)
            with patch("knowledge_workflow.downloads.urllib.request.urlopen", return_value=io.BytesIO(b"abcdefg")) as request:
                with self.assertRaisesRegex(ValueError, "size_exceeded"):
                    download_file("https://example.invalid/file", target, expected, expected_bytes=6)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_permanent_http_tls_and_disk_errors_are_not_retried(self):
        errors = [urllib.error.HTTPError("https://example.invalid", 404, "missing", {}, None),
                  urllib.error.URLError(ssl.SSLCertVerificationError("synthetic untrusted certificate")),
                  OSError(28, "synthetic disk full")]
        for error in errors:
            with self.subTest(kind=type(error).__name__), patch(
                    "knowledge_workflow.downloads.urllib.request.urlopen", side_effect=error) as request:
                with self.assertRaises(type(error)):
                    fetch_json("https://example.invalid/data", label="metadata")
                self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_server_unavailable_retries_without_disabling_http_errors(self):
        error = urllib.error.HTTPError("https://example.invalid", 503, "temporary", {}, None)
        with patch("knowledge_workflow.downloads.urllib.request.urlopen",
                   side_effect=[error, io.BytesIO(b'{}')]) as request:
            self.assertEqual(fetch_json("https://example.invalid/data", label="metadata"), {})
        self.assertEqual(request.call_count, 2)
