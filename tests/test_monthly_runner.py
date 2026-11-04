"""Offline safety tests for the monthly systemd runner."""

import fcntl
import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from deploy import run_monthly


MONTH = "2026-11"
NOW = datetime(2026, 11, 1, 14, 0)  # 09:00 America/New_York


def completed(payload: dict, returncode: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], payload["code"] if returncode is None else returncode)


class FakeInvoker:
    def __init__(self, payload: dict | None = None, *, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        kwargs["stdout"].write(json.dumps(self.payload).encode("utf-8"))
        return completed(self.payload)


def result(outcome: str, code: int = 0, written: bool = False) -> dict:
    return {
        "outcome": outcome,
        "code": code,
        "written": written,
        "playlist": f"Monthly Recs {MONTH}",
        "playlist_id": "private-playlist-id",
        "message": "safe test result",
    }


class MonthlyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / "state"
        self.repo_root = Path(self.temp.name) / "repo"
        self.repo_root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_runner(self, invoker, *, write=True):
        return run_monthly.run_once(
            state_dir=self.state_dir,
            repo_root=self.repo_root,
            now=NOW,
            write=write,
            invoke=invoker,
        )

    def read_state(self):
        return json.loads((self.state_dir / "state.json").read_text())

    def outbox_files(self):
        return sorted((self.state_dir / "outbox").glob("*.json"))

    def test_success_records_atomic_private_state_and_outbox(self):
        invoker = FakeInvoker(result("written", written=True))
        with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
            self.assertEqual(self.run_runner(invoker), 0)

        state = self.read_state()
        self.assertEqual(state["status"], "success")
        self.assertEqual(state["month"], MONTH)
        self.assertEqual(state["outcome"], "written")
        self.assertEqual(state["exit_code"], 0)
        self.assertEqual(state["success_idempotency_key"], f"monthly-recommend:{MONTH}")
        self.assertTrue(state["updated_at"].endswith("Z"))
        self.assertEqual(stat.S_IMODE((self.state_dir / "state.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)

        outbox = self.outbox_files()
        self.assertEqual(len(outbox), 1)
        event = json.loads(outbox[0].read_text())
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["success_idempotency_key"], f"monthly-recommend:{MONTH}")
        self.assertNotIn("private-playlist-id", outbox[0].read_text())
        self.assertNotIn("expected-owner", outbox[0].read_text())
        command, options = invoker.calls[0]
        self.assertIn("--write", command)
        self.assertEqual(command[-2:], ["--month", MONTH])
        self.assertEqual(options["timeout"], run_monthly.MAX_RUNTIME_SECONDS)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)

    def test_successful_month_is_idempotently_skipped(self):
        with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
            self.assertEqual(self.run_runner(FakeInvoker(result("written", written=True))), 0)
            second = FakeInvoker(result("written", written=True))
            self.assertEqual(self.run_runner(second), 0)
        self.assertEqual(second.calls, [])
        self.assertEqual(len(self.outbox_files()), 1)

    def test_empty_outcome_is_failure_and_does_not_record_success(self):
        invoker = FakeInvoker(result("no_candidates"))
        with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
            self.assertEqual(self.run_runner(invoker), 1)
        state = self.read_state()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["outcome"], "no_candidates")
        self.assertIsNone(state["success_idempotency_key"])
        self.assertEqual(json.loads(self.outbox_files()[0].read_text())["status"], "failed")

    def test_no_seeds_is_not_completed(self):
        invoker = FakeInvoker(result("no_seeds", code=2))
        with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
            self.assertEqual(self.run_runner(invoker), 2)
        state = self.read_state()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["outcome"], "no_seeds")
        self.assertIsNone(state["success_idempotency_key"])

    def test_timeout_is_recorded_without_raw_subprocess_output(self):
        invoker = FakeInvoker(error=subprocess.TimeoutExpired(cmd="hidden", timeout=5, output=b"secret"))
        with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
            self.assertEqual(self.run_runner(invoker), 124)
        state_text = (self.state_dir / "state.json").read_text()
        outbox_text = self.outbox_files()[0].read_text()
        self.assertIn('"outcome":"timeout"', state_text)
        self.assertNotIn("secret", state_text + outbox_text)

    def test_missing_expected_owner_fails_closed_before_subprocess(self):
        invoker = FakeInvoker(result("written", written=True))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.run_runner(invoker), 1)
        self.assertEqual(invoker.calls, [])
        self.assertEqual(self.read_state()["outcome"], "configuration_error")

    def test_lock_is_nonblocking(self):
        self.state_dir.mkdir(mode=0o700)
        lock_path = self.state_dir / "runner.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            invoker = FakeInvoker(result("written", written=True))
            with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
                self.assertEqual(self.run_runner(invoker), 75)
            self.assertEqual(invoker.calls, [])
        finally:
            os.close(fd)

    def test_dry_run_does_not_record_state_or_outbox(self):
        invoker = FakeInvoker(result("preview"))
        self.assertEqual(self.run_runner(invoker, write=False), 0)
        self.assertFalse((self.state_dir / "state.json").exists())
        self.assertFalse((self.state_dir / "outbox").exists())
        self.assertIn("--dry-run", invoker.calls[0][0])

    def test_invalid_state_stops_before_playlist_write(self):
        self.state_dir.mkdir(mode=0o700)
        (self.state_dir / "state.json").write_text('{"schema_version": 1}')
        invoker = FakeInvoker(result("written", written=True))
        with mock.patch.dict(os.environ, {run_monthly.EXPECTED_USER_ENV: "expected-owner"}):
            self.assertEqual(self.run_runner(invoker), 1)
        self.assertEqual(invoker.calls, [])
        self.assertEqual(json.loads(self.outbox_files()[0].read_text())["outcome"], "state_unreadable")


if __name__ == "__main__":
    unittest.main()
