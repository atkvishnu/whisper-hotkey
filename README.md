# Whisper Hotkey

Record speech with a desktop shortcut, press it again to stop, and paste the transcription. Speech recognition runs locally with [whisper.cpp](https://github.com/ggml-org/whisper.cpp).

## Requirements

Install Python 3.9+ and Git before running the installer. On macOS, the default installation uses Homebrew; install Xcode Command Line Tools as well.

| Platform | Dependency installation | Recording | Clipboard |
| --- | --- | --- | --- |
| Linux | apt-get, dnf, pacman, or zypper | PipeWire, PulseAudio, or ALSA | `wl-copy` on Wayland; `xclip` or `xsel` on X11 |
| macOS | Homebrew | SoX `rec` | `pbcopy` |

You can also provide dependencies manually with `--skip-deps`. This skips package installation, not dependency checks or engine/model downloads. Native Windows is not supported.

## Installation

```sh
git clone https://github.com/atkvishnu/whisper-hotkey-linux.git
cd whisper-hotkey-linux
./install.sh --dry-run
./install.sh
```

The installer installs dependencies, builds a pinned whisper.cpp release with CMake, verifies the downloaded model, and prints your launcher command. Internet access is needed for installation and model downloads; transcription runs offline.

The default model is multilingual `base`. Use `--model tiny.en` for the smaller English-only model. Transcription speed and accuracy depend on the model, hardware, and audio.

```sh
./install.sh --help
./install.sh --model tiny.en
./install.sh --prefix "$HOME/Apps/Whisper Hotkey"
./install.sh --skip-deps
```

Default installation locations:

- Linux: `$XDG_DATA_HOME/whisper-hotkey`, or `~/.local/share/whisper-hotkey` when unset.
- macOS: `~/Library/Application Support/whisper-hotkey`.

Reinstalling preserves your configuration choices and updates the selected model path. An existing whisper.cpp checkout with a different revision or local changes is left unchanged; choose a separate `--prefix` for a new installation.

## Set up a shortcut

1. Run the launcher command printed by the installer with `doctor` appended. This checks dependencies without recording.
2. Run the launcher once in a terminal to start recording, then again to stop. Verify that you can paste the result.
3. Assign the printed launcher command to an available key in your desktop's shortcut settings. On macOS, use a Shortcut with a **Run Shell Script** action and [assign it a keyboard shortcut](https://support.apple.com/guide/shortcuts-mac/apd163eb9f95/mac).

The installer does not change existing shortcuts. F9 is one option; choose another key if your desktop already uses it. Test the shortcut itself and grant microphone access to the application that launches it when requested.

## Usage

Press the shortcut to start recording. Press it again to stop, wait for transcription to finish, then paste. Notifications are optional; run the launcher in a terminal to see error details.

The launcher accepts `toggle` (the default), `stop`, `status`, and `doctor`. To transcribe an existing file using your default installation:

```sh
./scripts/whisper-toggle.sh transcribe /path/to/speech.wav --no-copy
```

Input must be 16 kHz, mono, 16-bit PCM WAV, at least 0.1 seconds long. `--no-copy` prints the transcript without changing the clipboard; it still saves the recovery transcript described below. For a custom installation directory, use its installed launcher instead.

## Configuration

Edit `config.json` in the installation directory.

| Setting | Default | Purpose |
| --- | --- | --- |
| `backend` | `auto` | Recorder: `pw-record`, `parecord`, `arecord`, or `sox` |
| `clipboard` | `auto` | Clipboard tool: `wl-copy`, `xclip`, `xsel`, or `pbcopy` |
| `language` | `auto` | Detect the language, or use a code such as `en` or `fr` |
| `threads` | `4` | CPU threads for transcription |
| `max_seconds` | `300` | Recording limit; reaching it stops capture and discards the audio |
| `transcribe_timeout` | `300` | Maximum seconds allowed for transcription |
| `notifications` | `true` | Show recording and transcription status |

On Linux, `auto` selects the first installed recorder in this order: `pw-record`, `parecord`, `arecord`. If that recorder cannot connect to your audio server, select a working backend explicitly. The optional `device` setting selects an ALSA device, PulseAudio source, or PipeWire target; for SoX, select the default input in macOS Sound settings.

## Storage and privacy

Recordings and temporary inference files are removed after a session, including handled failures. An abrupt shutdown or forced process kill can leave temporary files; the next idle control command or standalone transcription cleans them up.

One recovery file, `last-transcript.txt`, remains until the next successful transcription replaces it or you delete it. State and this transcript are stored in:

- Linux: `$XDG_STATE_HOME/whisper-hotkey`, or `~/.local/state/whisper-hotkey` when unset.
- macOS: `~/Library/Caches/whisper-hotkey`.

Relative XDG paths are ignored in favor of the defaults. State directories use mode `0700`; newly written state and transcripts use `0600`. Notifications contain status only. Your clipboard and clipboard-history applications manage their own retention.

## Troubleshooting and feedback

- **Shortcut does nothing:** run the printed launcher in a terminal, check for a key conflict, and check microphone permissions in the app that launches it.
- **No audio:** check the default microphone and select a working recording backend. Run `doctor` to check tool availability.
- **Clipboard fails:** if copying fails after transcription, the error includes the path to `last-transcript.txt`. Wayland needs `wl-copy`; X11 needs `xclip` or `xsel`. A headless shell can use `transcribe --no-copy`.
- **Whisper fails:** run `doctor` and check the executable/model paths. Failed transcription is not copied to the clipboard.

[Report a bug or suggest an improvement](https://github.com/atkvishnu/whisper-hotkey-linux/issues). Include your OS/distro, desktop environment, X11 or Wayland session type where applicable, command, reproduction steps, expected result, and error output. Remove private text from logs before posting. Keep feedback specific and respectful.

## Development

```sh
make check
docker build -f tests/Dockerfile.integration -t whisper-hotkey-integration .
docker run --rm --pull=never whisper-hotkey-integration
```

The controller uses Python's standard library. `make check` runs offline tests, shell syntax checks, and Python compilation. CI runs these checks on Linux and macOS. The Linux integration container builds Whisper and exercises virtual PulseAudio recording, transcription, and real X11 and Wayland clipboard tools without mounting the host microphone or clipboard.

These checks do not certify physical microphones, desktop hotkeys, or macOS permission behavior. Test those in the desktop session where you intend to use the tool.

## License

[MIT](LICENSE). See [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and [Whisper](https://github.com/openai/whisper) for the engine and model licenses.
