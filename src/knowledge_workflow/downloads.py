"""Bounded public-artifact downloads; integrity failures are never retried away."""
import hashlib
import http.client
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _transient(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 429, 500, 502, 503, 504}
    if isinstance(error, urllib.error.URLError):
        return _transient(error.reason)
    if isinstance(error, ssl.SSLCertVerificationError):
        return False
    return isinstance(error, (ConnectionError, TimeoutError, http.client.IncompleteRead))


def _retry(operation, label, attempts=4):
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            if not _transient(error) or attempt == attempts:
                raise
            delay = 2 ** (attempt - 1)
            print(f"download: {label}; transient {type(error).__name__}; "
                  f"retry {attempt + 1}/{attempts} in {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)


def fetch_json(url, *, label):
    def attempt():
        request = urllib.request.Request(url, headers={"User-Agent": "knowledge-workflow"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    return _retry(attempt, label)


def download_file(url, destination, expected_hash, *, expected_bytes=None, label=None):
    """Retry transport failures from byte zero; publish only a complete verified file."""
    destination = Path(destination)
    label = label or destination.name

    def attempt():
        descriptor, name = tempfile.mkstemp(prefix=".kw-download-", dir=destination.parent)
        temporary = Path(name)
        try:
            received = 0
            checksum = hashlib.sha256()
            started = last = time.monotonic()
            request = urllib.request.Request(url, headers={"User-Agent": "knowledge-workflow"})
            with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(request, timeout=30) as response:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if expected_bytes is not None and received > expected_bytes:
                        raise ValueError("artifact_download_size_exceeded")
                    output.write(block)
                    checksum.update(block)
                    now = time.monotonic()
                    if now - started > 600:
                        raise TimeoutError("artifact_download_attempt_deadline")
                    if now - last >= 5:
                        print(f"download: {label}; {received}/{expected_bytes or '?'} bytes",
                              file=sys.stderr, flush=True)
                        last = now
            if expected_bytes is not None and received != expected_bytes:
                raise http.client.IncompleteRead(b"", expected_bytes - received)
            if checksum.hexdigest() != expected_hash:
                raise ValueError("artifact_download_hash_mismatch")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    return _retry(attempt, label)
