#!/bin/bash

# Configuration
WHISPER_PATH="/home/vishnu/Projects/whisper.cpp"
MODEL_PATH="$WHISPER_PATH/models/ggml-base.bin"
WHISPER_BIN="$WHISPER_PATH/build/bin/whisper-cli"
TEMP_DIR="/tmp/whisper-recordings"
PID_FILE="/tmp/whisper-recording.pid"
RECORDING_FILE="$TEMP_DIR/recording_$(date +%Y%m%d_%H%M%S).wav"

# Create temp directory if it doesn't exist
mkdir -p "$TEMP_DIR"

# Function to show notification with icon
notify() {
    local urgency="$1"
    local message="$2"
    local icon="$3"
    
    if command -v notify-send &> /dev/null; then
        notify-send -u "$urgency" -i "$icon" "Whisper Transcription" "$message"
    fi
    echo "$message"
}

# Check if recording is already in progress
if [ -f "$PID_FILE" ]; then
    # Recording is in progress, stop it
    PID=$(cat "$PID_FILE")
    
    # Check if process is still running
    if kill -0 "$PID" 2>/dev/null; then
        # Stop recording
        kill -TERM "$PID"
        rm -f "$PID_FILE"
        
        notify "normal" "Recording stopped. Processing..." "media-playback-stop"
        
        # Wait a moment for the file to be written
        sleep 0.5
        
        # Find the most recent recording
        RECORDING_FILE=$(ls -t "$TEMP_DIR"/recording_*.wav 2>/dev/null | head -1)
        
        if [ ! -f "$RECORDING_FILE" ]; then
            notify "critical" "No recording file found!" "dialog-error"
            exit 1
        fi
        
        # Get file size
        FILE_SIZE=$(stat -c%s "$RECORDING_FILE" 2>/dev/null || stat -f%z "$RECORDING_FILE" 2>/dev/null)
        if [ "$FILE_SIZE" -lt 1000 ]; then
            notify "critical" "Recording too short!" "dialog-warning"
            rm "$RECORDING_FILE"
            exit 1
        fi
        
        # Transcribe the audio
        notify "normal" "Transcribing audio..." "applications-multimedia"
        
        export LD_LIBRARY_PATH="$WHISPER_PATH/build/src:$WHISPER_PATH/build/ggml/src:$LD_LIBRARY_PATH"
        TRANSCRIPTION=$("$WHISPER_BIN" -m "$MODEL_PATH" -f "$RECORDING_FILE" -nt 2>&1 | grep -v "whisper_" | grep -v "main:" | grep -v "system_info:" | grep -v "^$" | sed 's/^[[:space:]]*//' | tr -d '\n')
        
        # Check if transcription was successful
        if [ -z "$TRANSCRIPTION" ]; then
            notify "critical" "Transcription failed!" "dialog-error"
            exit 1
        fi
        
        # Copy to clipboard
        if command -v xclip &> /dev/null; then
            echo -n "$TRANSCRIPTION" | xclip -selection clipboard
            notify "normal" "Transcription copied to clipboard!" "edit-paste"
        elif command -v xsel &> /dev/null; then
            echo -n "$TRANSCRIPTION" | xsel --clipboard --input
            notify "normal" "Transcription copied to clipboard!" "edit-paste"
        else
            notify "normal" "Transcription complete (no clipboard tool found)" "dialog-information"
        fi
        
        # Save transcription to file
        TRANSCRIPT_FILE="$TEMP_DIR/transcript_$(date +%Y%m%d_%H%M%S).txt"
        echo "$TRANSCRIPTION" > "$TRANSCRIPT_FILE"
        
        # Show transcription in notification
        PREVIEW=$(echo "$TRANSCRIPTION" | head -c 100)
        if [ ${#TRANSCRIPTION} -gt 100 ]; then
            PREVIEW="${PREVIEW}..."
        fi
        notify "normal" "Transcription: $PREVIEW" "text-x-generic"
        
        # Clean up old recordings (keep last 10)
        ls -t "$TEMP_DIR"/recording_*.wav 2>/dev/null | tail -n +11 | xargs -r rm
        ls -t "$TEMP_DIR"/transcript_*.txt 2>/dev/null | tail -n +11 | xargs -r rm
    else
        # Process not running, clean up PID file
        rm -f "$PID_FILE"
        notify "normal" "No recording in progress" "dialog-information"
    fi
else
    # No recording in progress, start one
    notify "normal" "Recording started... Press F9 again to stop" "media-record"
    
    # Start recording in background
    RECORDING_FILE="$TEMP_DIR/recording_$(date +%Y%m%d_%H%M%S).wav"
    arecord -f S16_LE -r 16000 -c 1 "$RECORDING_FILE" 2>/dev/null &
    
    # Save PID
    echo $! > "$PID_FILE"
fi