"""Reusable local audio preparation. No model imports or network inputs."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import wave

from ..errors import LocalError


@dataclass(frozen=True)
class PreparedAudio:
    path: Path
    duration_seconds: float
    offset_seconds: float = 0.0


class AudioPreparer:
    def __init__(self, ffmpeg: str, ffprobe: str, max_duration: float = 900):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.max_duration = max_duration

    @staticmethod
    def executable(value: str, name: str) -> str:
        program = shutil.which(value)
        if program is None:
            raise LocalError(name + '_missing', 503)
        return program

    @staticmethod
    def run(arguments: list[str], timeout: int, code: str, capture: bool = False):
        try:
            return subprocess.run(arguments, check=True, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=timeout,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            raise LocalError('processing_timeout') from None
        except (OSError, subprocess.CalledProcessError):
            raise LocalError(code) from None

    def prepare(self, source: Path, destination: Path) -> PreparedAudio:
        ffmpeg = self.executable(self.ffmpeg, 'ffmpeg')
        ffprobe = self.executable(self.ffprobe, 'ffprobe')
        # Only container demuxers for supported uploads. No playlists or network protocols.
        input_options = ['-protocol_whitelist', 'file', '-format_whitelist', 'mov,mp3,wav,matroska,webm']
        response = self.run([ffprobe, '-v', 'error', *input_options,
                             '-show_entries', 'format=duration,format_name:stream=codec_type,duration',
                             '-of', 'json', str(source)], 30, 'probe_failed', capture=True)
        try:
            info = json.loads(response.stdout)
            streams = info['streams']
            if not any(stream.get('codec_type') == 'audio' for stream in streams):
                raise LocalError('no_audio')
            durations = [float(info['format']['duration'])]
            durations.extend(float(s['duration']) for s in streams if s.get('duration') not in (None, 'N/A'))
            if any(not math.isfinite(value) or value <= 0 for value in durations):
                raise ValueError()
            duration = max(durations)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LocalError('duration_invalid') from None
        if duration > self.max_duration:
            raise LocalError('duration_exceeded')
        try:
            self.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                      *input_options, '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
                      '-map_metadata', '-1', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                      '-t', str(self.max_duration + 1), '-f', 'wav', str(destination)],
                     300, 'conversion_failed')
            with wave.open(str(destination), 'rb') as audio:
                valid = (audio.getnchannels(), audio.getframerate(), audio.getsampwidth(), audio.getcomptype()) == (1, 16000, 2, 'NONE')
                actual_duration = audio.getnframes() / 16000
                if not valid or actual_duration <= 0:
                    raise LocalError('conversion_failed')
                if actual_duration > self.max_duration:
                    raise LocalError('duration_exceeded')
            return PreparedAudio(destination, duration)
        except (OSError, EOFError, wave.Error):
            raise LocalError('conversion_failed') from None
