import { useEffect, useState, type FormEvent } from 'react';
import { active, request, watchStatus, type Job } from './api';

export function App() {
  const [screen, setScreen] = useState<'upload' | 'review'>('upload');
  const [message, setMessage] = useState('Загрузите запись для подготовки аудио.');
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const isActive = active(job);

  useEffect(() => {
    if (!job || !isActive) return;
    return watchStatus(job.meeting_id, next => {
      setJob(next);
      setMessage(next.message);
    }, setMessage);
  }, [job?.meeting_id, isActive]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const file = data.get('file');
    if (!(file instanceof File) || file.size === 0) {
      setMessage('Выберите непустой файл записи.'); return;
    }
    if (file.size > 200 * 1024 * 1024) {
      setMessage('Размер файла превышает 200 МБ.'); return;
    }
    for (const key of ['meeting_date', 'timezone']) if (!data.get(key)) data.delete(key);
    setBusy(true);
    setMessage('Сохранение записи…');
    try {
      const next = await request<Job>('/api/meetings', { method: 'POST', body: data });
      setJob(next);
      setMessage(next.message);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Сервер недоступен. Проверьте запуск backend.');
    } finally { setBusy(false); }
  }

  async function remove() {
    if (!job || isActive) return;
    setBusy(true);
    try {
      await request<void>(`/api/meetings/${job.meeting_id}`, { method: 'DELETE' });
      setJob(null);
      setScreen('upload');
      setMessage('Встреча и её локальные файлы удалены.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Не удалось удалить встречу.');
    } finally { setBusy(false); }
  }

  return <div className="shell">
    <header><span className="brand">ПРОТОКОЛ</span><span>Внутренняя система · локальное хранение</span></header>
    <main>
      <h1>Протокол совещания</h1>
      <p className="intro">Загрузите запись и подготовьте аудио для локального распознавания.</p>
      <aside>Доступна подготовка аудио. Распознавание, диаризация, анализ и экспорт будут подключены следующим этапом.</aside>
      <nav aria-label="Экраны приложения">
        <button aria-current={screen === 'upload' ? 'page' : undefined} onClick={() => setScreen('upload')}>1. Загрузка и статус</button>
        <button aria-current={screen === 'review' ? 'page' : undefined} onClick={() => setScreen('review')}>2. Результат подготовки</button>
      </nav>
      <p role="status" aria-live="polite">{message}</p>
      {screen === 'upload' ? <div className="columns">
        <section><h2>Новая встреча</h2>
          <form onSubmit={upload}>
            <label>Название встречи<input name="title" required maxLength={1000} placeholder="Введите название" /></label>
            <label>Дата встречи (необязательно)<input name="meeting_date" type="date" /></label>
            <label>Часовой пояс (IANA, необязательно)<input name="timezone" defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone} /></label>
            <label>Файл записи<input name="file" type="file" accept=".mp4,.m4a,.mp3,.wav,.webm" required /></label>
            <p className="hint">MP4, M4A, MP3, WAV, WebM · до 200 МБ и 15 минут.</p>
            <button className="primary" disabled={busy || isActive}>{busy ? 'Ожидание…' : 'Загрузить запись'}</button>
          </form>
        </section>
        <section><h2>Статус подготовки</h2>
          <p>{job ? job.message : 'Запись ещё не загружена.'}</p>
          {job && <p className="hint">Идентификатор встречи: {job.meeting_id}</p>}
          {isActive && <p className="hint">Статус обновляется автоматически каждые 2 секунды.</p>}
          {job && !isActive && <button onClick={() => setScreen('review')}>Открыть результат</button>}
        </section>
      </div> : <section>
        <h2>Результат подготовки</h2>
        <div className="empty">{job?.status === 'ready_for_models'
          ? 'Аудио подготовлено. Локальные модели распознавания ещё не настроены'
          : job?.message ?? 'Загрузите запись на первом экране.'}</div>
        {job?.status === 'ready_for_models' && <p>Формат аудио: WAV, моно, 16 кГц, PCM 16-bit. Транскрипт ещё не создан.</p>}
        <div className="actions"><button disabled>Сохранить правки</button><button disabled>Утвердить</button><button disabled>Скачать DOCX</button><button disabled>Скачать PDF</button>
          <button disabled={!job || busy || isActive} onClick={remove}>Удалить встречу</button></div>
        {isActive && <p className="hint">Удаление доступно после завершения подготовки.</p>}
      </section>}
    </main>
  </div>;
}
