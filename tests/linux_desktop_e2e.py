#!/usr/bin/env python3
"""Real Linux audio-server -> Whisper -> X11 clipboard integration, without a mic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, capture_output=True, timeout=120, **kwargs)


def main():
    prefix = Path(sys.argv[1]).resolve()
    launcher = prefix / 'whisper-toggle.sh'
    sample = prefix / 'whisper.cpp/samples/jfk.wav'
    with tempfile.TemporaryDirectory(prefix='whisper-desktop-e2e-') as directory:
        root = Path(directory)
        env = dict(os.environ, DISPLAY=':97', WHISPER_HOTKEY_STATE_DIR=str(root / 'state'),
                   WHISPER_HOTKEY_CONFIG=str(root / 'config.json'))
        env.pop('WAYLAND_DISPLAY', None)
        env['XDG_SESSION_TYPE'] = 'x11'
        config = json.loads((prefix / 'config.json').read_text())
        config.update(backend='parecord', device='whisper_test.monitor', clipboard='xclip', notifications=False)
        (root / 'config.json').write_text(json.dumps(config))
        display = subprocess.Popen(['Xvfb', ':97', '-screen', '0', '640x480x24'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            run(['pulseaudio', '--start', '--exit-idle-time=-1'], env=env)
            run(['pactl', 'load-module', 'module-null-sink', 'sink_name=whisper_test'], env=env)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                probe = subprocess.run(['xclip', '-selection', 'clipboard'], input='', text=True, env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                if probe.returncode == 0:
                    break
                time.sleep(0.1)
            run([str(launcher), 'doctor'], env=env)
            started = time.monotonic()
            print(run([str(launcher)], env=env).stdout.strip(), flush=True)
            run(['paplay', '--device=whisper_test', str(sample)], env=env)
            time.sleep(0.3)
            print(run([str(launcher), 'stop'], env=env).stdout.strip(), flush=True)
            text = run(['xclip', '-selection', 'clipboard', '-o'], env=env).stdout
            assert 'country' in text.lower() and 'ask' in text.lower(), repr(text)
            assert not list((root / 'state').rglob('*.wav'))
            assert run([str(launcher), 'status'], env=env).stdout.strip() == 'idle'
            print(json.dumps({'result': 'passed', 'elapsed_seconds': round(time.monotonic() - started, 3),
                              'clipboard_text': text, 'audio': 'PulseAudio null-sink monitor',
                              'inference': 'real whisper.cpp tiny.en', 'clipboard': 'real xclip + Xvfb'}), flush=True)
        finally:
            subprocess.run([str(launcher), 'stop'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            subprocess.run(['pulseaudio', '--kill'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            display.terminate()
            display.wait(timeout=10)


if __name__ == '__main__':
    main()
