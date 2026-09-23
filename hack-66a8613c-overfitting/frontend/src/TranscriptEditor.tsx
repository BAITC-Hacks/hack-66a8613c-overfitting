import { useEffect, useState } from 'react';
import { request, type MeetingResult } from './api';

const draftsOf = (result: MeetingResult) => result.utterances.map(({ id, text, speaker_id }) => ({ id, text, speaker_id }));

export function TranscriptEditor({ result, meetingId, disabled, onBusy, onDirty, onResult }: {
  result: MeetingResult; meetingId: string; disabled: boolean;
  onBusy: (value: boolean) => void; onDirty: (value: boolean) => void; onResult: (value: MeetingResult) => void;
}) {
  const [drafts, setDrafts] = useState(() => draftsOf(result));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setDrafts(draftsOf(result)); }, [result]);
  const dirty = JSON.stringify(drafts) !== JSON.stringify(draftsOf(result));
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  async function save() {
    if (disabled || saving || !dirty || drafts.some(item => !item.text.trim())) return;
    onBusy(true); setSaving(true); setError(''); setSaved(false);
    const original = draftsOf(result);
    try {
      const utterances = drafts.filter(item => JSON.stringify(item) !== JSON.stringify(original.find(u => u.id === item.id)));
      const next = await request<MeetingResult>(`/api/meetings/${encodeURIComponent(meetingId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ utterances }),
      });
      onResult(next); setSaved(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить реплики.'); }
    finally { onBusy(false); setSaving(false); }
  }

  return <div className="transcript-editor">
    <p className="hint">Правки отменяют утверждение. Саммари автоматически не пересчитывается; проверьте связанные поручения.</p>
    <fieldset disabled={disabled || saving}>
      <legend>Правки транскрипта</legend>
      {drafts.map((draft, index) => <details key={draft.id}>
        <summary>Правка реплики {index + 1}</summary>
        <label>Текст реплики {index + 1}<textarea maxLength={8000} required value={draft.text} onChange={event => {
          setSaved(false); setDrafts(items => items.map(item => item.id === draft.id ? { ...item, text: event.target.value } : item));
        }} /></label>
        <label>Говорящий реплики {index + 1}<select value={draft.speaker_id ?? ''} onChange={event => {
          setSaved(false); setDrafts(items => items.map(item => item.id === draft.id ? { ...item, speaker_id: event.target.value || null } : item));
        }}>
          <option value="">Не назначен</option>
          {result.speakers.map(s => <option key={s.id} value={s.id}>{s.name ?? s.label}</option>)}
        </select></label>
      </details>)}
      <button disabled={!dirty || drafts.some(item => !item.text.trim())} onClick={() => void save()}>
        {saving ? 'Сохранение реплик…' : 'Сохранить реплики'}
      </button>
    </fieldset>
    {dirty && <p>Есть несохранённые правки транскрипта. Сохраните их перед утверждением.</p>}
    {saved && <p role="status">Реплики сохранены. Утверждение снято; проверьте анализ и поручения.</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
