#!/bin/bash

# Whisper Hotkey Transcribe - Installation Script
# This script automates the setup of whisper.cpp with F9 hotkey transcription

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_info() {
    echo -e "${YELLOW}[i]${NC} $1"
}

# Check if running on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    print_error "This script is designed for Linux systems only"
    exit 1
fi

print_info "Starting Whisper Hotkey Transcribe installation..."

# Check for required commands
print_info "Checking dependencies..."

MISSING_DEPS=""
for cmd in git make gcc g++ arecord; do
    if ! command -v $cmd &> /dev/null; then
        MISSING_DEPS="$MISSING_DEPS $cmd"
    fi
done

if [ ! -z "$MISSING_DEPS" ]; then
    print_error "Missing required dependencies:$MISSING_DEPS"
    print_info "Installing dependencies..."
    sudo apt update
    sudo apt install -y build-essential git alsa-utils xclip libnotify-bin
fi

# Check for clipboard tool
if ! command -v xclip &> /dev/null && ! command -v xsel &> /dev/null; then
    print_info "Installing clipboard tool..."
    sudo apt install -y xclip
fi

# Set installation directory
INSTALL_DIR="$HOME/Projects/whisper.cpp"
print_info "Installation directory: $INSTALL_DIR"

# Clone whisper.cpp if not exists
if [ ! -d "$INSTALL_DIR" ]; then
    print_info "Cloning whisper.cpp repository..."
    mkdir -p "$HOME/Projects"
    cd "$HOME/Projects"
    git clone https://github.com/ggerganov/whisper.cpp.git
    print_status "Repository cloned"
else
    print_info "whisper.cpp directory already exists"
    cd "$INSTALL_DIR"
    print_info "Pulling latest changes..."
    git pull
fi

# Build whisper.cpp
print_info "Building whisper.cpp (this may take a few minutes)..."
cd "$INSTALL_DIR"
make clean
make -j$(nproc)
print_status "Build completed"

# Download model if not exists
MODEL_PATH="$INSTALL_DIR/models/ggml-base.bin"
if [ ! -f "$MODEL_PATH" ]; then
    print_info "Downloading base model (74MB)..."
    bash ./models/download-ggml-model.sh base
    print_status "Model downloaded"
else
    print_status "Model already exists"
fi

# Copy the toggle script
print_info "Installing transcription script..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/scripts/whisper-toggle.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/whisper-toggle.sh"

# Update paths in the script
sed -i "s|/home/vishnu|$HOME|g" "$INSTALL_DIR/whisper-toggle.sh"
print_status "Script installed"

# Set up keyboard shortcut
print_info "Setting up F9 keyboard shortcut..."

# Check if GNOME is running
if [ "$XDG_CURRENT_DESKTOP" = "GNOME" ] || [ "$XDG_CURRENT_DESKTOP" = "ubuntu:GNOME" ]; then
    # Reset and create new custom keybinding
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/whisper-transcribe/']"
    
    # Set keybinding properties
    KEYBINDING_PATH="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/whisper-transcribe/"
    gsettings set $KEYBINDING_PATH name 'Whisper Transcribe'
    gsettings set $KEYBINDING_PATH command "$INSTALL_DIR/whisper-toggle.sh"
    gsettings set $KEYBINDING_PATH binding 'F9'
    
    print_status "F9 hotkey configured"
    print_info "You may need to log out and back in for the hotkey to take effect"
else
    print_info "Non-GNOME desktop detected. Please manually set up the keyboard shortcut:"
    print_info "Command: $INSTALL_DIR/whisper-toggle.sh"
    print_info "Suggested key: F9"
fi

# Test the installation
print_info "Testing whisper binary..."
export LD_LIBRARY_PATH="$INSTALL_DIR/build/src:$INSTALL_DIR/build/ggml/src:$LD_LIBRARY_PATH"
if "$INSTALL_DIR/build/bin/whisper-cli" --help &> /dev/null; then
    print_status "Whisper binary is working"
else
    print_error "Whisper binary test failed"
fi

# Create test recording
print_info "Creating a test recording (speak for 3 seconds)..."
sleep 1
timeout 3 arecord -f S16_LE -r 16000 -c 1 /tmp/test-whisper.wav 2>/dev/null || true

if [ -f /tmp/test-whisper.wav ]; then
    print_status "Audio recording works"
    rm /tmp/test-whisper.wav
else
    print_error "Audio recording failed - please check your microphone"
fi

print_info ""
print_status "Installation complete!"
print_info ""
print_info "Usage:"
print_info "1. Press F9 to start recording"
print_info "2. Speak into your microphone" 
print_info "3. Press F9 again to stop and transcribe"
print_info "4. The text will be copied to your clipboard"
print_info ""
print_info "If F9 doesn't work immediately:"
print_info "- Log out and log back in"
print_info "- Or manually add the shortcut in Settings → Keyboard → Custom Shortcuts"
print_info ""
print_status "Enjoy your voice-to-text transcription!"