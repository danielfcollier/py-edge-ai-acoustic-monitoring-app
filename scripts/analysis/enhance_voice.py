"""
Voice Enhancer & Compressor (Chunked + Noise Reduction).
Applies spectral noise reduction, bandpass filtering, and dynamic range compression
in 10-minute chunks to handle large files efficiently.

This script is designed to:
1. Reduce background static/hiss using spectral subtraction (noisereduce).
2. Isolate human voice frequencies (300Hz - 3400Hz).
3. Boost quiet speech and limit loud peaks (Dynamic Range Compression).
4. Maximize final volume (Normalization).

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2025
"""

import argparse
import logging
import math
import os
import sys

import noisereduce as nr
import numpy as np
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, normalize
from scipy.io import wavfile
from scipy.signal import butter, sosfiltfilt

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)


def butter_bandpass(lowcut, highcut, fs, order=6):
    """
    Generates the Second-Order Sections (SOS) filter coefficients for a Butterworth bandpass filter.

    :param lowcut: The low frequency cutoff in Hz (e.g., 300 Hz).
    :param highcut: The high frequency cutoff in Hz (e.g., 3400 Hz).
    :param fs: The sample rate of the audio in Hz (e.g., 48000 Hz).
    :param order: The order of the filter (higher means steeper roll-off). Default is 6.
    :return: A numpy array containing the SOS filter coefficients.
    """
    nyq = 0.5 * fs  # Nyquist Frequency (half the sample rate)
    low = lowcut / nyq
    high = highcut / nyq
    # We use SOS (Second-Order Sections) output format because it is numerically
    # more stable than the standard 'ba' (numerator/denominator) format for high-order filters.
    return butter(order, [low, high], btype="band", output="sos")


def butter_bandpass_filter(data, lowcut, highcut, fs, order=6):
    """
    Applies the Butterworth bandpass filter to the audio data using zero-phase filtering.

    This uses `sosfiltfilt` (forward-backward filtering) to ensure that the
    phase of the signal is preserved. This keeps the audio transients aligned
    and prevents "smearing" of the sound.

    :param data: The input audio array (normalized float).
    :param lowcut: Low cutoff frequency in Hz.
    :param highcut: High cutoff frequency in Hz.
    :param fs: Sample rate in Hz.
    :param order: Filter order. Default is 6.
    :return: A numpy array containing the filtered audio data.
    """
    sos = butter_bandpass(lowcut, highcut, fs, order=order)
    return sosfiltfilt(sos, data)

def process_chunk(data_chunk, sample_rate, low_freq, high_freq, reduce_noise_flag):
    # 1. Aggressive Spectral Noise Reduction
    # Since we are boosting volume massively, we need to kill the noise first.
    if reduce_noise_flag:
        data_chunk = nr.reduce_noise(
            y=data_chunk, 
            sr=sample_rate, 
            stationary=True, 
            prop_decrease=0.9,           # Increased to 0.9 (90% reduction)
            n_std_thresh_stationary=1.3  # Slightly more aggressive
        )

    # 2. Bandpass Filter
    filtered = butter_bandpass_filter(data_chunk, low_freq, high_freq, sample_rate, order=6)

    # 3. NOISE GATE (The "Silence" Enforcer)
    # Calculate the RMS (energy) of small windows to detect "silence" vs "sound"
    # This is a simple numpy-based gate.
    
    # Calculate global noise floor of this chunk (approximate using 10th percentile)
    # The noise floor is usually the quietest constant part of the signal.
    noise_floor = np.percentile(np.abs(filtered), 10)
    gate_threshold = noise_floor * 2.0  # Open gate only if 2x louder than noise
    
    # Create a mask: 1.0 where signal is loud, 0.1 where signal is quiet
    # We use 0.1 (attenuation) instead of 0.0 (silence) to sound more natural.
    mask = np.where(np.abs(filtered) > gate_threshold, 1.0, 0.1)
    
    # Apply the gate
    gated_data = filtered * mask

    # 4. HARD LIMITING (The "Door" Cutter)
    # We use the gated data to calculate the mean, so silence doesn't drag the average down too much
    non_silent_mean = np.mean(np.abs(gated_data[gated_data > noise_floor]))
    
    if np.isnan(non_silent_mean) or non_silent_mean == 0:
        non_silent_mean = 1e-6 # Avoid div by zero

    # Limit peaks to 5x the active average (Slightly relaxed from 4x)
    clip_limit = non_silent_mean * 5.0 
    limited_data = np.clip(gated_data, -clip_limit, clip_limit)
    
    # Normalize the limited data
    max_val = np.max(np.abs(limited_data))
    if max_val > 0:
        limited_data = limited_data / max_val

    # Convert to Int16 for Pydub
    final_int16 = (limited_data * 32767).astype(np.int16)

    seg = AudioSegment(
        final_int16.tobytes(),
        frame_rate=sample_rate,
        sample_width=2,
        channels=1,
    )

    # 5. Final Compression & Normalize
    compressed = compress_dynamic_range(
        seg, 
        threshold=-25.0, 
        ratio=6.0, 
        attack=5.0, 
        release=50.0
    )
    
    final = normalize(compressed, headroom=0.1)
    
    return final

def process_audio(input_path, output_path=None, low_freq=200, high_freq=2500, chunk_minutes=10, reduce_noise=False):
    """
    Main orchestration function.

    It loads the large audio file, splits it into manageable chunks to prevent RAM overflow,
    processes each chunk sequentially, and saves them as separate files.

    :param input_path: Path to the source WAV file.
    :param output_path: Base path for the destination files. If None, uses input filename.
                        Actual outputs will append '_partXXX' to this name.
    :param low_freq: Low frequency cutoff for voice isolation in Hz. Default 300.
    :param high_freq: High frequency cutoff for voice isolation in Hz. Default 3400.
    :param chunk_minutes: The duration of each split file in minutes. Default 10.
    :param reduce_noise: Boolean to enable/disable spectral noise reduction. Default True.
    """
    if not os.path.exists(input_path):
        logger.error(f"Error: File {input_path} not found.")
        sys.exit(1)

    logger.info(f"Loading {input_path}...")
    try:
        sample_rate, data = wavfile.read(input_path)
    except ValueError:
        logger.error("Error: Could not read WAV file. Ensure it is a standard PCM WAV.")
        sys.exit(1)

    # Convert to Float Normalized
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0

    # Mix to Mono
    if len(data.shape) > 1:
        logger.info("Mixing stereo to mono...")
        data = np.mean(data, axis=1)

    # --- CHUNK PROCESSING CALCULATION ---
    total_samples = len(data)
    chunk_samples = int(chunk_minutes * 60 * sample_rate)
    total_chunks = math.ceil(total_samples / chunk_samples)

    logger.info(f"Audio Duration: {total_samples / sample_rate / 60:.2f} minutes")
    if reduce_noise:
        logger.info("ℹ️  Noise Reduction is ENABLED (This may take longer)")

    if total_chunks > 1:
        logger.info(f"Splitting into {total_chunks} chunk(s) of ~{chunk_minutes} mins.")

    base_name = os.path.splitext(input_path)[0]

    for i in range(total_chunks):
        start = i * chunk_samples
        end = min(start + chunk_samples, total_samples)

        if total_chunks > 1:
            logger.info(f"--- Processing Chunk {i + 1}/{total_chunks} ---")

        chunk_data = data[start:end]

        # Process the specific chunk with the configured flags
        processed_seg = process_chunk(chunk_data, sample_rate, low_freq, high_freq, reduce_noise)

        # Generate Output Filename
        if output_path:
            out_root, out_ext = os.path.splitext(output_path)
            chunk_out = f"{out_root}_part{i + 1:03d}{out_ext}"
        else:
            chunk_out = f"{base_name}_enhanced_part{i + 1:03d}.mp3" if total_chunks > 1 else f"{base_name}_enhanced.mp3"

        logger.info(f"Encoding {chunk_out}...")
        processed_seg.export(chunk_out, format="mp3", bitrate="192k")

    logger.info("✅ All chunks processed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enhance voice in WAV and convert to MP3 (Chunked + Denoise).")
    parser.add_argument("input_file", help="Path to input WAV file")
    parser.add_argument("--out", help="Path to output MP3 file (optional)")
    parser.add_argument("--low", type=int, default=300, help="Low cutoff Hz")
    parser.add_argument("--high", type=int, default=3400, help="High cutoff Hz")
    parser.add_argument("--split", type=int, default=10, help="Split size in minutes")
    # New flag to disable noise reduction if needed
    parser.add_argument("--no-denoise", action="store_true", help="Disable spectral noise reduction (faster)")

    args = parser.parse_args()

    # Logic inverted: Default is True, if --no-denoise is passed it becomes False
    do_denoise = not args.no_denoise

    process_audio(args.input_file, args.out, args.low, args.high, args.split, do_denoise)
