"""Lazy, local-only CUDA adapters. No model names, downloads or CPU fallback."""
import gc
from importlib import import_module
from pathlib import Path

from ..errors import LocalError
from .contracts import AsrSegment, DiarizationTurn, Transcription
from .offline import local_inference


def _require_files(directory: Path, names: tuple[str, ...], code: str):
    if not directory.is_dir():
        raise LocalError(code, 503)
    try:
        for name in names:
            with (directory / name).open('rb') as stream:
                header = stream.read(128)
            if not header or header.startswith(b'version https://git-lfs.github.com/spec'):
                raise OSError()
    except OSError:
        raise LocalError(code, 503) from None


def _torch_cuda():
    torch = import_module('torch')
    try:
        if not torch.cuda.is_available():
            raise LocalError('cuda_unavailable', 503)
        torch.cuda.init()
    except LocalError:
        raise
    except Exception:
        raise LocalError('cuda_unavailable', 503) from None
    return torch


def _release(torch):
    gc.collect()
    if torch is not None:
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass


class FasterWhisperAdapter:
    def __init__(self, model_path: Path):
        self.model_path = model_path.resolve()

    def transcribe(self, audio: Path) -> Transcription:
        with local_inference():
            _require_files(self.model_path, ('model.bin', 'config.json', 'tokenizer.json', 'preprocessor_config.json'), 'whisper_model_missing')
            model = torch = None
            try:
                torch = _torch_cuda()
                ct2 = import_module('ctranslate2')
                try:
                    if ct2.get_cuda_device_count() < 1 or 'float16' not in ct2.get_supported_compute_types('cuda'):
                        raise ValueError()
                except Exception:
                    raise LocalError('cuda_unavailable', 503) from None
                factory = import_module('faster_whisper').WhisperModel
                model = factory(str(self.model_path), device='cuda', compute_type='float16', local_files_only=True)
                segments, info = model.transcribe(str(audio), language=None, task='transcribe',
                                                  multilingual=True, vad_filter=False,
                                                  condition_on_previous_text=False, log_progress=False)
                # Consume the lazy generator while errors/logs/network are guarded.
                result = [AsrSegment(start=s.start, end=s.end, text=s.text) for s in segments if s.text.strip()]
                return Transcription(segments=result, detected_language=info.language if result else None)
            except LocalError:
                raise
            except (ImportError, ModuleNotFoundError):
                raise LocalError('ml_dependencies_missing', 503) from None
            except Exception:
                raise LocalError('transcription_failed') from None
            finally:
                model = None
                _release(torch)


def _local_pipeline_config(root: Path, config: dict) -> dict:
    """Resolve community-1 assets before pyannote sees them; reject hub references."""
    try:
        if config['pipeline']['name'] != 'pyannote.audio.pipelines.SpeakerDiarization':
            raise ValueError()
        params = dict(config['pipeline']['params'])
        for component in ('segmentation', 'embedding', 'plda'):
            reference = params[component]
            if isinstance(reference, str):
                value = reference.removeprefix('$model/')
                path = Path(value)
                path = (path if path.is_absolute() else root / path).resolve()
                subfolder = None
            elif isinstance(reference, dict):
                if set(reference) - {'checkpoint', 'subfolder'}:
                    raise ValueError()
                path = Path(reference['checkpoint'])
                path = (path if path.is_absolute() else root / path).resolve()
                subfolder = reference.get('subfolder')
                if subfolder:
                    path = (path / subfolder).resolve()
            else:
                raise ValueError()
            if not path.is_relative_to(root) or not path.exists():
                raise ValueError()
            if component != 'plda':
                _require_files(path if path.is_dir() else path.parent,
                               ('pytorch_model.bin',) if path.is_dir() else (path.name,), 'pyannote_model_missing')
            else:
                _require_files(path, ('xvec_transform.npz', 'plda.npz'), 'pyannote_model_missing')
            params[component] = {'checkpoint': str(path), 'token': False}
        # Do not allow arbitrary preprocessors/classes or implicit online defaults.
        allowed = {'segmentation', 'embedding', 'plda', 'clustering', 'segmentation_step',
                   'embedding_exclude_overlap', 'embedding_batch_size', 'segmentation_batch_size', 'legacy'}
        if set(params) - allowed:
            raise ValueError()
        params['legacy'] = False
        return {'pipeline': {'name': 'pyannote.audio.pipelines.SpeakerDiarization', 'params': params},
                'params': config['params']}
    except (KeyError, TypeError, ValueError, OSError):
        raise LocalError('pyannote_model_missing', 503) from None


class PyannoteAdapter:
    def __init__(self, model_path: Path):
        self.model_path = model_path.resolve()

    def diarize(self, audio: Path) -> list[DiarizationTurn]:
        with local_inference():
            _require_files(self.model_path, ('config.yaml',), 'pyannote_model_missing')
            pipeline = torch = None
            try:
                yaml = import_module('yaml')
                with (self.model_path / 'config.yaml').open(encoding='utf-8') as stream:
                    config = _local_pipeline_config(self.model_path, yaml.safe_load(stream))
                torch = _torch_cuda()
                factory = import_module('pyannote.audio').Pipeline
                pipeline = factory.from_pretrained(config, token=False)
                if pipeline is None:
                    raise LocalError('pyannote_model_missing', 503)
                pipeline.to(torch.device('cuda'))
                # WAV is already PCM; pass in-memory samples to avoid codec/model downloads.
                import wave
                np = import_module('numpy')
                with wave.open(str(audio), 'rb') as stream:
                    if (stream.getnchannels(), stream.getframerate(), stream.getsampwidth()) != (1, 16000, 2):
                        raise ValueError()
                    samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype('float32') / 32768.0
                waveform = torch.from_numpy(samples).unsqueeze(0)
                output = pipeline({'waveform': waveform, 'sample_rate': 16000})
                return [DiarizationTurn(start=turn.start, end=turn.end, speaker=str(label))
                        for turn, _, label in output.speaker_diarization.itertracks(yield_label=True)]
            except LocalError:
                raise
            except (ImportError, ModuleNotFoundError):
                raise LocalError('ml_dependencies_missing', 503) from None
            except Exception:
                raise LocalError('diarization_failed') from None
            finally:
                pipeline = None
                _release(torch)
