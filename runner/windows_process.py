
"""Bounded Windows subprocesses with an owned, non-breakaway Job Object."""
import ctypes as ct
from ctypes import wintypes as wt
import os
import re
import subprocess
import sys
from threading import Event, Thread
from time import monotonic


STREAM_LIMIT = 1024 * 1024
SHUTDOWN_SECONDS = 5
MAX_PROCESSES = 32
MEMORY_BYTES = 8 * 1024**3
CPU_SECONDS = 600

# The trusted isolated Python bootstrap starts the requested executable only
# after the parent has assigned it to the job. The private release pipe carries
# the optional live credential, so only the target receives it in its environment.
BOOTSTRAP = """
import os, subprocess, sys
if sys.stdin.buffer.read(1) != b'+':
    sys.exit(125)
credential = sys.stdin.buffer.read()
env = os.environ.copy()
if credential:
    env['CODEX_API_KEY'] = credential.decode('utf-8')
sys.exit(subprocess.call(sys.argv[1:], env=env, stdin=subprocess.DEVNULL))
"""


class BasicLimits(ct.Structure):
    _fields_ = [
        ('per_process_time', ct.c_longlong), ('job_time', ct.c_longlong),
        ('flags', wt.DWORD), ('min_working_set', ct.c_size_t),
        ('max_working_set', ct.c_size_t), ('active_limit', wt.DWORD),
        ('affinity', ct.c_size_t), ('priority', wt.DWORD), ('scheduling', wt.DWORD),
    ]


class JobLimits(ct.Structure):
    _fields_ = [
        ('basic', BasicLimits), ('io', ct.c_ulonglong * 6),
        ('process_memory', ct.c_size_t), ('job_memory', ct.c_size_t),
        ('peak_process_memory', ct.c_size_t), ('peak_job_memory', ct.c_size_t),
    ]


class JobAccounting(ct.Structure):
    _fields_ = [
        ('times', ct.c_longlong * 4), ('page_faults', wt.DWORD),
        ('total', wt.DWORD), ('active', wt.DWORD), ('terminated', wt.DWORD),
    ]


class JobAccountingAndIO(ct.Structure):
    _fields_ = [('accounting', JobAccounting), ('io', ct.c_ulonglong * 6)]


class Job:
    def __init__(self):
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.create = self.bind(kernel, 'CreateJobObjectW', wt.HANDLE, ct.c_void_p, wt.LPCWSTR)
        self.configure = self.bind(kernel, 'SetInformationJobObject', wt.BOOL,
                                   wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD)
        self.assign = self.bind(kernel, 'AssignProcessToJobObject', wt.BOOL, wt.HANDLE, wt.HANDLE)
        self.terminate = self.bind(kernel, 'TerminateJobObject', wt.BOOL, wt.HANDLE, wt.UINT)
        self.query = self.bind(kernel, 'QueryInformationJobObject', wt.BOOL,
                               wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.c_void_p)
        self.close = self.bind(kernel, 'CloseHandle', wt.BOOL, wt.HANDLE)

    @staticmethod
    def bind(kernel, name, result, *args):
        function = getattr(kernel, name)
        function.restype = result
        function.argtypes = args
        return function

    @staticmethod
    def checked(value):
        if not value:
            raise ct.WinError(ct.get_last_error())
        return value

    def __enter__(self):
        self.handle = self.checked(self.create(None, None))
        limits = JobLimits()
        # KILL_ON_JOB_CLOSE | JOB_MEMORY | ACTIVE_PROCESS | JOB_TIME.
        limits.basic.flags = 0x2000 | 0x0200 | 0x0008 | 0x0004
        limits.basic.active_limit = MAX_PROCESSES
        limits.basic.job_time = CPU_SECONDS * 10_000_000
        limits.job_memory = MEMORY_BYTES
        try:
            self.checked(self.configure(self.handle, 9, ct.byref(limits), ct.sizeof(limits)))
        except OSError:
            self.close(self.handle)
            raise
        return self

    def __exit__(self, *_):
        self.checked(self.close(self.handle))

    def active(self):
        accounting = JobAccounting()
        self.checked(self.query(self.handle, 1, ct.byref(accounting), ct.sizeof(accounting), None))
        return accounting.active


    def progress(self):
        counters = JobAccountingAndIO()
        # JobObjectBasicAndIoAccountingInformation, including exited descendants.
        self.checked(self.query(self.handle, 8, ct.byref(counters), ct.sizeof(counters), None))
        return {
            'read_bytes': counters.io[3],
            'written_bytes': counters.io[4],
            'other_io_bytes': counters.io[5],
            'processes_started': counters.accounting.total,
            'processes_exited': counters.accounting.total - counters.accounting.active,
        }


def controlled_env():
    allowed = {'systemroot', 'windir', 'systemdrive', 'comspec', 'pathext', 'temp', 'tmp'}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    system = os.environ['SystemRoot']
    env['PATH'] = os.pathsep.join([os.path.join(system, 'System32'), system])
    return env


def redact(raw, secret):
    if not secret:
        return raw
    secret = secret.encode('utf-8')
    # Also mask a partial credential at a cutoff between bytes.
    tail = next((length for length in range(min(len(raw), len(secret) - 1), 3, -1)
                 if raw.endswith(secret[:length])), 0)
    if tail:
        raw = raw[:-tail] + b'[REDACTED]'
    raw = raw.replace(secret, b'[REDACTED]')
    return re.sub(rb'(?i)(?:sk-[A-Za-z0-9_-]{8,}|Bearer +[A-Za-z0-9._~-]{8,})', b'[REDACTED]', raw)


def run_process(argv, cwd, env, wall, idle, *, inspect_stdout=None,
                observe_progress=None, renew_on_job_writes=False, stream_limit=STREAM_LIMIT):
    """Run one target with bounded streams and optional job telemetry.

    Stream capture always contributes to activity. Offline callers can opt in
    to job write activity for silent work. Reads, other I/O and process counts
    remain telemetry. Job write accounting measures activity, not valid artifacts.
    """
    if not 0 < stream_limit <= STREAM_LIMIT:
        raise ValueError('Invalid stream limit')
    bootstrap_env = env.copy()
    secret = bootstrap_env.pop('CODEX_API_KEY', '')
    release_payload = b'+' + secret.encode('utf-8')
    buffers = [bytearray(), bytearray()]
    overflow, read_error = Event(), Event()
    released, release_error = Event(), Event()
    last_activity = [monotonic(), monotonic()]
    readers = []
    process = None
    cause = None
    tree_drained = False
    final_progress = None
    start = monotonic()

    def capture(index, stream):
        try:
            with stream:
                while chunk := os.read(stream.fileno(), 16384):
                    last_activity[index] = monotonic()
                    remaining = stream_limit - len(buffers[index])
                    buffers[index].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
                        return
        except OSError:
            read_error.set()

    def release(stream):
        try:
            with stream:
                stream.write(release_payload)
            released.set()
        except OSError:
            release_error.set()

    try:
        with Job() as job:
            process = subprocess.Popen(
                [sys.executable, '-I', '-B', '-c', BOOTSTRAP, *map(str, argv)],
                cwd=cwd, env=bootstrap_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                job.checked(job.assign(job.handle, int(process._handle)))
            except OSError:
                # The bootstrap is still waiting and has not started the target.
                process.stdin.close()
                process.kill()
                process.wait(timeout=SHUTDOWN_SECONDS)
                raise
            progress_before = {}
            sample_job = observe_progress is not None or renew_on_job_writes
            next_sample = start
            last_job_write = start
            for index, stream in enumerate((process.stdout, process.stderr)):
                reader = Thread(target=capture, args=(index, stream), daemon=True)
                reader.start()
                readers.append(reader)
            # Keep the watchdog active even if the pipe write stalls.
            writer = Thread(target=release, args=(process.stdin,), daemon=True)
            writer.start()
            inspected = 0
            try:
                while True:
                    now = monotonic()
                    if inspect_stdout is not None and len(buffers[0]) != inspected:
                        observed = bytes(buffers[0])
                        inspected = len(observed)
                        cause = inspect_stdout(observed)
                    if not cause:
                        if overflow.is_set():
                            cause = 'stream-limit'
                        elif read_error.is_set():
                            cause = 'capture-error'
                        elif release_error.is_set():
                            cause = 'supervisor-error'
                        elif now - start >= wall:
                            cause = 'wall-timeout'
                        else:
                            if sample_job and now >= next_sample:
                                counters = job.progress()
                                counters.update(stdout_bytes=len(buffers[0]), stderr_bytes=len(buffers[1]))
                                write_renewal = (renew_on_job_writes and counters['written_bytes'] >
                                                 progress_before.get('written_bytes', 0))
                                if write_renewal:
                                    last_job_write = now
                                if observe_progress is not None and any(
                                        value > progress_before.get(key, 0)
                                        for key, value in counters.items()):
                                    observe_progress({
                                        'elapsed_seconds': round(now - start, 3),
                                        'bootstrap_pid': process.pid,
                                        'idle_renewed_by_job_write': write_renewal,
                                        **counters,
                                    })
                                progress_before = counters
                                next_sample = now + 0.25
                            if process.poll() is not None and job.active() == 0:
                                break
                            if now - max(*last_activity, last_job_write) >= idle:
                                cause = 'idle-timeout'
                    if cause:
                        break
                    overflow.wait(0.02)
            except KeyboardInterrupt:
                cause = 'cancelled'
            except OSError:
                cause = 'supervisor-error'
            if cause:
                job.checked(job.terminate(job.handle, 124))
            shutdown = monotonic() + SHUTDOWN_SECONDS
            process.wait(timeout=max(0, shutdown - monotonic()))
            writer.join(max(0, shutdown - monotonic()))
            for reader in readers:
                reader.join(max(0, shutdown - monotonic()))
            while job.active() and monotonic() < shutdown:
                Event().wait(0.02)
            tree_drained = job.active() == 0
            if observe_progress is not None:
                final_progress = job.progress()
            if overflow.is_set():
                cause = cause or 'stream-limit'
            if read_error.is_set() or any(reader.is_alive() for reader in readers):
                cause = cause or 'capture-error'
            if not tree_drained or not released.is_set():
                cause = cause or 'supervisor-error'
    except (OSError, subprocess.SubprocessError) as error:
        cause = cause or type(error).__name__
    finally:
        if process is not None:
            for index, stream in enumerate((process.stdout, process.stderr)):
                if index >= len(readers):
                    stream.close()
            for reader in readers:
                if reader.is_alive():
                    reader.join(0.25)
    raw = [bytes(buffer) for buffer in buffers]
    streams = [redact(buffer, secret)[:stream_limit] for buffer in raw]
    observation = {
        'launched': released.is_set(), 'exit_code': process.poll() if process is not None else None,
        'stop_reason': cause, 'elapsed_seconds': round(monotonic() - start, 3),
        'job_drained': tree_drained, 'stream_limit_reached': overflow.is_set(),
        'evidence_redacted': streams != raw,
    }
    if observe_progress is not None:
        observation['job_counters'] = final_progress
    return observation, *streams
