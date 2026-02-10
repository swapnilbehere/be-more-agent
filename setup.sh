#!/bin/bash

# ==============================================================================
#  Be More Agent - Installer 🤖
# ==============================================================================

echo "🤖 INITIALIZING INSTALLER..."

# 1. System Updates & Dependencies
echo "[1/5] Updating System & Installing Libraries..."
sudo apt-get update
sudo apt-get install -y \
    python3-pip python3-venv \
    portaudio19-dev libasound2-dev \
    git cmake curl \
    libatlas-base-dev

# 2. Virtual Environment Setup
echo "[2/5] Setting up Python Environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# Install Python requirements
pip install --upgrade pip
pip install -r requirements.txt

# 3. Download Wake Word Model (OpenWakeWord)
echo "[3/5] Downloading Wake Word Model..."
if [ ! -f "wakeword.onnx" ]; then
    # Downloads a robust pre-trained model. You can replace this URL with your own custom model.
    # Using 'hey_jarvis' as a reliable default.
    curl -L -o wakeword.onnx https://github.com/dscripka/openWakeWord/raw/main/openwakeword/resources/models/hey_jarvis_v0.1.onnx
    echo "✅ Default wake word 'Hey Jarvis' installed. (Rename your own model to wakeword.onnx to override)"
else
    echo "✅ Wake word model found."
fi

# 4. Whisper.cpp Setup (Speech-to-Text)
echo "[4/5] Building Whisper (Ear)..."
if [ ! -d "whisper.cpp" ]; then
    git clone https://github.com/ggerganov/whisper.cpp.git
    cd whisper.cpp
    # Download the small English model (good balance of speed/accuracy for Pi)
    bash ./models/download-ggml-model.sh base.en
    # Compile
    make
    cd ..
else
    echo "✅ Whisper already installed."
fi

# 5. Piper Setup (Text-to-Speech)
echo "[5/5] Setting up Piper (Voice)..."
if [ ! -d "piper" ]; then
    # Detect Architecture (64-bit vs 32-bit)
    ARCH=$(uname -m)
    if [[ "$ARCH" == "aarch64" ]]; then
        PIPER_URL="https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_aarch64.tar.gz"
    else
        PIPER_URL="https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_armv7l.tar.gz"
    fi
    
    wget -O piper.tar.gz $PIPER_URL
    tar -xvf piper.tar.gz
    rm piper.tar.gz
    
    # Download a Voice Model (Ryan - Low/Medium quality is fast on Pi)
    cd piper
    wget -O en_GB-semaine-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/semaine/medium/en_GB-semaine-medium.onnx
    wget -O en_GB-semaine-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/semaine/medium/en_GB-semaine-medium.onnx.json
    cd ..
else
    echo "✅ Piper already installed."
fi

echo "========================================================"
echo "🎉 INSTALLATION COMPLETE!"
echo "   1. Ensure Ollama is running: 'ollama serve'"
echo "   2. Start the Agent: 'python agent.py'"
echo "========================================================"
