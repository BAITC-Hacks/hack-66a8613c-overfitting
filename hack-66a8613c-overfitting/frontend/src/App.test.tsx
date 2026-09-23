import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from './App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
  expect(screen.getByText('unit action')).toBeTruthy();
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
  expect(await screen.findByText('unit source')).toBeTruthy();
  expect(screen.getByText('Саммари и поручения формируются локально. Транскрипт уже сохранён.')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Удалить встречу' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText('unit action')).toBeNull();
});

it('preserves the transcript and shows analysis error without fabricated summary', async () => {
  mockAnalysis('failed', { ...analysisResult, analysis_completed: false, summary: '', topics: [], key_points: [], action_items: [] });
  render(<App />);
  fill();
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть результат' }));
  expect(await screen.findByText('unit source')).toBeTruthy();
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
