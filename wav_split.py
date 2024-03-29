import collections
import contextlib
import wave
import numpy as np
from scipy.signal import resample_poly

# def read_wave(path):
#     """Reads a .wav file.
#
#     Takes the path, and returns (PCM audio data, sample rate).
#     """
#     with contextlib.closing(wave.open(path, 'rb')) as wf:
#         num_channels = wf.getnchannels()
#         assert num_channels == 1
#         sample_width = wf.getsampwidth()
#         assert sample_width == 2
#         sample_rate = wf.getframerate()
#         assert sample_rate in (8000, 16000, 32000)
#         frames = wf.getnframes()
#         pcm_data = wf.readframes(frames)
#         duration = frames / sample_rate
#         return pcm_data, sample_rate, duration

def read_wave(path, target_sample_rate=16000):
    """Reads a .wav file and resamples it to the target sample rate.

    Takes the path and target sample rate, and returns (PCM audio data, target sample rate, duration).
    """
    with contextlib.closing(wave.open(path, 'rb')) as wf:
        num_channels, sample_width, sample_rate, frames, _, _ = wf.getparams()
        assert sample_width == 2, "Only supports 16-bit audio."

        # Read frames and convert to byte array
        pcm_data = wf.readframes(frames)

        # Convert byte array to numpy array
        pcm_array = np.frombuffer(pcm_data, dtype=np.int16)

        # Check if the audio is mono or stereo and convert to mono if necessary
        if num_channels == 2:
            pcm_array = pcm_array.reshape(-1, 2).mean(axis=1).astype(np.int16)

        # Resample if the sample rate is not one of the expected rates
        if sample_rate not in (8000, 16000, 32000):
            # Calculate the number of output samples
            num_output_samples = int(frames * target_sample_rate / sample_rate)
            pcm_array = resample_poly(pcm_array, target_sample_rate, sample_rate)
            pcm_array = np.round(pcm_array).astype(np.int16)  # Ensure it's int16

        duration = len(pcm_array) / target_sample_rate
        return pcm_array.tobytes(), target_sample_rate, duration


def write_wave(path, audio, sample_rate):
    """Writes a .wav file.

    Takes path, PCM audio data, and sample rate.
    """
    with contextlib.closing(wave.open(path, 'wb')) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio)


class Frame(object):
    """Represents a "frame" of audio data."""

    def __init__(self, bytes, timestamp, duration):
        self.bytes = bytes
        self.timestamp = timestamp
        self.duration = duration


def frame_generator(frame_duration_ms, audio, sample_rate):
    """Generates audio frames from PCM audio data.

    Takes the desired frame duration in milliseconds, the PCM data, and
    the sample rate.

    Yields Frames of the requested duration.
    """
    n = int(sample_rate * (frame_duration_ms / 1000.0) * 2)
    offset = 0
    timestamp = 0.0
    duration = (float(n) / sample_rate) / 2.0
    while offset + n < len(audio):
        yield Frame(audio[offset:offset + n], timestamp, duration)
        timestamp += duration
        offset += n


def vad_collector(sample_rate, frame_duration_ms, padding_duration_ms, vad, frames):
    """Filters out non-voiced audio frames.

    Given a webrtcvad.Vad and a source of audio frames, yields only
    the voiced audio.

    Uses a padded, sliding window algorithm over the audio frames.
    When more than 90% of the frames in the window are voiced (as
    reported by the VAD), the collector triggers and begins yielding
    audio frames. Then the collector waits until 90% of the frames in
    the window are unvoiced to detrigger.

    The window is padded at the front and back to provide a small
    amount of silence or the beginnings/endings of speech around the
    voiced frames.

    Arguments:

    sample_rate - The audio sample rate, in Hz.
    frame_duration_ms - The frame duration in milliseconds.
    padding_duration_ms - The amount to pad the window, in milliseconds.
    vad - An instance of webrtcvad.Vad.
    frames - a source of audio frames (sequence or generator).

    Returns: A generator that yields PCM audio data.
    """
    num_padding_frames = int(padding_duration_ms / frame_duration_ms)
    # We use a deque for our sliding window/ring buffer.
    ring_buffer = collections.deque(maxlen=num_padding_frames)
    # We have two states: TRIGGERED and NOTTRIGGERED. We start in the
    # NOTTRIGGERED state.
    triggered = False

    voiced_frames = []
    for frame in frames:
        is_speech = vad.is_speech(frame.bytes, sample_rate)

        if not triggered:
            ring_buffer.append((frame, is_speech))
            num_voiced = len([f for f, speech in ring_buffer if speech])
            # If we're NOTTRIGGERED and more than 90% of the frames in
            # the ring buffer are voiced frames, then enter the
            # TRIGGERED state.
            if num_voiced > 0.9 * ring_buffer.maxlen:
                triggered = True
                # We want to yield all the audio we see from now until
                # we are NOTTRIGGERED, but we have to start with the
                # audio that's already in the ring buffer.
                for f, s in ring_buffer:
                    voiced_frames.append(f)
                ring_buffer.clear()
        else:
            # We're in the TRIGGERED state, so collect the audio data
            # and add it to the ring buffer.
            voiced_frames.append(frame)
            ring_buffer.append((frame, is_speech))
            num_unvoiced = len([f for f, speech in ring_buffer if not speech])
            # If more than 90% of the frames in the ring buffer are
            # unvoiced, then enter NOTTRIGGERED and yield whatever
            # audio we've collected.
            if num_unvoiced > 0.9 * ring_buffer.maxlen:
                triggered = False
                yield b''.join([f.bytes for f in voiced_frames])
                ring_buffer.clear()
                voiced_frames = []
    if triggered:
        pass
    # If we have any leftover voiced audio when we run out of input,
    # yield it.
    if voiced_frames:
        yield b''.join([f.bytes for f in voiced_frames])
