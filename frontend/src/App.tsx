import { useState, type FormEvent } from 'react';

export function App() {
  const [screen, setScreen] = useState<'upload' | 'review'>('upload');
  const [message, setMessage] = useState('Обработка ещё не реализована. Задач нет.');
  const [busy, setBusy] = useState(false);

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
    setBusy(true);
    setMessage('Ожидание ответа сервера…');
    try {
      const response = await fetch('/api/meetings', { method: 'POST', body: data });
      if (response.status === 501) {
        setMessage('Загрузка и обработка пока не реализованы. Запись не сохранена, задача не создана.');
      } else {
        setMessage(`Сервер вернул HTTP ${response.status}. Каркас не поддерживает обработку результата.`);
      }
    } catch {
      setMessage('Сервер недоступен. Проверьте запуск backend.');
    } finally { setBusy(false); }
  }

  return <div className="shell">
    <header><span className="brand">ПРОТОКОЛ</span><span>Внутренняя система · локальное хранение</span></header>
    <main>
      <h1>Протокол совещания</h1>
      <p className="intro">Запись → транскрипт → проверка → утверждённый документ</p>
      <aside>Каркас прототипа. Распознавание, анализ, сохранение и экспорт пока не реализованы.</aside>
      <nav aria-label="Экраны приложения">
        <button aria-current={screen === 'upload' ? 'page' : undefined} onClick={() => setScreen('upload')}>1. Загрузка и статус</button>
        <button aria-current={screen === 'review' ? 'page' : undefined} onClick={() => setScreen('review')}>2. Проверка протокола</button>
      </nav>
      {screen === 'upload' ? <div className="columns">
        <section><h2>Новая встреча</h2>
          <form onSubmit={upload}>
            <label>Название встречи<input name="title" required placeholder="Введите название" /></label>
            <label>Дата встречи<input name="meeting_date" type="date" required /></label>
            <label>Часовой пояс (IANA)<input name="timezone" required defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone} /></label>
            <label>Файл записи<input name="file" type="file" accept=".mp4,.m4a,.mp3,.wav,.webm" required /></label>
            <p className="hint">MP4, M4A, MP3, WAV, WebM · до 200 МБ и 15 минут. Проверка длительности появится с FFmpeg.</p>
            <button className="primary" disabled={busy}>{busy ? 'Ожидание…' : 'Отправить в API-заглушку'}</button>
          </form>
        </section>
        <section><h2>Статус обработки</h2><p role="status" aria-live="polite">{message}</p>
          <p className="hint">После подключения обработчика здесь появятся состояние задачи и текущий этап.</p>
        </section>
      </div> : <section>
        <h2>Проверка протокола</h2><p>Результатов пока нет. Здесь будет доступна ручная проверка перед утверждением.</p>
        <h3>Участники и говорящие</h3><p className="empty">Метки говорящих и поля для имён появятся после диаризации.</p>
        <h3>Саммари по ключевым пунктам</h3>
        <div className="table-wrap"><table><thead><tr><th>Направление / доклад</th><th>Показатель</th><th>Проблема</th></tr></thead><tbody><tr><td colSpan={3}>Нет обработанных данных</td></tr></tbody></table></div>
        <h3>Темы и поручения</h3>
        <div className="table-wrap"><table><thead><tr><th>Поручение</th><th>Ответственный</th><th>Срок</th><th>Источник / проверка</th></tr></thead><tbody><tr><td colSpan={4}>Поручения появятся после анализа записи</td></tr></tbody></table></div>
        <h3>Транскрипт</h3><p className="empty">Реплики с временем и говорящими пока отсутствуют.</p>
        <div className="actions"><button disabled>Сохранить правки</button><button disabled>Утвердить</button><button disabled>Скачать DOCX</button><button disabled>Скачать PDF</button><button disabled>Удалить встречу</button></div>
        <p className="hint">Редактирование, утверждение, экспорт и удаление ещё не реализованы.</p>
      </section>}
    </main>
  </div>;
}
