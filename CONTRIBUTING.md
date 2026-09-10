# Contributing to Whisper Hotkey

Bug reports, fixes, documentation improvements, and testing on Linux and macOS are welcome.

## Reporting bugs and suggesting changes

Search the [existing issues](https://github.com/atkvishnu/whisper-hotkey/issues) before [opening a new issue](https://github.com/atkvishnu/whisper-hotkey/issues/new).

For a bug report, include:

- Your OS and version; on Linux, include the distribution, desktop environment, and X11 or Wayland session type.
- The release or commit you are using, the command you ran, and any relevant configuration changes.
- Steps to reproduce the problem, what you expected, and what happened.
- Error output and, for installation or recording problems, the output of the installed launcher's `doctor` command if available.

Remove private text from logs before posting. For a feature request, describe the problem and give an example of how the change would help. Keep feedback specific and respectful.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep the change focused. For larger changes, open an issue to discuss the approach first.
3. Add or update tests for behavior changes and run the checks below.
4. Update user-facing instructions when behavior or setup changes.
5. Explain what changed, link any related issue, and list the checks and platforms you tested. Mention anything you could not test.

Use clear names and explain non-obvious behavior in comments. The installer and controller use Python's standard library and support Python 3.9+. Keep changes compatible with both Linux and macOS where applicable.

## Testing

Run the offline tests, shell syntax checks, and Python compilation from the repository root:

```sh
make check
```

CI runs these checks on Ubuntu and macOS with Python 3.9 and 3.13. For changes affecting installation, recording, transcription, or clipboard handling, also run the Linux integration container with Docker:

```sh
docker build -f tests/Dockerfile.integration -t whisper-hotkey-integration .
docker run --rm --pull=never whisper-hotkey-integration
```

Building the container needs internet access to install dependencies and download Whisper and its model. The integration test exercises virtual PulseAudio recording, transcription, and X11 and Wayland clipboard tools. It does not mount the host microphone or clipboard.

For desktop behavior changes, also check the affected platform in a real desktop session:

- Run the installed launcher with `doctor` appended to check dependencies.
- Start and stop recording using your configured shortcut, then verify the transcription can be pasted.
- Check notifications when enabled and microphone permissions where applicable.
- For installer changes, test a fresh installation and reinstalling over an existing configuration.

The installer does not assign a shortcut; F9 is only an example. Automated checks do not verify physical microphones, desktop shortcuts, or macOS permission prompts. See the [README](README.md) for setup and configuration details.
