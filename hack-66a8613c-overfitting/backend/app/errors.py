"""Only predefined descriptions cross API/log boundaries."""
MESSAGES = {
    'whisper_model_missing': 'Локальная модель Whisper отсутствует или неполна. Проверьте WHISPER_MODEL_PATH.',
    'pyannote_model_missing': 'Локальная модель диаризации отсутствует или неполна. Проверьте PYANNOTE_MODEL_PATH.',
    'ml_dependencies_missing': 'ML-зависимости не установлены или недоступны. Установите requirements-ml.txt.',
    'cuda_unavailable': 'CUDA с поддержкой float16 недоступна. Проверьте GPU, драйвер и CUDA-библиотеки.',
    'transcription_failed': 'Локальное распознавание завершилось ошибкой. Проверьте модель и ресурсы GPU.',
    'diarization_failed': 'Локальная диаризация завершилась ошибкой. Проверьте модель и ресурсы GPU.',
    'transcript_save_failed': 'Не удалось сохранить транскрипт в локальной базе.',
    'network_forbidden': 'ML-компонент попытался использовать сеть. Проверьте полноту локальных моделей.',
    'empty_file': 'Файл пуст. Выберите запись с аудио.',
    'unsupported_extension': 'Поддерживаются только MP4, M4A, MP3, WAV и WebM.',
    'file_too_large': 'Размер файла превышает 200 МБ.',
    'save_failed': 'Не удалось сохранить запись. Проверьте доступ и свободное место в локальном хранилище.',
    'unsafe_path': 'Операция остановлена: небезопасный путь в локальном хранилище.',
    'not_found': 'Встреча не найдена.',
    'busy': 'Встреча ожидает подготовки или обрабатывается. Удаление пока недоступно.',
    'delete_failed': 'Не удалось удалить файлы встречи. Проверьте доступ и повторите удаление.',
    'ffmpeg_missing': 'FFmpeg недоступен. Настройте FFMPEG_PATH и загрузите запись повторно.',
    'ffprobe_missing': 'FFprobe недоступен. Настройте FFPROBE_PATH и загрузите запись повторно.',
    'probe_failed': 'Не удалось проверить запись. Файл повреждён или формат не поддерживается.',
    'no_audio': 'В записи не найдена аудиодорожка.',
    'duration_invalid': 'Не удалось определить корректную длительность записи.',
    'duration_exceeded': 'Длительность записи превышает 15 минут.',
    'conversion_failed': 'Не удалось подготовить аудио. Проверьте запись и установку FFmpeg.',
    'processing_timeout': 'Превышено время подготовки аудио.',
    'processing_failed': 'Обработка завершилась ошибкой. Проверьте локальное окружение.',
    'interrupted': 'Обработка прервана перезапуском приложения. Загрузите запись повторно.',
    'legacy_state': 'Старая задача требует повторной загрузки для подготовки аудио.',
    'invalid_metadata': 'Проверьте название, дату и часовой пояс встречи (формат IANA).',
}


class LocalError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code = code
        self.status = status
        self.message = MESSAGES[code]
        super().__init__(self.message)
