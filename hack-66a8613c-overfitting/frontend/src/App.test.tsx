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

it('shows model configuration failure without a transcript', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ id: 'j', meeting_id: 'm', status: 'failed', message: 'CUDA недоступна', error_code: 'cuda_unavailable' }) }));
  render(<App />);
  fill();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('CUDA недоступна'));
  fireEvent.click(screen.getByRole('button', { name: 'Открыть результат' }));
  expect(screen.getByText(/Код ошибки: cuda_unavailable/)).toBeTruthy();
  expect(screen.queryByRole('heading', { name: 'Транскрипт' })).toBeNull();
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
