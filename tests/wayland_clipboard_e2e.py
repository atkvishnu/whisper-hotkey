#!/usr/bin/env python3
"""Real Whisper -> Wayland clipboard integration in a headless Sway session."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time


def main():
    prefix = Path(sys.argv[1]).resolve()
    launcher = prefix / 'whisper-toggle.sh'
    with tempfile.TemporaryDirectory(prefix='whisper-wayland-e2e-') as directory:
        root = Path(directory)
        runtime = root / 'runtime'
        runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_SESSION_TYPE='wayland',
                   WLR_BACKENDS='headless', WLR_LIBINPUT_NO_DEVICES='1', WLR_RENDERER='pixman',
                   WHISPER_HOTKEY_CONFIG=str(root / 'config.json'),
                   WHISPER_HOTKEY_STATE_DIR=str(root / 'state'))
        env.pop('DISPLAY', None)
        env.pop('WAYLAND_DISPLAY', None)
        config = {'whisper_bin': str(prefix / 'whisper.cpp/build/bin/whisper-cli'),
                  'model_path': str(prefix / 'whisper.cpp/models/ggml-tiny.en.bin'),
                  'clipboard': 'auto', 'notifications': False}
        (root / 'config.json').write_text(json.dumps(config))
        (root / 'sway.conf').write_text('output HEADLESS-1 mode 1280x720\nseat seat0 fallback true\n')
        with (root / 'sway.log').open('w') as log:
            compositor = subprocess.Popen(['sway', '--unsupported-gpu', '-c', str(root / 'sway.conf')],
                                          env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    sockets = [path for path in runtime.glob('wayland-*')
                               if stat.S_ISSOCK(path.stat().st_mode)]
                    if sockets:
                        env['WAYLAND_DISPLAY'] = sockets[0].name
                        break
                    if compositor.poll() is not None:
                        raise RuntimeError((root / 'sway.log').read_text())
                    time.sleep(0.1)
                else:
                    raise RuntimeError('Sway did not create a Wayland socket within 10 seconds.')
                started = time.monotonic()
                result = subprocess.run([str(launcher), 'transcribe', str(prefix / 'whisper.cpp/samples/jfk.wav')],
                                        env=env, capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise RuntimeError(result.stderr)
                clipboard = subprocess.run(['wl-paste', '--no-newline'], env=env, capture_output=True,
                                           text=True, check=True, timeout=10).stdout
                assert clipboard == result.stdout.strip(), (clipboard, result.stdout)
                assert 'country' in clipboard.lower() and 'ask' in clipboard.lower(), repr(clipboard)
                assert not list((root / 'state').glob('transcribe-*'))
                print(json.dumps({'result': 'passed', 'elapsed_seconds': round(time.monotonic() - started, 3),
                                  'clipboard': 'real wl-copy/wl-paste on headless Sway',
                                  'inference': 'real whisper.cpp tiny.en', 'clipboard_text': clipboard}), flush=True)
            finally:
                compositor.terminate()
                try:
                    compositor.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    compositor.kill()
                    compositor.wait(timeout=5)


if __name__ == '__main__':
    main()
