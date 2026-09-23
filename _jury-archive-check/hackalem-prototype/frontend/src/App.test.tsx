import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function fill() {
  fireEvent.change(screen.getByLabelText('Название встречи'), { target: { value: 'Test' } });
  const input = screen.getByLabelText('Файл записи') as HTMLInputElement;
  const file = new File(['unit bytes'], 'test.wav', { type: 'audio/wav' });
  fireEvent.change(input, { target: { files: [file] } });
  // jsdom does not propagate synthetic FileList into native FormData.
  const NativeFormData = globalThis.FormData;
  vi.stubGlobal('FormData', class extends NativeFormData {
    constructor(form?: HTMLFormElement) { super(form); if (form) this.set('file', file); }
  });
  fireEvent.submit(input.closest('form')!);
}

it('uploads a file, shows saved transcript and deletes the meeting', async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') return { status: 204, ok: true };
    if (_url.endsWith('/result')) return { status: 200, ok: true, json: async () => ({ detected_language: 'kk',
      speakers: [{ id: 's', label: 'speaker_1', name: 'Спикер 1' }],
      utterances: [{ id: 'u', speaker_id: 's', start_seconds: 1, end_seconds: 2.5, text: 'unit fixture', requires_review: true }] }) };
    const status = init?.method === 'POST' ? 'queued' : 'ready';
    return { status: 200, ok: true, json: async () => ({ id: 'j', meeting_id: 'm', status, message: status, error_code: null }) };
  });
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  fill();
  await screen.findByRole('button', { name: 'Открыть результат' });
  expect(fetch.mock.calls[0][1]?.body).toBeInstanceOf(FormData);
  fireEvent.click(screen.getByRole('button', { name: 'Открыть результат' }));
  expect(await screen.findByText('unit fixture')).toBeTruthy();
  expect(screen.getByText('Спикер 1')).toBeTruthy();
  expect(screen.getByText('00:01.0 — 00:02.5')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Удалить встречу' }));
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Встреча и её локальные файлы удалены.'));
  expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true);
});

it('displays upload errors without polling', async () => {
  const fetch = vi.fn().mockResolvedValue({ status: 415, ok: false, json: async () => ({ detail: { message: 'Неподдерживаемый файл' } }) });
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  fill();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Неподдерживаемый файл'));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('blocks deletion while transcription is active', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ id: 'j', meeting_id: 'm', status: 'transcribing', message: 'Распознавание', error_code: null }) }));
  render(<App />);
  fill();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Распознавание'));
  fireEvent.click(screen.getByRole('button', { name: '2. Результат подготовки' }));
  expect((screen.getByRole('button', { name: 'Удалить встречу' }) as HTMLButtonElement).disabled).toBe(true);
});

it('shows model configuration failure without fabricated utterances', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ status: 200, ok: true, json: async () => url.endsWith('/result')
    ? { analysis_completed: false, speakers: [], utterances: [] }
    : { id: 'j', meeting_id: 'm', status: 'failed', message: 'CUDA недоступна', error_code: 'cuda_unavailable' } })));
  render(<App />);
  fill();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('CUDA недоступна'));
  fireEvent.click(screen.getByRole('button', { name: 'Открыть результат' }));
  expect(screen.getByText(/Код ошибки: cuda_unavailable/)).toBeTruthy();
  expect(await screen.findByText('Сохранённых реплик нет.')).toBeTruthy();
});

it('reports result request failure and allows a retry', async () => {
  let fail = true;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/result')) {
      if (fail) return { status: 503, ok: false, json: async () => ({}) };
      return { status: 200, ok: true, json: async () => ({ detected_language: null, speakers: [], utterances: [] }) };
    }
    return { status: 200, ok: true, json: async () => ({ id: 'j', meeting_id: 'm', status: 'ready', message: 'Готово', error_code: null }) };
  }));
  render(<App />);
  fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  const retry = await screen.findByRole('button', { name: 'Повторить загрузку транскрипта' });
  fail = false;
  fireEvent.click(retry);
  expect(await screen.findByText('Распознавание завершено. Речевые реплики не обнаружены.')).toBeTruthy();
});

const analysisResult = {
  meeting: { id: 'm', title: 'Test', approved_at: null as string | null },
  analysis_completed: true, requires_review: true, summary: 'unit summary', detected_language: 'ru',
  speakers: [{ id: 's', label: 'speaker_1', name: 'Спикер 1' }],
  utterances: [{ id: 'u', speaker_id: 's', start_seconds: 1, end_seconds: 2, text: 'unit source', requires_review: false }],
  key_points: [{ id: 'k', direction: 'unit direction', metric: 'не указан', problem: 'unit problem', source_utterance_ids: ['u'], requires_review: true }],
  topics: [{ id: 't', position: 1, title: 'unit topic', summary: 'unit description', source_utterance_ids: ['u'], requires_review: true }],
  action_items: [{ id: 'a', topic_id: 't', text: 'unit action', responsible: 'не указан', deadline_original: 'не указан', source_utterance_ids: ['u'], requires_review: true }],
};

function mockAnalysis(status = 'ready', value = analysisResult) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => url.endsWith('/result') ? value
    : { id: 'j', meeting_id: 'm', status, message: status, error_code: status === 'failed' ? 'ollama_unavailable' : null } })));
}

it('renders summary, key points, topics and actions above linked transcript', async () => {
  mockAnalysis();
  render(<App />);
  fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  expect(await screen.findByText('unit summary')).toBeTruthy();
  expect(screen.getAllByRole('columnheader').map(cell => cell.textContent)).toEqual([
    'Направление / доклад', 'Показатель', 'Проблема', 'Поручение', 'Ответственный', 'Срок',
  ]);
  expect(screen.getByRole('heading', { name: 'Тема 1 — unit topic' })).toBeTruthy();
  expect(screen.getByText('unit action', { selector: 'td' })).toBeTruthy();
  expect(screen.getAllByText('Требует проверки').length).toBe(4);
  const headings = screen.getAllByRole('heading').map(item => item.textContent);
  expect(headings.indexOf('Саммари по ключевым пунктам')).toBeLessThan(headings.indexOf('Транскрипт'));
  const link = screen.getAllByRole('link', { name: 'Реплика 00:01.0' })[0];
  expect(link.getAttribute('href')).toBe('#utterance-u');
  expect(document.getElementById('utterance-u')?.textContent).toContain('unit source');
});

it('keeps polling and blocks deletion while analysis is running', async () => {
  mockAnalysis('analyzing', { ...analysisResult, analysis_completed: false, summary: '', topics: [], key_points: [], action_items: [] });
  render(<App />);
  fill();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('analyzing'));
  fireEvent.click(screen.getByRole('button', { name: '2. Результат подготовки' }));
  expect(await screen.findByText('unit source', { selector: 'p' })).toBeTruthy();
  expect(screen.getByText('Саммари и поручения формируются локально. Транскрипт уже сохранён.')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Удалить встречу' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText('unit action')).toBeNull();
});

it('preserves the transcript and shows analysis error without fabricated summary', async () => {
  mockAnalysis('failed', { ...analysisResult, analysis_completed: false, summary: '', topics: [], key_points: [], action_items: [] });
  render(<App />);
  fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  expect(await screen.findByText('unit source', { selector: 'p' })).toBeTruthy();
  expect(screen.getByText(/Код ошибки: ollama_unavailable/)).toBeTruthy();
  expect(screen.queryByRole('heading', { name: 'Саммари по ключевым пунктам' })).toBeNull();
});

it('does not invent actions when the model returned an empty list', async () => {
  mockAnalysis('ready', { ...analysisResult, action_items: [] });
  render(<App />);
  fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  expect(await screen.findByText('Явные поручения по теме не выделены.')).toBeTruthy();
  expect(screen.queryByRole('columnheader', { name: 'Ответственный' })).toBeNull();
});

it('updates displayed speaker names from PATCH without reloading the result', async () => {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => ({ ok: true, status: 200, json: async () =>
    init?.method === 'PATCH' ? { ...analysisResult, speakers: [{ id: 's', label: 'speaker_1', name: 'Әлия' }] }
      : url.endsWith('/result') ? analysisResult : { id: 'j', meeting_id: 'm', status: 'ready', message: 'ready', error_code: null },
  }));
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  fireEvent.change(await screen.findByLabelText('Имя speaker_1'), { target: { value: 'Әлия' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правки' }));
  expect(await screen.findByText('Әлия', { selector: 'strong' })).toBeTruthy();
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/result')).length).toBe(1);
});

it('seeks the local player only after source or timestamp clicks', async () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  const scroll = vi.fn();
  mockAnalysis(); render(<App />); fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  const player = await screen.findByLabelText('Запись встречи') as HTMLVideoElement;
  expect(player.getAttribute('src')).toBe('/api/meetings/m/media');
  expect(player.autoplay).toBe(false);
  expect(play).not.toHaveBeenCalled();
  document.getElementById('utterance-u')!.scrollIntoView = scroll;
  fireEvent.click(screen.getAllByRole('link', { name: 'Реплика 00:01.0' })[0]);
  expect(player.currentTime).toBe(1);
  expect(play).toHaveBeenCalledTimes(1);
  expect(scroll).toHaveBeenCalled();
  player.currentTime = 0;
  fireEvent.click(screen.getByRole('button', { name: 'Воспроизвести реплику 00:01.0' }));
  expect(player.currentTime).toBe(1);
  expect(play).toHaveBeenCalledTimes(2);
  play.mockRejectedValueOnce(new Error('unsupported'));
  fireEvent.click(screen.getByRole('button', { name: 'Воспроизвести реплику 00:01.0' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Воспроизведение недоступно'));
});

it('saves transcript and speaker removal, revokes approval and keeps the displayed result fresh', async () => {
  const approved = { ...analysisResult, meeting: { ...analysisResult.meeting, approved_at: '2026-09-23T00:00:00Z' } };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => ({ ok: true, status: 200, json: async () =>
    init?.method === 'PATCH' ? { ...analysisResult, utterances: [{ ...analysisResult.utterances[0], text: 'Түзетілген мәтін', speaker_id: null }] }
      : url.endsWith('/result') ? approved : { id: 'j', meeting_id: 'm', status: 'ready', message: 'ready', error_code: null },
  }));
  vi.stubGlobal('fetch', fetch); render(<App />); fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  expect((await screen.findByRole('button', { name: 'Скачать DOCX' }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(screen.getByLabelText('Текст реплики 1'), { target: { value: 'Түзетілген мәтін' } });
  fireEvent.change(screen.getByLabelText('Говорящий реплики 1'), { target: { value: '' } });
  expect((screen.getByRole('button', { name: 'Скачать DOCX' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить реплики' }));
  expect(await screen.findByText('Түзетілген мәтін', { selector: 'p' })).toBeTruthy();
  expect(screen.getByText('Спикер не определён', { selector: 'strong' })).toBeTruthy();
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/result'))).toHaveLength(1);
  const body = fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!.body as string;
  expect(JSON.parse(body)).toEqual({ utterances: [{ id: 'u', text: 'Түзетілген мәтін', speaker_id: null }] });
  expect((screen.getByRole('button', { name: 'Скачать DOCX' }) as HTMLButtonElement).disabled).toBe(true);
});

it('keeps transcript drafts after a safe save error and rejects empty text locally', async () => {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => init?.method === 'PATCH'
    ? { ok: false, status: 422, json: async () => ({ detail: { message: 'Не удалось сохранить реплики.' } }) }
    : { ok: true, status: 200, json: async () => url.endsWith('/result') ? analysisResult
      : { id: 'j', meeting_id: 'm', status: 'ready', message: 'ready', error_code: null } });
  vi.stubGlobal('fetch', fetch); render(<App />); fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  fireEvent.change(await screen.findByLabelText('Текст реплики 1'), { target: { value: '   ' } });
  expect((screen.getByRole('button', { name: 'Сохранить реплики' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Текст реплики 1'), { target: { value: 'Правка' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить реплики' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Не удалось сохранить реплики.');
  expect((screen.getByLabelText('Текст реплики 1') as HTMLTextAreaElement).value).toBe('Правка');
});
