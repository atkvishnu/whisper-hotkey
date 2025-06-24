# Detailed Setup Guide

This guide provides step-by-step instructions for setting up Whisper Hotkey Transcribe on your system.

## System Requirements

### Minimum Requirements
- Ubuntu 20.04 or later (or Debian-based distribution)
- 4GB RAM (8GB+ recommended)
- 2GB free disk space
- x86_64 processor with AVX support
- Working microphone

### Tested Configuration
- Ubuntu 22.04.5 LTS
- Intel Ultra 9 185H (22 threads)
- 64GB RAM
- GNOME 42.9

## Step-by-Step Installation

### Step 1: Update System
```bash
sudo apt update && sudo apt upgrade -y
```

### Step 2: Install Dependencies
```bash
sudo apt install -y \
    build-essential \
    git \
    cmake \
    alsa-utils \
    xclip \
    libnotify-bin \
    pulseaudio-utils
```

### Step 3: Test Microphone
```bash
# List audio devices
arecord -l

# Test recording (speak for 5 seconds)
arecord -d 5 -f S16_LE -r 16000 test.wav
aplay test.wav
rm test.wav
```

### Step 4: Clone and Install
```bash
git clone https://github.com/atkvishnu/whisper-hotkey-transcribe.git
cd whisper-hotkey-transcribe
./install.sh
```

## Manual Hotkey Setup

If automatic setup fails, configure manually:

### GNOME (Ubuntu default)
1. Open Settings
2. Navigate to Keyboard → View and Customize Shortcuts
3. Scroll to bottom and click "Custom Shortcuts"
4. Click the + button
5. Fill in:
   - Name: `Whisper Transcribe`
   - Command: `/home/atkvishnu/Projects/whisper.cpp/whisper-toggle.sh`
   - Shortcut: Click "Set Shortcut" and press F9

### KDE Plasma
1. Open System Settings
2. Go to Shortcuts → Custom Shortcuts
3. Edit → New → Global Shortcut → Command/URL
4. Set trigger to F9
5. Set action to the script path

### XFCE
1. Open Settings → Keyboard
2. Go to Application Shortcuts tab
3. Click Add
4. Enter the script path and assign F9

## Troubleshooting

### Issue: F9 key not working

**Solution 1**: Restart GNOME Shell
```bash
# Press Alt+F2, type 'r', press Enter
# Or:
gnome-session-quit --logout
```

**Solution 2**: Check for conflicts
```bash
# List all F9 bindings
gsettings list-recursively | grep F9
```

### Issue: "Library not found" error

**Solution**: Add library path to your shell profile
```bash
echo 'export LD_LIBRARY_PATH=$HOME/Projects/whisper.cpp/build/src:$HOME/Projects/whisper.cpp/build/ggml/src:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc
```

### Issue: Poor transcription quality

**Solutions**:
1. Use a better microphone
2. Reduce background noise
3. Speak clearly and at normal pace
4. Try a larger model:
```bash
cd ~/Projects/whisper.cpp
bash ./models/download-ggml-model.sh small
# Edit whisper-toggle.sh to use ggml-small.bin
```

### Issue: High CPU usage

**Solutions**:
1. Use a smaller model (tiny or base)
2. Limit thread count by editing the script:
```bash
# Add -t flag to limit threads
"$WHISPER_BIN" -m "$MODEL_PATH" -f "$RECORDING_FILE" -t 4 -nt
```

## Performance Tuning

### For Laptops (Battery Saving)
- Use the tiny model
- Limit to 2-4 threads
- Increase notification intervals

### For Desktops (Best Quality)
- Use medium or large models
- Use all available threads
- Enable timestamps if needed

## Advanced Configuration

### Change Hotkey
Edit the binding in your desktop environment's keyboard settings

### Change Model
Edit `whisper-toggle.sh`:
```bash
MODEL_PATH="$WHISPER_PATH/models/ggml-small.bin"
```

### Custom Output Format
Modify the transcription processing in the script:
```bash
# Add timestamps
"$WHISPER_BIN" -m "$MODEL_PATH" -f "$RECORDING_FILE" -nt -ml 1

# Output to specific format
"$WHISPER_BIN" -m "$MODEL_PATH" -f "$RECORDING_FILE" -of txt
```

## Security Considerations

- Audio files are stored temporarily in `/tmp`
- No data is sent to external servers
- Files are automatically cleaned up
- Consider encrypting `/tmp` for sensitive use

## Getting Help

1. Check the [FAQ](https://github.com/atkvishnu/whisper-hotkey-transcribe/wiki/FAQ)
2. Search [existing issues](https://github.com/atkvishnu/whisper-hotkey-transcribe/issues)
3. Join our [discussions](https://github.com/atkvishnu/whisper-hotkey-transcribe/discussions)
4. Open a new issue with system details