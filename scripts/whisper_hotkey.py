#!/usr/bin/env python3
"""Local dictation controller for Linux and macOS; Python standard library only."""

import argparse
import contextlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import wave

if os.name == 'posix':
    import fcntl


class HotkeyError(Exception):
    pass


def data_home():
    if platform.system() == 'Darwin':
        return Path.home() / 'Library/Application Support/whisper-hotkey'
    base = Path(os.environ.get('XDG_DATA_HOME', ''))
    if not base.is_absolute():
        base = Path.home() / '.local/share'
    return base / 'whisper-hotkey'


def load_config():
    config_path = Path(os.environ.get('WHISPER_HOTKEY_CONFIG', data_home() / 'config.json')).expanduser()
    config = {'whisper_bin': str(data_home() / 'whisper.cpp/build/bin/whisper-cli'),
              'model_path': str(data_home() / 'whisper.cpp/models/ggml-base.bin'),
              'backend': 'auto', 'clipboard': 'auto', 'language': 'auto', 'threads': 4,
              'max_seconds': 300, 'transcribe_timeout': 300, 'notifications': True}
    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding='utf-8'))
        if not isinstance(loaded, dict):
            raise HotkeyError('Configuration must be a JSON object.')
        config.update(loaded)
    elif 'WHISPER_HOTKEY_CONFIG' in os.environ:
        raise HotkeyError(f'Configuration not found: {config_path}')
    for key, env in [('whisper_bin', 'WHISPER_BIN'), ('model_path', 'MODEL_PATH')]:
        config[key] = os.environ.get(env, config[key])
        config[key] = str(Path(config[key]).expanduser().absolute())
    for key in ('threads', 'max_seconds', 'transcribe_timeout'):
        if type(config[key]) is not int or config[key] < 1:
            raise HotkeyError(f'{key} must be a positive integer.')
    if not isinstance(config['language'], str) or not config['language']:
        raise HotkeyError('language must be a language code or auto.')
    return config


def private_dir(path):
    path = Path(path).expanduser().absolute()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise HotkeyError(f'Expected an owned, non-symlink directory: {path}')
    path.chmod(0o700)
    return path


def state_home():
    if 'WHISPER_HOTKEY_STATE_DIR' in os.environ:
        return private_dir(os.environ['WHISPER_HOTKEY_STATE_DIR'])
    if platform.system() == 'Darwin':
        base = Path.home() / 'Library/Caches'
    else:
        base = Path(os.environ.get('XDG_STATE_HOME', ''))
        if not base.is_absolute():
            base = Path.home() / '.local/state'
    return private_dir(base / 'whisper-hotkey')


def write_json(path, value):
    # Replace atomically so a separate hotkey invocation never reads partial state.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def acquire(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise HotkeyError(f'Unsafe lock file: {path}')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise


def command_exists(command):
    return shutil.which(command) is not None


def recording_command(config, output):
    backend = config['backend']
    if backend == 'auto':
        choices = ('sox',) if platform.system() == 'Darwin' else ('pw-record', 'parecord', 'arecord')
        backend = next((item for item in choices if command_exists('rec' if item == 'sox' else item)), '')
    commands = {
        'arecord': ['arecord', '-q', '-t', 'wav', '-f', 'S16_LE', '-r', '16000', '-c', '1'],
        'parecord': ['parecord', '--file-format=wav', '--format=s16le', '--rate=16000', '--channels=1'],
        'pw-record': ['pw-record', '--rate=16000', '--channels=1', '--format=s16'],
        'sox': ['rec', '-q', '-r', '16000', '-c', '1', '-b', '16', '-e', 'signed-integer'],
    }
    if backend not in commands or not command_exists(commands[backend][0]):
        raise HotkeyError('No recording backend available. Install alsa-utils (Linux) or sox (macOS), or configure backend.')
    command = commands[backend][:]
    device = config.get('device')
    if device:
        if backend in ('arecord', 'parecord', 'pw-record'):
            command += [{'arecord': '-D', 'parecord': '--device', 'pw-record': '--target'}[backend], str(device)]
        else:
            raise HotkeyError('For sox, select the default input in macOS Sound settings.')
    return command + [str(output)]


def clipboard_command(config):
    backend = config['clipboard']
    if backend == 'auto':
        if platform.system() == 'Darwin':
            backend = 'pbcopy'
        elif os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
            backend = 'wl-copy'
        elif os.environ.get('DISPLAY'):
            backend = 'xclip' if command_exists('xclip') else 'xsel'
        else:
            raise HotkeyError('No desktop clipboard session. Run from your desktop or use transcribe --no-copy.')
    commands = {'pbcopy': ['pbcopy'], 'wl-copy': ['wl-copy', '--type', 'text/plain;charset=utf-8'],
                'xclip': ['xclip', '-selection', 'clipboard'], 'xsel': ['xsel', '--clipboard', '--input']}
    if backend not in commands or not command_exists(commands[backend][0]):
        raise HotkeyError(f'Clipboard backend unavailable: {backend or "auto"}. Install the tool for your desktop session.')
    return commands[backend]


def notify(config, message):
    if not config.get('notifications'):
        return
    command = None
    if platform.system() == 'Darwin' and command_exists('osascript'):
        command = ['osascript', '-e', 'on run argv\ndisplay notification (item 1 of argv) with title "Whisper Hotkey"\nend run', message]
    elif command_exists('notify-send'):
        command = ['notify-send', 'Whisper Hotkey', message]
    if command:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)


def check_whisper(config):
    if not os.access(config['whisper_bin'], os.X_OK) or not Path(config['whisper_bin']).is_file():
        raise HotkeyError(f'Whisper executable not found or not executable: {config["whisper_bin"]}')
    model = Path(config['model_path'])
    if not model.is_file() or model.stat().st_size == 0:
        raise HotkeyError(f'Whisper model missing or empty: {model}')


def validate_audio(audio):
    try:
        with wave.open(str(audio), 'rb') as stream:
            if stream.getsampwidth() != 2 or stream.getnchannels() != 1 or stream.getframerate() != 16000:
                raise HotkeyError('Audio must be 16 kHz, mono, 16-bit PCM WAV.')
            if len(stream.readframes(1600)) < 3200:
                raise HotkeyError('Recording too short or empty (minimum 0.1 seconds).')
    except (wave.Error, EOFError) as exc:
        raise HotkeyError(f'Invalid WAV recording: {exc}') from exc


def transcribe(config, audio, state, copy=True):
    check_whisper(config)
    validate_audio(audio)
    with tempfile.TemporaryDirectory(prefix='transcribe-', dir=state) as directory:
        output = Path(directory) / 'result'
        command = [config['whisper_bin'], '-m', config['model_path'], '-f', str(audio),
                   '-otxt', '-of', str(output), '-nt', '-l', config['language'], '-t', str(config['threads'])]
        with (Path(directory) / 'whisper.log').open('w') as log:
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=log,
                                        timeout=config['transcribe_timeout'])
            except subprocess.TimeoutExpired as exc:
                raise HotkeyError('Whisper transcription timed out.') from exc
            if result.returncode:
                raise HotkeyError(f'Whisper failed (exit {result.returncode}); check the executable and model with doctor.')
        output = output.with_suffix('.txt')
        if not output.is_file():
            raise HotkeyError('Whisper produced no transcript file.')
        text = output.read_text(encoding='utf-8').strip()
        if not text:
            raise HotkeyError('Whisper produced an empty transcript.')
        # Keep one recovery transcript; recordings and inference logs are ephemeral.
        transcript = state / 'last-transcript.txt'
        fd, temporary = tempfile.mkstemp(dir=state, prefix='.transcript-')
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text + '\n')
        os.replace(temporary, transcript)
        if copy:
            try:
                result = subprocess.run(clipboard_command(config), input=text.encode('utf-8'),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.TimeoutExpired, HotkeyError) as exc:
                raise HotkeyError(f'Clipboard failed; transcript saved at {transcript}. {exc}') from exc
            if result.returncode:
                raise HotkeyError(f'Clipboard failed (exit {result.returncode}); transcript saved at {transcript}.')
        return text


def stop_recorder(recorder):
    if recorder.poll() is None:
        recorder.send_signal(signal.SIGINT)
        try:
            recorder.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:
            recorder.kill()
            recorder.wait()
            raise HotkeyError('Recorder did not finish writing within 10 seconds.') from exc


SESSION_FILES = ('recording.wav', 'recorder.log', 'stop', 'capture-ready', 'capture-error.json')


def validate_worker(state, session, lock_fd):
    if (session.parent != state or not session.name.startswith('session-')
            or session.is_symlink() or not session.is_dir()
            or session.stat().st_uid != os.getuid()):
        raise HotkeyError('Invalid internal recording session.')
    status = read_json(state / 'status.json')
    if status.get('session') != session.name or status.get('phase') != 'starting':
        raise HotkeyError('Internal recording session does not match current state.')
    inherited = os.fstat(lock_fd)
    expected = (state / 'session.lock').lstat()
    if (not stat.S_ISREG(inherited.st_mode) or not stat.S_ISREG(expected.st_mode)
            or inherited.st_uid != os.getuid() or inherited.st_nlink != 1
            or (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino)):
        raise HotkeyError('Invalid inherited recording lock.')
    probe = acquire(state / 'session.lock')
    if probe is not None:
        os.close(probe)
        raise HotkeyError('Internal recording lock is not held.')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise HotkeyError('Inherited recording descriptor does not own the lock.') from exc


def remove_artifacts(directory, names):
    for name in names:
        artifact = directory / name
        try:
            info = artifact.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            artifact.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        directory.rmdir()


def capture_guard(config, session, receiver, sender):
    # Close the inherited writer: EOF then means the recording worker died.
    # This guard inherits its lifecycle lock and owns the actual recorder child.
    sender.close()
    recorder = None
    stopping = False
    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        with (session / 'recorder.log').open('w') as log:
            audio = session / 'recording.wav'
            recorder = subprocess.Popen(recording_command(config, audio),
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            # Supported backends open their output during startup. Waiting for
            # that file avoids reporting success before the executable runs.
            startup_deadline = time.monotonic() + 8
            while not audio.is_file():
                if stopping or receiver.poll(0.05) or (session / 'stop').exists():
                    raise HotkeyError('Recording stopped before recording started.')
                if recorder.poll() is not None:
                    raise HotkeyError('Recorder exited before recording started; check your microphone and backend.')
                if time.monotonic() >= startup_deadline:
                    raise HotkeyError('Recorder startup timed out before opening its audio output.')
            if recorder.poll() is not None:
                raise HotkeyError('Recorder exited before recording started; check your microphone and backend.')
            (session / 'capture-ready').touch(mode=0o600)
            deadline = time.monotonic() + config['max_seconds']
            while not stopping and not receiver.poll(0.05):
                if recorder.poll() is not None:
                    raise HotkeyError('Recorder exited unexpectedly; check your microphone.')
                if time.monotonic() >= deadline:
                    raise HotkeyError('Recording time limit reached; audio discarded. Start again when ready.')
            stop_recorder(recorder)
    except (HotkeyError, OSError, ValueError) as exc:
        write_json(session / 'capture-error.json', {'message': str(exc)})
    finally:
        if recorder is not None:
            with contextlib.suppress(HotkeyError, OSError):
                stop_recorder(recorder)
        receiver.close()


def finish_capture(capture, sender):
    sender.close()
    capture.join(timeout=15)
    if capture.is_alive():
        capture.terminate()
        capture.join(timeout=15)
        raise HotkeyError('Recording guard did not shut down promptly.')
    if capture.exitcode != 0:
        raise HotkeyError('Recording guard exited unexpectedly.')


def worker(config, state, session, lock_fd):
    validate_worker(state, session, lock_fd)
    # Fork before any desktop notification calls. The guard only uses POSIX
    # subprocesses/pipes and holds the inherited lock until recording has ended.
    context = multiprocessing.get_context('fork')
    receiver, sender = context.Pipe(duplex=False)
    capture = context.Process(target=capture_guard, args=(config, session, receiver, sender))
    result = {'session': session.name, 'ok': False}
    def interrupted(signum, frame):
        raise HotkeyError('Recording interrupted.')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        capture.start()
        receiver.close()
        while not (session / 'capture-ready').exists():
            if not capture.is_alive():
                raise HotkeyError(read_json(session / 'capture-error.json').get('message', 'Recording guard failed to start.'))
            time.sleep(0.05)
        write_json(state / 'status.json', {'session': session.name, 'phase': 'recording'})
        notify(config, 'Recording started. Use the hotkey again to stop.')
        while not (session / 'stop').exists():
            if not capture.is_alive():
                raise HotkeyError(read_json(session / 'capture-error.json').get('message', 'Recorder exited unexpectedly.'))
            time.sleep(0.05)
        write_json(state / 'status.json', {'session': session.name, 'phase': 'processing'})
        finish_capture(capture, sender)
        error = read_json(session / 'capture-error.json')
        if error:
            raise HotkeyError(error['message'])
        notify(config, 'Recording stopped. Transcribing…')
        transcribe(config, session / 'recording.wav', state)
        result.update(ok=True, message='Transcription copied to clipboard.')
        notify(config, result['message'])
    except (HotkeyError, OSError, ValueError) as exc:
        result['message'] = str(exc)
        notify(config, 'Transcription failed. Run the command in a terminal for details.')
    finally:
        receiver.close()
        if capture.pid is not None:
            with contextlib.suppress(HotkeyError):
                finish_capture(capture, sender)
        else:
            sender.close()
        remove_artifacts(session, SESSION_FILES)
        write_json(state / 'result.json', result)
        write_json(state / 'status.json', {'phase': 'idle'})
        os.close(lock_fd)
    return 0 if result['ok'] else 1


def clean_stale_sessions(state):
    # Called only while holding the lifecycle lock, including before standalone
    # transcription. Never follow a symlink or remove unrelated files.
    for pattern, names in (('session-*', SESSION_FILES),
                           ('transcribe-*', ('whisper.log', 'result.txt'))):
        for directory in state.glob(pattern):
            if directory.is_symlink() or not directory.is_dir():
                continue
            remove_artifacts(directory, names)
    for pattern in ('.transcript-*', '.state-*'):
        for temporary in state.glob(pattern):
            info = temporary.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                temporary.unlink(missing_ok=True)


def stop_active_session(config, state, status):
    name = status.get('session', '')
    if not name.startswith('session-') or Path(name).name != name:
        raise HotkeyError('Invalid recording state.')
    session = state / name
    (session / 'stop').touch(mode=0o600)
    deadline = time.monotonic() + config['transcribe_timeout'] + 30
    while time.monotonic() < deadline:
        result = read_json(state / 'result.json')
        if result.get('session') == name:
            if not result.get('ok'):
                raise HotkeyError(result.get('message', 'Transcription failed.'))
            return result['message']
        probe = acquire(state / 'session.lock')
        if probe is not None:
            os.close(probe)
            # The worker writes its result before releasing the lock.
            result = read_json(state / 'result.json')
            if result.get('session') != name:
                raise HotkeyError('Recording worker exited unexpectedly; run toggle to recover.')
        time.sleep(0.05)
    raise HotkeyError('Timed out waiting for transcription; check status before trying again.')


def start_worker(config, state, session_fd):
    check_whisper(config)
    recording_command(config, state / 'check.wav')
    clipboard_command(config)
    session = Path(tempfile.mkdtemp(prefix='session-', dir=state))
    write_json(state / 'status.json', {'session': session.name, 'phase': 'starting'})
    # The lock is transferred via an inherited descriptor, then closed
    # here without LOCK_UN. It stays held until the worker has finished.
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_record',
                                str(session), str(session_fd)], pass_fds=(session_fd,),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = read_json(state / 'status.json')
        if status.get('session') == session.name and status.get('phase') == 'recording':
            return 'Recording started. Use the hotkey again to stop.'
        result = read_json(state / 'result.json')
        if result.get('session') == session.name:
            raise HotkeyError(result.get('message', 'Recording failed.'))
        if process.poll() is not None:
            raise HotkeyError('Recording worker failed to start.')
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=15)
    raise HotkeyError('Recorder startup timed out.')


def control(config, state, action):
    command_fd = acquire(state / 'command.lock')
    if command_fd is None:
        raise HotkeyError('Another hotkey command is processing; wait for it to finish.')
    try:
        session_fd = acquire(state / 'session.lock')
        status = read_json(state / 'status.json')
        if session_fd is None:
            if action == 'status':
                return status.get('phase', 'starting')
            if status.get('phase') != 'recording':
                raise HotkeyError('Transcription is already processing; wait for it to finish.')
            return stop_active_session(config, state, status)
        try:
            clean_stale_sessions(state)
            if action in ('status', 'stop'):
                return 'idle' if action == 'status' else 'No recording in progress.'
            return start_worker(config, state, session_fd)
        finally:
            os.close(session_fd)
    finally:
        os.close(command_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action')
    for action in ('toggle', 'stop', 'status', 'doctor'):
        sub.add_parser(action)
    trans = sub.add_parser('transcribe', help='Transcribe an existing 16 kHz mono PCM WAV')
    trans.add_argument('audio', type=Path)
    trans.add_argument('--no-copy', action='store_true')
    record = sub.add_parser('_record', help=argparse.SUPPRESS)
    record.add_argument('session', type=Path)
    record.add_argument('lock_fd', type=int)
    args = parser.parse_args(argv)
    try:
        if platform.system() not in ('Linux', 'Darwin'):
            raise HotkeyError('Supported platforms are Linux and macOS. Native Windows is not supported yet.')
        os.umask(0o077)
        config = load_config()
        state = state_home()
        action = args.action or 'toggle'
        if action == '_record':
            return worker(config, state, args.session, args.lock_fd)
        if action == 'doctor':
            check_whisper(config)
            result = subprocess.run([config['whisper_bin'], '--help'], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode:
                raise HotkeyError('Whisper --help failed; reinstall or correct whisper_bin.')
            print('Whisper:', config['whisper_bin'])
            print('Model:', config['model_path'])
            print('Recorder:', recording_command(config, state / 'check.wav')[0])
            print('Clipboard:', clipboard_command(config)[0])
            print('State:', state)
            print('Dependencies found. Microphone permissions and hotkeys need a desktop check.')
        elif action == 'transcribe':
            command_fd = acquire(state / 'command.lock')
            session_fd = acquire(state / 'session.lock')
            try:
                if command_fd is None or session_fd is None:
                    raise HotkeyError('A recording or transcription is already in progress.')
                clean_stale_sessions(state)
                print(transcribe(config, args.audio.expanduser().absolute(), state, copy=not args.no_copy))
            finally:
                for fd in (command_fd, session_fd):
                    if fd is not None:
                        os.close(fd)
        else:
            print(control(config, state, action))
        return 0
    except (HotkeyError, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
