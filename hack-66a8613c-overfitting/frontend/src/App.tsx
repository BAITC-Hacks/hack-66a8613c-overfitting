import { useEffect, useState, type FormEvent } from 'react';
import { active, request, watchStatus, type Job, type MeetingResult } from './api';

function timestamp(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes.toString().padStart(2, '0')}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
}

export function App() {
  const [screen, setScreen] = useState<'upload' | 'review'>('upload');
  const [message, setMessage] = useState('Загрузите запись для подготовки аудио.');
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MeetingResult | null>(null);
  const [resultError, setResultError] = useState('');
  const [resultAttempt, setResultAttempt] = useState(0);
  const isActive = active(job);

  useEffect(() => {
    if (!job || !isActive) return;
    return watchStatus(job.meeting_id, next => {
      setJob(next);
      setMessage(next.message);
    }, setMessage);
  }, [job?.meeting_id, isActive]);

  useEffect(() => {
    setResult(null);
    setResultError('');
    if (!job || !['analyzing', 'ready', 'failed'].includes(job.status)) return;
    const controller = new AbortController();
    request<MeetingResult>(`/api/meetings/${job.meeting_id}/result`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setResult(value); })
      .catch(() => { if (!controller.signal.aborted) setResultError('Не удалось загрузить транскрипт. Повторите запрос.'); });
    return () => controller.abort();
  }, [job?.meeting_id, job?.status, resultAttempt]);

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
      <p className="intro">Загрузите запись для локального распознавания, саммари и извлечения поручений.</p>
      <aside>Машинный транскрипт и анализ требуют проверки человеком. Правки и экспорт пока недоступны.</aside>
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
        <section><h2>Статус обработки</h2>
          <p>{job ? job.message : 'Запись ещё не загружена.'}</p>
          {job && <p className="hint">Идентификатор встречи: {job.meeting_id}</p>}
          {isActive && <p className="hint">Статус обновляется автоматически каждые 2 секунды.</p>}
          {job && !isActive && <button onClick={() => setScreen('review')}>Открыть результат</button>}
        </section>
      </div> : <section>
        <h2>Результат обработки</h2>
        <div className="empty">{job?.message ?? 'Загрузите запись на первом экране.'}</div>
        {job?.status === 'ready_for_models' && <p>Формат аудио: WAV, моно, 16 кГц, PCM 16-bit. Транскрипт ещё не создан.</p>}
        {job?.status === 'failed' && <p className="hint">Код ошибки: {job.error_code}. Проверьте локальное окружение и загрузите запись повторно.</p>}
        {job?.status === 'ready' && !result && !resultError && <p>Загрузка транскрипта…</p>}
        {resultError && <div role="alert"><p>{resultError}</p><button onClick={() => setResultAttempt(value => value + 1)}>Повторить загрузку транскрипта</button></div>}
        {result && <>
          {result.analysis_completed ? <>
            <h3>Саммари по ключевым пунктам</h3>
            {result.requires_review && <p className="review-label">Требует проверки</p>}
            <p className="analysis-text">{result.summary || 'Нет речевых данных для саммари.'}</p>
            {result.key_points.length > 0 ? <div className="table-wrap"><table>
              <thead><tr><th>Направление / доклад</th><th>Показатель</th><th>Проблема</th></tr></thead>
              <tbody>{result.key_points.map(point => <tr key={point.id}>
                <td>{point.direction}{point.requires_review && <div className="review-label">Требует проверки</div>}
                  <SourceLinks ids={point.source_utterance_ids} result={result} /></td>
                <td>{point.metric}</td><td>{point.problem}</td>
              </tr>)}</tbody>
            </table></div> : <p>Ключевые пункты не выделены.</p>}
            {result.topics.map(topic => {
              const actions = result.action_items.filter(item => item.topic_id === topic.id);
              return <div key={topic.id} className="topic">
                <h3>Тема {topic.position} — {topic.title}</h3>
                <p className="analysis-text">{topic.summary}</p>
                {topic.requires_review && <p className="review-label">Требует проверки</p>}
                <SourceLinks ids={topic.source_utterance_ids} result={result} />
                {actions.length > 0 ? <div className="table-wrap"><table>
                  <thead><tr><th>Поручение</th><th>Ответственный</th><th>Срок</th></tr></thead>
                  <tbody>{actions.map(item => <tr key={item.id}>
                    <td>{item.text}{item.requires_review && <div className="review-label">Требует проверки</div>}
                      <SourceLinks ids={item.source_utterance_ids} result={result} /></td>
                    <td>{item.responsible}</td><td>{item.deadline_original}</td>
                  </tr>)}</tbody>
                </table></div> : <p>Явные поручения по теме не выделены.</p>}
              </div>;
            })}
          </> : <p className="hint">{job?.status === 'analyzing' ? 'Саммари и поручения формируются локально. Транскрипт уже сохранён.' : 'Результат анализа отсутствует.'}</p>}
          <h3>Транскрипт</h3>
          {result.detected_language && <p className="hint">Начальная оценка языка: {result.detected_language}. Запись может содержать несколько языков.</p>}
          {result.utterances.length === 0 ? <p>{job?.status === 'failed' ? 'Сохранённых реплик нет.' : 'Распознавание завершено. Речевые реплики не обнаружены.'}</p> : <ol className="timeline">
            {result.utterances.map(utterance => <li key={utterance.id} id={`utterance-${utterance.id}`}>
              <div><strong>{result.speakers.find(speaker => speaker.id === utterance.speaker_id)?.name ?? 'Спикер не определён'}</strong>
                {' · '}<time>{timestamp(utterance.start_seconds)} — {timestamp(utterance.end_seconds)}</time>
                {utterance.requires_review && <span className="hint"> · Требует проверки</span>}</div>
              <p>{utterance.text}</p>
            </li>)}
          </ol>}
        </>}
        <div className="actions"><button disabled>Сохранить правки</button><button disabled>Утвердить</button><button disabled>Скачать DOCX</button><button disabled>Скачать PDF</button>
          <button disabled={!job || busy || isActive} onClick={remove}>Удалить встречу</button></div>
        {isActive && <p className="hint">Удаление доступно после завершения обработки.</p>}
      </section>}
    </main>
  </div>;
}

function SourceLinks({ ids, result }: { ids: string[]; result: MeetingResult }) {
  return <div className="sources">{ids.map(id => {
    const source = result.utterances.find(item => item.id === id);
    return source ? <a key={id} href={`#utterance-${id}`}>Реплика {timestamp(source.start_seconds)}</a> : null;
  })}</div>;
}
