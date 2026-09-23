import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReviewControls } from './ReviewControls';
import { type MeetingResult } from './api';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const initial: MeetingResult = {
  meeting: { id: '00000000-0000-4000-8000-000000000001', title: 'test', approved_at: null },
  job: { id: 'j', meeting_id: 'm', status: 'ready', message: 'ready', error_code: null },
  analysis_completed: true, requires_review: true, summary: '', detected_language: 'kk',
  speakers: [{ id: 's', label: 'speaker_1', name: 'Спикер 1' }], utterances: [], key_points: [], topics: [],
  action_items: [{ id: 'a', topic_id: 't', text: 'test action', responsible: 'не указан', deadline_original: 'не указан', source_utterance_ids: ['u'], requires_review: true }],
};
function Harness({ value = initial }: { value?: MeetingResult }) {
  const [result, setResult] = useState(value);
  const [busy, setBusy] = useState(false);
  return <ReviewControls result={result} meetingId={result.meeting.id} busy={busy} onBusy={setBusy} onResult={setResult} />;
}
const disabled = (name: string) => (screen.getByRole('button', { name }) as HTMLButtonElement).disabled;

it('saves controlled edits without immutable fields, then explicitly approves refreshed result', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ...initial,
    speakers: [{ ...initial.speakers[0], name: 'Әлия' }],
    action_items: [{ ...initial.action_items[0], text: 'checked action', responsible: 'Болат', deadline_original: 'ертең', requires_review: false }],
  }) }).mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ...initial,
    meeting: { ...initial.meeting, approved_at: '2026-01-01T00:00:00Z' },
    action_items: [{ ...initial.action_items[0], requires_review: false }],
  }) });
  vi.stubGlobal('fetch', fetch);
  render(<Harness />);
  expect(disabled('Скачать DOCX')).toBe(true);
  expect(disabled('Утвердить')).toBe(true);
  fireEvent.change(screen.getByLabelText('Имя speaker_1'), { target: { value: 'Әлия' } });
  fireEvent.change(screen.getByLabelText('Текст поручения 1'), { target: { value: 'checked action' } });
  fireEvent.change(screen.getByLabelText('Ответственный по поручению 1'), { target: { value: 'Болат' } });
  fireEvent.change(screen.getByLabelText('Срок поручения 1'), { target: { value: 'ертең' } });
  fireEvent.click(screen.getByLabelText('Поручение 1 требует проверки'));
  expect(disabled('Утвердить')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правки' }));
  await screen.findByText('Правки сохранены. Для экспорта утвердите результат.');
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ speakers: [{ id: 's', name: 'Әлия' }],
    action_items: [{ id: 'a', text: 'checked action', responsible: 'Болат', deadline_original: 'ертең', requires_review: false }] });
  expect(fetch.mock.calls[0][1].method).toBe('PATCH');
  expect(disabled('Утвердить')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Утвердить' }));
  await screen.findByText('Результат утверждён человеком.');
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ approve: true });
  expect(disabled('Скачать PDF')).toBe(false);
});

it('blocks exports immediately on dirty edits and keeps edits after a failed save', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: { message: 'Не удалось сохранить правки.' } }) }));
  render(<Harness value={{ ...initial, meeting: { ...initial.meeting, approved_at: '2026-01-01T00:00:00Z' } }} />);
  expect(disabled('Скачать PDF')).toBe(false);
  fireEvent.change(screen.getByLabelText('Имя speaker_1'), { target: { value: 'Изменено' } });
  expect(disabled('Скачать PDF')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правки' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Не удалось сохранить правки.');
  expect((screen.getByLabelText('Имя speaker_1') as HTMLInputElement).value).toBe('Изменено');
  expect(disabled('Скачать DOCX')).toBe(true);
});

it('rejects whitespace-only edits locally and shows safe approval failures', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: { message: 'Поручения требуют проверки.' } }) });
  vi.stubGlobal('fetch', fetch);
  render(<Harness value={{ ...initial, action_items: [] }} />);
  fireEvent.change(screen.getByLabelText('Имя speaker_1'), { target: { value: '  ' } });
  expect(disabled('Сохранить правки')).toBe(true);
  expect(disabled('Утвердить')).toBe(true);
  fireEvent.change(screen.getByLabelText('Имя speaker_1'), { target: { value: 'Спикер 1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Утвердить' }));
  await screen.findByRole('alert');
  expect(disabled('Скачать PDF')).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('handles export errors without downloading partial files', async () => {
  const create = vi.fn();
  vi.stubGlobal('URL', { createObjectURL: create });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: { code: 'libreoffice_missing', message: 'LibreOffice недоступен.' } }) }));
  render(<Harness value={{ ...initial, meeting: { ...initial.meeting, approved_at: '2026-01-01T00:00:00Z' } }} />);
  fireEvent.click(screen.getByRole('button', { name: 'Скачать PDF' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'LibreOffice недоступен.');
  expect(create).not.toHaveBeenCalled();
});

it.each(['DOCX', 'PDF'])('downloads %s from the existing route with a safe filename and releases its blob', async format => {
  const blob = new Blob(['unit document']);
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => blob });
  const create = vi.fn(() => 'blob:unit'); const revoke = vi.fn();
  vi.stubGlobal('fetch', fetch);
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
  let filename = '';
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; });
  render(<Harness value={{ ...initial, meeting: { ...initial.meeting, approved_at: '2026-01-01T00:00:00Z' } }} />);
  fireEvent.click(screen.getByRole('button', { name: `Скачать ${format}` }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(blob));
  expect(fetch).toHaveBeenCalledWith(`/api/meetings/${initial.meeting.id}/exports/${format.toLowerCase()}`);
  expect(filename).toBe(`meeting-${initial.meeting.id}.${format.toLowerCase()}`);
  await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:unit'), { timeout: 2000 });
});
