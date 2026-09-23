import { useEffect, useState, type FormEvent } from 'react';
import { downloadExport, request, type MeetingResult } from './api';

const draftOf = (result: MeetingResult) => ({
  speakers: result.speakers.map(({ id, name }) => ({ id, name: name ?? '' })),
  action_items: result.action_items.map(({ id, text, responsible, deadline_original, requires_review }) =>
    ({ id, text, responsible, deadline_original, requires_review })),
});

export function ReviewControls({ result, meetingId, busy, onBusy, onResult }: {
  result: MeetingResult; meetingId: string; busy: boolean;
  onBusy: (busy: boolean) => void; onResult: (result: MeetingResult) => void;
}) {
  const [draft, setDraft] = useState(() => draftOf(result));
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { setDraft(draftOf(result)); }, [result]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(result));
  const approved = !!result.meeting?.approved_at;
  const needsReview = draft.action_items.some(item => item.requires_review);
  const valid = draft.speakers.every(item => item.name.trim()) && draft.action_items.every(item =>
    item.text.trim() && item.responsible.trim() && item.deadline_original.trim());

  async function patch(approve: boolean) {
    if (busy || (approve ? dirty || needsReview || approved : !dirty || !valid)) return;
    onBusy(true); setNotice(''); setError('');
    try {
      const body = approve ? { approve: true } : {
        speakers: draft.speakers.filter(item => item.name !== (result.speakers.find(s => s.id === item.id)?.name ?? '')),
        action_items: draft.action_items.filter(item => {
          const original = draftOf(result).action_items.find(action => action.id === item.id);
          return JSON.stringify(item) !== JSON.stringify(original);
        }),
      };
      const next = await request<MeetingResult>(`/api/meetings/${meetingId}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      onResult(next);
      setNotice(approve ? 'Результат утверждён. Экспорт доступен.' : 'Правки сохранены. Для экспорта утвердите результат.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить результат.'); }
    finally { onBusy(false); }
  }

  async function download(format: 'docx' | 'pdf') {
    if (!approved || dirty || busy) return;
    onBusy(true); setError(''); setNotice('');
    try { await downloadExport(meetingId, format); setNotice('Документ подготовлен для скачивания.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось скачать документ.'); }
    finally { onBusy(false); }
  }

  function save(event: FormEvent) { event.preventDefault(); void patch(false); }

  return <div className="review-controls">
    <h3>Проверка и утверждение</h3>
    <p>{approved && !dirty ? 'Результат утверждён человеком.' : 'Экспорт доступен только после сохранения правок и утверждения результата.'}</p>
    <form onSubmit={save}>
      <fieldset disabled={busy}>
        <legend>Спикеры и поручения</legend>
        <div className="speaker-fields">{draft.speakers.map((speaker, index) => <label key={speaker.id}>
          Имя {result.speakers[index].label}
          <input required maxLength={200} value={speaker.name} onChange={event => setDraft(current => ({ ...current,
            speakers: current.speakers.map(item => item.id === speaker.id ? { ...item, name: event.target.value } : item),
          }))} />
        </label>)}</div>
        {draft.action_items.map((action, index) => <details key={action.id} className="action-editor">
          <summary>Правка поручения {index + 1}</summary>
          <label>Текст поручения {index + 1}<textarea required maxLength={8000} value={action.text} onChange={event => setDraft(current => ({ ...current,
            action_items: current.action_items.map(item => item.id === action.id ? { ...item, text: event.target.value } : item),
          }))} /></label>
          <label>Ответственный по поручению {index + 1}<input required maxLength={500} value={action.responsible} onChange={event => setDraft(current => ({ ...current,
            action_items: current.action_items.map(item => item.id === action.id ? { ...item, responsible: event.target.value } : item),
          }))} /></label>
          <label>Срок поручения {index + 1}<input required maxLength={500} value={action.deadline_original} onChange={event => setDraft(current => ({ ...current,
            action_items: current.action_items.map(item => item.id === action.id ? { ...item, deadline_original: event.target.value } : item),
          }))} /></label>
          <label className="checkbox-label"><input type="checkbox" checked={action.requires_review} onChange={event => setDraft(current => ({ ...current,
            action_items: current.action_items.map(item => item.id === action.id ? { ...item, requires_review: event.target.checked } : item),
          }))} />Поручение {index + 1} требует проверки</label>
        </details>)}
        <p className="hint">Если ответственный или срок неизвестен, сохраните «не указан». Имя спикера не назначает его ответственным.</p>
        <button disabled={busy || !dirty || !valid}>Сохранить правки</button>
      </fieldset>
    </form>
    {dirty && <p className="hint">Есть несохранённые правки. Утверждение и экспорт недоступны.</p>}
    {needsReview && <p className="hint">Проверьте поручения и снимите их отметки «требует проверки» перед утверждением.</p>}
    <p className="hint">Нажимая «Утвердить», вы подтверждаете проверку саммари, тем, поручений и транскрипта. Сохранение новых правок отменяет утверждение.</p>
    <div className="actions">
      <button disabled={busy || dirty || needsReview || approved} onClick={() => void patch(true)}>Утвердить</button>
      <button disabled={busy || dirty || !approved} onClick={() => void download('docx')}>Скачать DOCX</button>
      <button disabled={busy || dirty || !approved} onClick={() => void download('pdf')}>Скачать PDF</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}
