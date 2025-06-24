# 🎙️ Whisper Hotkey Transcribe

Instantly transcribe your voice to text with a single keypress using OpenAI's Whisper running locally on your machine. Press F9 to start recording, press F9 again to stop and get the transcription copied to your clipboard!

## ✨ Features

- **One-key operation**: Press F9 to start/stop recording
- **Instant transcription**: Audio is processed locally using whisper.cpp
- **Clipboard integration**: Transcribed text is automatically copied to clipboard
- **Visual feedback**: Desktop notifications show recording status
- **CPU optimized**: Runs efficiently without GPU (perfect for laptops)
- **Privacy first**: Everything runs locally, no internet required

## 🖥️ Tested System

This setup was tested and runs perfectly on:
- **OS**: Ubuntu 22.04.5 LTS
- **CPU**: Intel Ultra 9 185H (22 threads)
- **RAM**: 64GB
- **Desktop**: GNOME 42.9

## 📋 Prerequisites

- Ubuntu/Debian-based Linux distribution
- GNOME desktop environment (for keybinding setup)
- Basic development tools (`git`, `make`, `gcc`)
- Audio recording tools (`arecord` from `alsa-utils`)
- Clipboard tool (`xclip` or `xsel`)

## 🚀 Installation

### Quick Install

```bash
# Clone this repository
git clone https://github.com/atkvishnu/whisper-hotkey-transcribe.git
cd whisper-hotkey-transcribe

# Run the installation script
chmod +x install.sh
./install.sh
```

### Manual Installation

1. **Install dependencies**:
```bash
sudo apt update
sudo apt install build-essential git alsa-utils xclip libnotify-bin
```

2. **Clone and build whisper.cpp**:
```bash
cd ~/Projects
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
make -j$(nproc)
```

3. **Download a Whisper model**:
```bash
# Download the base model (good balance of speed and accuracy)
bash ./models/download-ggml-model.sh base
```

4. **Set up the transcription script**:
```bash
# Copy the script from this repo
cp scripts/whisper-toggle.sh ~/Projects/whisper.cpp/
chmod +x ~/Projects/whisper.cpp/whisper-toggle.sh
```

5. **Configure the F9 hotkey**:
   
   **Method 1: Manual Configuration (Recommended)**
   - Open Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts
   - Click the + button to add a new shortcut
   - Fill in the following:
     - Name: `Whisper Transcribe`
     - Command: `/home/atkvishnu/Projects/whisper.cpp/whisper-toggle.sh`
     - Shortcut: Click "Set Shortcut" and press F9
   
   **Method 2: Command Line (May require logout)**
   ```bash
   # The install script attempts this automatically
   gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/']"
   gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/ name 'Whisper Transcribe'
   gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/ command '/home/atkvishnu/Projects/whisper.cpp/whisper-toggle.sh'
   gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/ binding 'F9'
   ```

## 🎯 Usage

1. **Press F9** - You'll see a notification "Recording started... Press F9 again to stop"
2. **Speak clearly** into your microphone
3. **Press F9 again** - Recording stops, audio is transcribed
4. **Check your clipboard** - The transcribed text is automatically copied!

## 🔧 Configuration

The script can be customized by editing `whisper-toggle.sh`:

- **Model selection**: Change `MODEL_PATH` to use different Whisper models
- **Audio quality**: Modify `arecord` parameters for different sample rates
- **File retention**: Adjust how many recordings to keep (default: 10)

### Available Models

- `tiny` - Fastest, lowest accuracy (39 MB)
- `base` - Good balance (74 MB) - **Recommended**
- `small` - Better accuracy (244 MB)
- `medium` - High accuracy (769 MB)
- `large` - Best accuracy (1550 MB)

## 📁 File Locations

- Recordings: `/tmp/whisper-recordings/recording_*.wav`
- Transcripts: `/tmp/whisper-recordings/transcript_*.txt`
- PID file: `/tmp/whisper-recording.pid`

## 🐛 Troubleshooting

### F9 key not working
1. Log out and log back in after setting up the keybinding
2. Or restart GNOME Shell: Alt+F2, type 'r', press Enter

### "Library not found" errors
The script includes the necessary library paths. If you still get errors:
```bash
export LD_LIBRARY_PATH=/path/to/whisper.cpp/build/src:/path/to/whisper.cpp/build/ggml/src:$LD_LIBRARY_PATH
```

### No audio recording
Check your microphone:
```bash
# List recording devices
arecord -l

# Test recording
arecord -d 5 test.wav
aplay test.wav
```

### Transcription accuracy issues
- Speak clearly and avoid background noise
- Try a larger model for better accuracy
- Ensure audio levels are appropriate

## 🤝 Contributing

Feel free to open issues or submit pull requests! Some ideas for improvements:
- Support for other desktop environments
- Additional hotkey configurations  
- Integration with other applications
- Support for multiple languages

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) - Georgi Gerganov's excellent C++ port of Whisper
- [OpenAI Whisper](https://github.com/openai/whisper) - The original Whisper model
- Tested and developed on an Intel Ultra 9 185H system with 64GB RAM

---

Made with ❤️ for the open-source community. If you find this useful, please star the repository!