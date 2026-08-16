"""
Perception - L2: Voice Activity Detection via Silero VAD ONNX

Silero VAD is a state-of-the-art voice activity detector (~1.7MB ONNX).
Detects whether someone is speaking in an audio chunk.

Also provides AudioCapture for MacBook microphone input.
"""

import logging
import collections
from typing import Optional

import numpy as np
import onnxruntime as ort

from config import AUDIO_DEVICE_INDEX, VAD_THRESHOLD
from runtime.utils.model_loader import ensure_model, load_session

logger = logging.getLogger("L2.VAD")

_VAD_MODEL = "silero_vad"
_SAMPLE_RATE = 16000
_CHUNK_SAMPLES = 512       # 32ms at 16kHz
_SPEECH_WINDOW = 0.3       # seconds—if VAD triggered recently, keep True


class VoiceActivityDetector:
    """
    Silero VAD wrapper. Detects speech in 16kHz mono audio chunks.

    Usage:
        vad = VoiceActivityDetector()
        for audio_chunk in audio_stream:
            if vad.is_speech(audio_chunk):
                ...
    """

    def __init__(self, threshold: float = VAD_THRESHOLD):
        path = ensure_model(_VAD_MODEL)
        self._session = load_session(str(path))
        self.threshold = threshold
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sample_rate = _SAMPLE_RATE
        self._input_name = self._session.get_inputs()[0].name
        self._state_name = self._session.get_inputs()[1].name
        self._last_speech_time = 0.0
        self._hangover_samples = int(_SPEECH_WINDOW * _SAMPLE_RATE / _CHUNK_SAMPLES)
        self._hangover_count = 0

    def reset_state(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._hangover_count = 0

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """
        Check if audio chunk contains speech.

        Args:
            audio_chunk: float32 array, 16kHz mono, length == _CHUNK_SAMPLES (512).

        Returns:
            True if speech detected (with hangover smoothing).
        """
        if len(audio_chunk) != _CHUNK_SAMPLES:
            raise ValueError(f"Expected {_CHUNK_SAMPLES} samples, got {len(audio_chunk)}")

        ort_inputs = {
            self._input_name: audio_chunk.reshape(1, _CHUNK_SAMPLES),
            self._state_name: self._state,
        }
        prob, self._state = self._session.run(None, ort_inputs)
        speech_prob = float(prob)

        if speech_prob > self.threshold:
            self._hangover_count = self._hangover_samples
            return True

        if self._hangover_count > 0:
            self._hangover_count -= 1
            return True

        return False


class AudioCapture:
    """
    MacBook microphone capture at 16kHz mono.

    Requires: pip install sounddevice
    """

    def __init__(self, device_index: int = AUDIO_DEVICE_INDEX):
        self.device_index = device_index
        self._stream = None
        self._running = False

    def start(self) -> bool:
        """Open the microphone. Returns True on success."""
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(
                device=self.device_index,
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype=np.float32,
                blocksize=_CHUNK_SAMPLES,
                callback=self._callback if False else None,
            )
            self._stream.start()
            self._running = True
            logger.info("Microphone opened: device=%d, rate=%dHz",
                        self.device_index, _SAMPLE_RATE)
            return True
        except Exception as e:
            logger.error("Failed to open microphone: %s", e)
            logger.error("Install sounddevice: pip install sounddevice")
            return False

    def read(self) -> Optional[np.ndarray]:
        """
        Read one chunk of audio samples.
        Returns float32 array of _CHUNK_SAMPLES, or None on error.
        """
        if not self._running or self._stream is None:
            return None
        try:
            data, _ = self._stream.read(_CHUNK_SAMPLES)
            return data.flatten().astype(np.float32)
        except Exception as e:
            logger.warning("Audio read error: %s", e)
            return None

    def stop(self) -> None:
        """Close the microphone."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("Microphone closed")
