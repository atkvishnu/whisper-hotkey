import importlib.util
import json
import os
import signal
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

RUNTIME = Path(__file__).resolve().parents[1] / 'scripts/whisper_hotkey.py'
spec = importlib.util.spec_from_file_location('whisper_hotkey', RUNTIME)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

FAKE_TOOL = r'''
import os, signal, sys, time, wave
from pathlib import Path
name = Path(sys.argv[0]).name
if name in ('arecord', 'parecord', 'pw-record', 'rec'):
    if os.environ.get('RECORDER_PID_FILE'):
        Path(os.environ['RECORDER_PID_FILE']).write_text(f'{os.getpid()} {os.getppid()}')
    time.sleep(float(os.environ.get('RECORDER_START_DELAY', '0')))
    with open(os.environ['EVENTS'], 'a') as out:
        out.write('record\n')
    if os.environ.get('RECORDER_FAIL'):
        sys.exit(2)
    def stop(signum, frame):
        time.sleep(float(os.environ.get('FLUSH_DELAY', '0')))
        with wave.open(sys.argv[-1], 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(b'\0\0' * (10 if os.environ.get('SHORT_AUDIO') else 8000))
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if not os.environ.get('RECORDER_NO_OUTPUT'):
        Path(sys.argv[-1]).touch()
    while True:
        time.sleep(0.02)
elif name == 'whisper-cli':
    if '--help' in sys.argv:
        sys.exit(0)
    time.sleep(float(os.environ.get('INFERENCE_DELAY', '0')))
    print('diagnostic on stdout')
    print('diagnostic on stderr', file=sys.stderr)
    if os.environ.get('WHISPER_FAIL'):
        print('error: failed to open model', file=sys.stderr)
        sys.exit(7)
    if not os.environ.get('NO_OUTPUT'):
        text = '' if os.environ.get('EMPTY_OUTPUT') else 'Hello café\nनमस्ते world'
        Path(sys.argv[sys.argv.index('-of') + 1] + '.txt').write_text(text, encoding='utf-8')
else:
    if os.environ.get('CLIPBOARD_FAIL'):
        sys.exit(9)
    Path(os.environ['CLIPBOARD_FILE']).write_bytes(sys.stdin.buffer.read())
'''


def make_wav(path, frames=8000, rate=16000):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b'\0\0' * frames)


class BackendTests(unittest.TestCase):
    def test_relative_xdg_paths_use_home_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {'HOME': directory, 'XDG_DATA_HOME': 'relative-data',
                                         'XDG_STATE_HOME': 'relative-state'}, clear=True), patch.object(app.platform, 'system', return_value='Linux'):
                self.assertEqual(app.data_home(), home / '.local/share/whisper-hotkey')
                self.assertEqual(app.state_home(), home / '.local/state/whisper-hotkey')
            self.assertFalse((home / 'relative-state').exists())

    def test_absolute_xdg_paths_are_used(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {'HOME': directory, 'XDG_DATA_HOME': str(home / 'data'),
                                         'XDG_STATE_HOME': str(home / 'state')}, clear=True), patch.object(app.platform, 'system', return_value='Linux'):
                self.assertEqual(app.data_home(), home / 'data/whisper-hotkey')
                self.assertEqual(app.state_home(), home / 'state/whisper-hotkey')

    def test_wayland_never_chooses_xwayland_clipboard(self):
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':1'}, clear=True), patch.object(app, 'command_exists', return_value=True), patch.object(app.platform, 'system', return_value='Linux'):
            self.assertEqual(app.clipboard_command({'clipboard': 'auto'})[0], 'wl-copy')

    def test_missing_wayland_tool_does_not_fall_back_to_xclip(self):
        with patch.dict(os.environ, {'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':1'}, clear=True), patch.object(app, 'command_exists', side_effect=lambda x: x == 'xclip'), patch.object(app.platform, 'system', return_value='Linux'):
            with self.assertRaisesRegex(app.HotkeyError, 'wl-copy'):
                app.clipboard_command({'clipboard': 'auto'})

    def test_x11_xclip_and_xsel(self):
        with patch.dict(os.environ, {'DISPLAY': ':0'}, clear=True), patch.object(app.platform, 'system', return_value='Linux'):
            for tool in ('xclip', 'xsel'):
                with self.subTest(tool=tool), patch.object(app, 'command_exists', side_effect=lambda x: x == tool):
                    self.assertEqual(app.clipboard_command({'clipboard': 'auto'})[0], tool)

    def test_macos_backend_selection(self):
        with patch.object(app.platform, 'system', return_value='Darwin'), patch.object(app, 'command_exists', return_value=True):
            self.assertEqual(app.recording_command({'backend': 'auto'}, Path('/a b.wav'))[-1], '/a b.wav')
            self.assertEqual(app.recording_command({'backend': 'auto'}, Path('/a b.wav'))[0], 'rec')
            self.assertEqual(app.clipboard_command({'clipboard': 'auto'}), ['pbcopy'])

    def test_linux_audio_fallback(self):
        with patch.object(app.platform, 'system', return_value='Linux'):
            for tool in ('pw-record', 'parecord', 'arecord'):
                with self.subTest(tool=tool), patch.object(app, 'command_exists', side_effect=lambda x: x == tool):
                    self.assertEqual(app.recording_command({'backend': 'auto'}, Path('test.wav'))[0], tool)

    def test_device_is_single_argument(self):
        with patch.object(app, 'command_exists', return_value=True):
            for backend in ('pw-record', 'parecord', 'arecord'):
                command = app.recording_command({'backend': backend, 'device': 'input with spaces; $(false)'}, Path('file.wav'))
                self.assertIn('input with spaces; $(false)', command)

    def test_no_desktop_is_actionable(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(app.platform, 'system', return_value='Linux'):
            with self.assertRaisesRegex(app.HotkeyError, 'No desktop'):
                app.clipboard_command({'clipboard': 'auto'})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='whisper test ')
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('arecord', 'whisper-cli', 'xclip'):
            path = self.bin / name
            path.write_text(f'#!{sys.executable}\n' + FAKE_TOOL)
            path.chmod(0o755)
        self.model = self.root / 'model with spaces.bin'
        self.model.write_bytes(b'model')
        self.audio = self.root / 'speech with spaces.wav'
        make_wav(self.audio)
        self.config_path = self.root / 'settings.json'
        self.config = {'whisper_bin': str(self.bin / 'whisper-cli'), 'model_path': str(self.model),
                       'backend': 'arecord', 'clipboard': 'xclip', 'notifications': False,
                       'max_seconds': 5, 'transcribe_timeout': 5}
        self.config_path.write_text(json.dumps(self.config))
        self.state = self.root / 'state'
        self.clip = self.root / 'clipboard'
        self.events = self.root / 'events'
        self.env = dict(os.environ, HOME=str(self.root), PATH=str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                        WHISPER_HOTKEY_CONFIG=str(self.config_path), WHISPER_HOTKEY_STATE_DIR=str(self.state),
                        CLIPBOARD_FILE=str(self.clip), EVENTS=str(self.events))
        self.env.pop('WHISPER_BIN', None)
        self.env.pop('MODEL_PATH', None)

    def tearDown(self):
        self.run_cli('stop')
        self.temporary.cleanup()

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(RUNTIME), *map(str, args)], cwd='/', env=env or self.env,
                              capture_output=True, text=True, timeout=20)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def assert_failure(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertNotIn('copied to clipboard', result.stdout)

    def test_full_toggle_waits_for_flush_and_preserves_unicode(self):
        self.env['FLUSH_DELAY'] = '0.7'
        self.assert_success(self.run_cli())
        self.assertEqual(self.run_cli('status').stdout.strip(), 'recording')
        self.assert_success(self.run_cli())
        self.assertEqual(self.clip.read_text(), 'Hello café\nनमस्ते world')
        self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')
        self.assertEqual(list(self.state.glob('session-*')), [])
        self.assertEqual(list(self.state.rglob('*.wav')), [])

    def test_existing_audio_stdout_excludes_diagnostics(self):
        result = self.run_cli('transcribe', self.audio, '--no-copy')
        self.assert_success(result)
        self.assertEqual(result.stdout.strip(), 'Hello café\nनमस्ते world')
        self.assertFalse(self.clip.exists())

    def test_whisper_stdout_is_discarded_before_log_capture(self):
        self.state.mkdir()
        def inference(command, **kwargs):
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            self.assertNotEqual(kwargs['stderr'], subprocess.DEVNULL)
            Path(command[command.index('-of') + 1] + '.txt').write_text('dictated text')
            return subprocess.CompletedProcess(command, 0)
        config = dict(self.config, language='auto', threads=4)
        with patch.object(app.subprocess, 'run', side_effect=inference):
            self.assertEqual(app.transcribe(config, self.audio, self.state, copy=False), 'dictated text')

    def test_whisper_error_is_not_copied(self):
        self.env['WHISPER_FAIL'] = '1'
        self.assert_failure(self.run_cli('transcribe', self.audio), 'Whisper failed (exit 7)')
        self.assertFalse(self.clip.exists())
        self.assertFalse((self.state / 'last-transcript.txt').exists())

    def test_missing_and_empty_transcripts_fail(self):
        for env_key, message in [('NO_OUTPUT', 'no transcript file'), ('EMPTY_OUTPUT', 'empty transcript')]:
            with self.subTest(env_key=env_key):
                result = self.run_cli('transcribe', self.audio, env=dict(self.env, **{env_key: '1'}))
                self.assert_failure(result, message)
                self.assertFalse(self.clip.exists())

    def test_clipboard_failure_preserves_recovery_transcript(self):
        self.env['CLIPBOARD_FAIL'] = '1'
        self.assert_failure(self.run_cli('transcribe', self.audio), 'Clipboard failed (exit 9)')
        self.assertEqual((self.state / 'last-transcript.txt').read_text().strip(), 'Hello café\nनमस्ते world')

    def test_missing_binary_and_model_fail_before_recording(self):
        for key in ('whisper_bin', 'model_path'):
            with self.subTest(key=key):
                self.config_path.write_text(json.dumps(dict(self.config, **{key: '/missing path'})))
                self.assert_failure(self.run_cli(), 'Whisper')
                self.assertFalse(self.events.exists())

    def test_recorder_immediate_failure_is_reported(self):
        self.env['RECORDER_FAIL'] = '1'
        self.assert_failure(self.run_cli(), 'Recorder exited before')
        self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')

    def test_slow_recorder_failure_is_reported_before_start_success(self):
        self.env.update(RECORDER_FAIL='1', RECORDER_START_DELAY='0.65')
        self.assert_failure(self.run_cli(), 'Recorder exited before')
        self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')
        self.assertEqual(list(self.state.glob('session-*')), [])

    def test_start_waits_for_recorder_output_creation(self):
        self.env['RECORDER_START_DELAY'] = '0.65'
        self.assert_success(self.run_cli())
        status = json.loads((self.state / 'status.json').read_text())
        audio = self.state / status['session'] / 'recording.wav'
        self.assertTrue(audio.is_file(), 'Recording reported ready before the backend opened its output')
        self.assertEqual(audio.stat().st_size, 0)
        self.assert_success(self.run_cli('stop'))

    def test_recorder_that_never_opens_output_times_out(self):
        self.env['RECORDER_NO_OUTPUT'] = '1'
        self.assert_failure(self.run_cli(), 'Recorder startup timed out')
        self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')
        self.assertEqual(list(self.state.glob('session-*')), [])

    def test_short_recording_fails(self):
        self.env['SHORT_AUDIO'] = '1'
        self.assert_success(self.run_cli())
        self.assert_failure(self.run_cli('stop'), 'too short')
        self.assertFalse(self.clip.exists())

    def test_wrong_audio_format_fails(self):
        make_wav(self.audio, rate=44100)
        self.assert_failure(self.run_cli('transcribe', self.audio), '16 kHz')

    def test_invalid_audio_fails(self):
        self.audio.write_text('not a wav')
        self.assert_failure(self.run_cli('transcribe', self.audio), 'Invalid WAV')

    def test_transcription_timeout_fails(self):
        self.config_path.write_text(json.dumps(dict(self.config, transcribe_timeout=1)))
        self.env['INFERENCE_DELAY'] = '2'
        self.assert_failure(self.run_cli('transcribe', self.audio), 'timed out')
        self.assertFalse(self.clip.exists())

    def test_private_storage(self):
        self.assert_success(self.run_cli('transcribe', self.audio, '--no-copy'))
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        for path in self.state.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))

    def test_symlink_state_and_lock_are_rejected(self):
        target = self.root / 'target'
        target.mkdir()
        self.state.symlink_to(target, target_is_directory=True)
        self.assert_failure(self.run_cli('status'), 'non-symlink')
        self.state.unlink()
        self.state.mkdir()
        external = self.root / 'external'
        external.write_text('untouched')
        (self.state / 'command.lock').symlink_to(external)
        self.assertNotEqual(self.run_cli('status').returncode, 0)
        self.assertEqual(external.read_text(), 'untouched')
        (self.state / 'command.lock').unlink()

    def test_stale_state_does_not_signal_unrelated_process(self):
        self.state.mkdir()
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])
        try:
            (self.state / 'status.json').write_text(json.dumps({'phase': 'recording', 'pid': unrelated.pid, 'session': 'session-old'}))
            old = self.state / 'session-old'
            old.mkdir()
            (old / 'recording.wav').write_bytes(b'old')
            self.assert_success(self.run_cli())
            self.assertIsNone(unrelated.poll())
            self.assertFalse(old.exists())
            self.assert_success(self.run_cli('stop'))
        finally:
            unrelated.terminate()
            unrelated.wait()

    def make_stale_inference(self):
        self.state.mkdir(exist_ok=True)
        inference = self.state / 'transcribe-stale'
        inference.mkdir()
        for name in ('whisper.log', 'result.txt'):
            (inference / name).write_text('private dictated text')
        for name in ('.transcript-stale', '.state-stale'):
            (self.state / name).write_text('private temporary contents')
        return inference

    def assert_stale_inference_removed(self, inference):
        self.assertFalse(inference.exists())
        self.assertFalse((self.state / '.transcript-stale').exists())
        self.assertFalse((self.state / '.state-stale').exists())

    def test_idle_control_removes_stale_inference_and_private_temporaries(self):
        inference = self.make_stale_inference()
        last = self.state / 'last-transcript.txt'
        last.write_text('saved recovery transcript')
        self.assert_success(self.run_cli('status'))
        self.assert_stale_inference_removed(inference)
        self.assertEqual(last.read_text(), 'saved recovery transcript')

    def test_standalone_transcribe_cleans_stale_files_before_inference(self):
        inference = self.make_stale_inference()
        self.audio.write_text('invalid audio')
        self.assert_failure(self.run_cli('transcribe', self.audio, '--no-copy'), 'Invalid WAV')
        self.assert_stale_inference_removed(inference)

    def test_recovery_preserves_unrelated_paths_and_symlink_targets(self):
        inference = self.make_stale_inference()
        external = self.root / 'external'
        external.mkdir()
        target = external / 'whisper.log'
        target.write_text('external file')
        for name in ('transcribe-link', 'session-link'):
            (self.state / name).symlink_to(external, target_is_directory=True)
        link = self.state / '.transcript-link'
        link.symlink_to(target)
        unrelated = inference / 'keep.txt'
        unrelated.write_text('unrelated file')
        nested = inference / 'nested'
        nested.mkdir()
        (nested / 'keep.txt').write_text('nested file')
        temporary_directory = self.state / '.state-unrelated-directory'
        temporary_directory.mkdir()
        (temporary_directory / 'keep.txt').write_text('keep directory')
        self.assert_success(self.run_cli('status'))
        self.assertFalse((inference / 'whisper.log').exists())
        self.assertFalse((inference / 'result.txt').exists())
        self.assertEqual(unrelated.read_text(), 'unrelated file')
        self.assertEqual((nested / 'keep.txt').read_text(), 'nested file')
        self.assertEqual((temporary_directory / 'keep.txt').read_text(), 'keep directory')
        self.assertEqual(target.read_text(), 'external file')
        self.assertTrue(link.is_symlink())
        for name in ('transcribe-link', 'session-link'):
            self.assertTrue((self.state / name).is_symlink())

    def test_recovery_does_not_clean_while_lifecycle_lock_is_held(self):
        inference = self.make_stale_inference()
        (self.state / 'status.json').write_text(json.dumps({'phase': 'processing'}))
        lock_fd = app.acquire(self.state / 'session.lock')
        self.assertIsNotNone(lock_fd)
        try:
            self.assertEqual(self.run_cli('status').stdout.strip(), 'processing')
            self.assert_failure(self.run_cli('transcribe', self.audio, '--no-copy'), 'already in progress')
            self.assertEqual((inference / 'result.txt').read_text(), 'private dictated text')
            self.assertTrue((self.state / '.transcript-stale').exists())
            self.assertTrue((self.state / '.state-stale').exists())
        finally:
            os.close(lock_fd)

    def test_third_keypress_while_processing_is_rejected(self):
        self.env['INFERENCE_DELAY'] = '1'
        self.assert_success(self.run_cli())
        stop = subprocess.Popen([sys.executable, str(RUNTIME), 'stop'], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if json.loads((self.state / 'status.json').read_text()).get('phase') == 'processing':
                    break
                time.sleep(0.02)
            self.assert_failure(self.run_cli(), 'processing')
            stdout, stderr = stop.communicate(timeout=10)
            self.assertEqual(stop.returncode, 0, stderr + stdout)
            self.assertEqual(self.events.read_text().splitlines(), ['record'])
        finally:
            if stop.poll() is None:
                stop.terminate()
                stop.wait()

    def test_simultaneous_commands_do_not_start_two_recorders(self):
        self.assert_concurrent_commands_start_one_recorder()

    def test_simultaneous_commands_tolerate_slow_recorder_startup(self):
        self.env['RECORDER_START_DELAY'] = '0.65'
        self.assert_concurrent_commands_start_one_recorder()

    def assert_concurrent_commands_start_one_recorder(self):
        processes = [subprocess.Popen([sys.executable, str(RUNTIME)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            results = [p.communicate(timeout=10) for p in processes]
            diagnostics = [(p.returncode, stdout, stderr) for p, (stdout, stderr) in zip(processes, results)]
            self.assertTrue(any(p.returncode == 0 for p in processes), diagnostics)
            # Bound the fixture observation wait and retain both command
            # results if startup fails to produce exactly one recorder event.
            deadline = time.monotonic() + 5
            events = []
            while time.monotonic() < deadline:
                if self.events.exists():
                    events = self.events.read_text().splitlines()
                    if events:
                        break
                time.sleep(0.02)
            self.assertEqual(events, ['record'], diagnostics)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait()

    def test_maximum_recording_duration_discards_audio(self):
        self.config_path.write_text(json.dumps(dict(self.config, max_seconds=1)))
        self.assert_success(self.run_cli())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if json.loads((self.state / 'status.json').read_text()).get('phase') == 'idle':
                break
            time.sleep(0.1)
        self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')
        self.assertIn('time limit', json.loads((self.state / 'result.json').read_text())['message'])
        self.assertFalse(self.clip.exists())
        self.assertEqual(list(self.state.rglob('*.wav')), [])

    def test_internal_worker_rejects_unrelated_directory(self):
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        victim = unrelated / 'keep.txt'
        victim.write_text('must remain')
        result = self.run_cli('_record', unrelated, '0')
        self.assert_failure(result, 'Invalid internal recording session')
        self.assertEqual(victim.read_text(), 'must remain')

    def test_internal_worker_rejects_forged_lock(self):
        self.state.mkdir()
        session = self.state / 'session-forged'
        session.mkdir()
        victim = session / 'recording.wav'
        victim.write_text('must remain')
        (self.state / 'status.json').write_text(json.dumps({'phase': 'starting', 'session': session.name}))
        (self.state / 'session.lock').touch()
        with open(os.devnull) as wrong_lock:
            result = subprocess.run([sys.executable, str(RUNTIME), '_record', str(session), str(wrong_lock.fileno())],
                                    pass_fds=(wrong_lock.fileno(),), env=self.env, capture_output=True, text=True)
        self.assert_failure(result, 'Invalid inherited recording lock')
        self.assertEqual(victim.read_text(), 'must remain')

    def test_internal_worker_rejects_symlink_session(self):
        self.state.mkdir()
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        victim = unrelated / 'keep.txt'
        victim.write_text('must remain')
        session = self.state / 'session-linked'
        session.symlink_to(unrelated, target_is_directory=True)
        (self.state / 'status.json').write_text(json.dumps({'phase': 'starting', 'session': session.name}))
        result = self.run_cli('_record', session, '0')
        self.assert_failure(result, 'Invalid internal recording session')
        self.assertEqual(victim.read_text(), 'must remain')
        self.assertFalse((unrelated / 'recorder.log').exists())

    def test_internal_worker_rejects_descriptor_that_does_not_own_lock(self):
        self.state.mkdir()
        session = self.state / 'session-wrong-descriptor'
        session.mkdir()
        victim = session / 'recording.wav'
        victim.write_text('must remain')
        (self.state / 'status.json').write_text(json.dumps({'phase': 'starting', 'session': session.name}))
        self.config_path.write_text(json.dumps(dict(self.config, backend='invalid')))
        lock_fd = app.acquire(self.state / 'session.lock')
        self.assertIsNotNone(lock_fd)
        try:
            with (self.state / 'session.lock').open() as unheld:
                result = subprocess.run([sys.executable, str(RUNTIME), '_record', str(session), str(unheld.fileno())],
                                        pass_fds=(unheld.fileno(),), env=self.env, capture_output=True, text=True, timeout=10)
            self.assert_failure(result, 'Inherited recording descriptor does not own the lock')
            self.assertEqual(victim.read_text(), 'must remain')
        finally:
            os.close(lock_fd)

    def test_worker_cleanup_preserves_unexpected_session_files(self):
        self.assert_success(self.run_cli())
        status = json.loads((self.state / 'status.json').read_text())
        session = self.state / status['session']
        unexpected = session / 'keep.txt'
        unexpected.write_text('must remain')
        self.assert_success(self.run_cli('stop'))
        self.assertEqual(unexpected.read_text(), 'must remain')
        self.assertEqual(list(session.iterdir()), [unexpected])
        self.assert_success(self.run_cli('status'))
        self.assertEqual(unexpected.read_text(), 'must remain')

    def test_worker_death_stops_owned_recorder_and_releases_lock(self):
        pid_file = self.root / 'recorder-processes'
        self.env['RECORDER_PID_FILE'] = str(pid_file)
        self.assert_success(self.run_cli())
        recorder_pid, guard_pid = map(int, pid_file.read_text().split())
        if Path('/proc').is_dir():
            worker_pid = int(Path(f'/proc/{guard_pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
            command = Path(f'/proc/{worker_pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
        else:
            worker_pid = int(subprocess.check_output(['ps', '-p', str(guard_pid), '-o', 'ppid='], text=True).strip())
            command = subprocess.check_output(['ps', '-p', str(worker_pid), '-o', 'args='], text=True)
        self.assertIn(str(self.state), command)
        self.assertIn('_record', command)
        os.kill(worker_pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(recorder_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail('Recorder survived recording worker death')
        self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')
        self.assertEqual(list(self.state.glob('session-*')), [])
        self.assert_success(self.run_cli())
        self.assert_success(self.run_cli('stop'))

    def test_worker_exit_during_startup_stops_recorder(self):
        pid_file = self.root / 'startup-recorder-processes'
        self.env.update(RECORDER_PID_FILE=str(pid_file), RECORDER_START_DELAY='3')
        for signum in (signal.SIGKILL, signal.SIGTERM):
            with self.subTest(signum=signum):
                pid_file.unlink(missing_ok=True)
                start = subprocess.Popen([sys.executable, str(RUNTIME)], env=self.env,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not pid_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(pid_file.exists(), 'Recorder did not launch during startup')
                    recorder_pid, guard_pid = map(int, pid_file.read_text().split())
                    if Path('/proc').is_dir():
                        worker_pid = int(Path(f'/proc/{guard_pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
                        command = Path(f'/proc/{worker_pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
                    else:
                        worker_pid = int(subprocess.check_output(['ps', '-p', str(guard_pid), '-o', 'ppid='], text=True).strip())
                        command = subprocess.check_output(['ps', '-p', str(worker_pid), '-o', 'args='], text=True)
                    self.assertIn(str(self.state), command)
                    self.assertIn('_record', command)
                    self.assertEqual(json.loads((self.state / 'status.json').read_text())['phase'], 'starting')
                    os.kill(worker_pid, signum)
                    stdout, stderr = start.communicate(timeout=10)
                    self.assertNotEqual(start.returncode, 0, stdout + stderr)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        try:
                            os.kill(recorder_pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail('Recorder survived worker exit during startup')
                    self.assertEqual(self.run_cli('status').stdout.strip(), 'idle')
                    self.assertEqual(list(self.state.glob('session-*')), [])
                finally:
                    if start.poll() is None:
                        start.terminate()
                        start.wait(timeout=10)

    def test_config_validation(self):
        for bad in ([], {'threads': 0}, {'max_seconds': -1}, {'transcribe_timeout': True}):
            with self.subTest(bad=bad):
                self.config_path.write_text(json.dumps(bad))
                self.assert_failure(self.run_cli('status'), 'Configuration' if bad == [] else 'positive integer')

    def test_doctor_checks_tools_without_recording(self):
        self.assert_success(self.run_cli('doctor'))
        self.assertFalse(self.events.exists())
        self.assertFalse(self.clip.exists())


if __name__ == '__main__':
    unittest.main()
