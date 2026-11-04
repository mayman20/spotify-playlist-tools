#!/usr/bin/env python3
"""Private systemd entrypoint for the monthly Spotify recommendation run.

This wrapper never writes Spotify credentials or recommendation contents to its
state/outbox. The recommendation CLI's ``--json`` output is parsed and reduced
to status metadata only.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/New_York")
EXPECTED_USER_ENV = "SPOTIFY_EXPECTED_USER_ID"
MAX_RUNTIME_SECONDS = 20 * 60
MAX_RESULT_BYTES = 64 * 1024
KNOWN_OUTCOMES = {
    "written", "preview", "no_candidates", "no_seeds",
    "configuration_error", "error", "failed",
}
DEFAULT_STATE_DIR = Path("/var/lib/spotify-toolkit/monthly-runner")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECOMMENDER = PROJECT_ROOT / "scripts" / "monthly_recommend" / "monthly_recommend.py"


class RunnerError(Exception):
    """An expected, sanitized runner failure."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_for(now: datetime | None = None) -> str:
    moment = now or datetime.now(TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TIMEZONE)
    return moment.astimezone(TIMEZONE).strftime("%Y-%m")


def _iso_utc(now: datetime | None = None) -> str:
    moment = now or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RunnerError("private state path is not a directory")
    os.chmod(path, 0o700)


def _atomic_json(path: Path, payload: dict) -> None:
    """Write JSON with mode 0600, fsync file and directory, then replace."""
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=".monthly-runner-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RunnerError("state file is not a regular file")
    os.chmod(path, 0o600)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError("state file is unreadable") from exc
    if not isinstance(state, dict):
        raise RunnerError("state file has an invalid format")
    if (
        state.get("schema_version") != 1
        or not isinstance(state.get("status"), str)
        or not isinstance(state.get("month"), str)
        or not isinstance(state.get("outcome"), str)
        or not isinstance(state.get("exit_code"), int)
        or not isinstance(state.get("success_idempotency_key"), (str, type(None)))
    ):
        raise RunnerError("state file has an invalid format")
    return state


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[bool]:
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def _write_outbox(state_dir: Path, *, month: str, status: str, outcome: str,
                  exit_code: int, created_at: str, success_key: str | None) -> None:
    outbox = state_dir / "outbox"
    _ensure_private_dir(outbox)
    event_id = f"{created_at.replace(':', '').replace('-', '')}-{uuid.uuid4().hex}"
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "created_at": created_at,
        "month": month,
        "status": status,
        "outcome": outcome,
        "exit_code": exit_code,
        "success_idempotency_key": success_key,
    }
    _atomic_json(outbox / f"{event_id}.json", payload)


def _persist_result(state_dir: Path, previous: dict | None, *, month: str,
                    status: str, outcome: str, exit_code: int,
                    now: datetime | None = None) -> None:
    updated_at = _iso_utc(now)
    success_month = previous.get("success_month") if previous else None
    success_key = previous.get("success_idempotency_key") if previous else None
    if status == "success":
        success_month = month
        success_key = f"monthly-recommend:{month}"

    state = {
        "schema_version": 1,
        "updated_at": updated_at,
        "status": status,
        "month": month,
        "outcome": outcome,
        "exit_code": exit_code,
        "success_month": success_month,
        "success_idempotency_key": success_key,
    }

    # Put the durable local event first. If state replacement then fails, a
    # retry remains safe: the playlist operation is month-idempotent and the
    # success key was not committed.
    _write_outbox(
        state_dir, month=month, status=status, outcome=outcome,
        exit_code=exit_code, created_at=updated_at,
        success_key=success_key if status == "success" else None,
    )
    _atomic_json(state_dir / "state.json", state)


def _invoke_cli(month: str, *, write: bool, timeout: int,
                python_executable: str = sys.executable,
                repo_root: Path = PROJECT_ROOT,
                invoke=subprocess.run) -> tuple[int, dict | None, str]:
    command = [python_executable, str(repo_root / "scripts" / "monthly_recommend" /
                                      "monthly_recommend.py"), "--json"]
    command.append("--write" if write else "--dry-run")
    command.extend(("--month", month))

    # Suppress stderr and spool stdout to an unlinked, mode-0600 temporary file.
    # Never forward CLI logs, track data, exceptions, or environment contents.
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            completed = invoke(
                command,
                cwd=str(repo_root),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, None, "timeout"
        except Exception:
            return 1, None, "subprocess_error"
        output.seek(0)
        raw = output.read(MAX_RESULT_BYTES + 1)

    if len(raw) > MAX_RESULT_BYTES:
        return 1, None, "result_too_large"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 1, None, "invalid_result"
    if not isinstance(payload, dict):
        return 1, None, "invalid_result"
    try:
        code = int(payload["code"])
        outcome = payload["outcome"]
        written = payload["written"]
        if not isinstance(outcome, str) or outcome not in KNOWN_OUTCOMES or not isinstance(written, bool):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return 1, None, "invalid_result"
    if completed.returncode != code:
        return 1, None, "exit_code_mismatch"
    return code, {"outcome": outcome, "written": written}, outcome


def run_once(*, state_dir: Path = DEFAULT_STATE_DIR, repo_root: Path = PROJECT_ROOT,
             now: datetime | None = None, write: bool = True,
             timeout: int = MAX_RUNTIME_SECONDS, python_executable: str = sys.executable,
             invoke=subprocess.run) -> int:
    month = _month_for(now)
    state_dir = Path(state_dir)
    try:
        _ensure_private_dir(state_dir)
    except (OSError, RunnerError):
        print("Monthly runner failed: private state directory unavailable.", file=sys.stderr)
        return 1

    try:
        with _exclusive_lock(state_dir / "runner.lock") as acquired:
            if not acquired:
                print("Monthly runner skipped: another run holds the lock.")
                return 75
            try:
                previous = _read_state(state_dir / "state.json")
            except RunnerError:
                # A corrupt/missing idempotency record must never be replaced
                # before a potentially destructive playlist write.
                _write_outbox(
                    state_dir, month=month, status="failed", outcome="state_unreadable",
                    exit_code=1, created_at=_iso_utc(now), success_key=None,
                )
                print("Monthly runner failed: prior state is unreadable; no Spotify action was started.", file=sys.stderr)
                return 1

            success_key = f"monthly-recommend:{month}"
            if write and previous and previous.get("success_idempotency_key") == success_key:
                print(f"Monthly recommendation already succeeded for {month}; skipping duplicate run.")
                return 0

            if write and not os.environ.get(EXPECTED_USER_ENV, "").strip():
                _persist_result(
                    state_dir, previous, month=month, status="failed",
                    outcome="configuration_error", exit_code=1, now=now,
                )
                print(f"Monthly runner failed: {EXPECTED_USER_ENV} is required; no Spotify action was started.", file=sys.stderr)
                return 1

            code, result, outcome = _invoke_cli(
                month, write=write, timeout=timeout, python_executable=python_executable,
                repo_root=Path(repo_root), invoke=invoke,
            )
            if not write:
                if code == 0 and result and result["outcome"] == "preview":
                    print(f"Monthly recommendation preview completed for {month}; no Spotify changes were requested.")
                    return 0
                print(f"Monthly recommendation preview failed for {month} ({outcome}); no state was recorded.", file=sys.stderr)
                return code or 1

            if code == 0 and result and result["outcome"] == "written" and result["written"]:
                _persist_result(
                    state_dir, previous, month=month, status="success",
                    outcome="written", exit_code=0, now=now,
                )
                print(f"Monthly recommendation write completed for {month}.")
                return 0

            # `no_candidates` can exit zero in the CLI, but is not a successful
            # monthly completion and must not advance the idempotency marker.
            failure_code = code if code != 0 else 1
            _persist_result(
                state_dir, previous, month=month, status="failed",
                outcome=outcome, exit_code=failure_code, now=now,
            )
            print(f"Monthly recommendation did not complete for {month} ({outcome}); success was not recorded.", file=sys.stderr)
            return failure_code
    except (OSError, RunnerError):
        print("Monthly runner failed: private state could not be updated.", file=sys.stderr)
        return 1


def _status(state_dir: Path) -> int:
    try:
        state = _read_state(Path(state_dir) / "state.json")
    except (OSError, RunnerError):
        print("Monthly runner status unavailable.", file=sys.stderr)
        return 1
    if state is None:
        print("No monthly runner state is recorded.")
        return 0
    fields = ("updated_at", "status", "month", "outcome", "exit_code",
              "success_month", "success_idempotency_key")
    print(json.dumps({key: state.get(key) for key in fields}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the private monthly Spotify recommendation job.")
    parser.add_argument("--dry-run", action="store_true", help="preview only; do not record state or write playlists")
    parser.add_argument("--status", action="store_true", help="show sanitized local runner state")
    parser.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("SPOTIFY_RUNNER_STATE_DIR", DEFAULT_STATE_DIR)))
    args = parser.parse_args(argv)
    if args.status:
        return _status(args.state_dir)
    return run_once(state_dir=args.state_dir, write=not args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
