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

it('uploads a file, shows prepared card and deletes the meeting', async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') return { status: 204, ok: true };
    const status = init?.method === 'POST' ? 'queued' : 'ready_for_models';
    return { status: 200, ok: true, json: async () => ({ id: 'j', meeting_id: 'm', status, message: status, error_code: null }) };
  });
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  fill();
  await screen.findByRole('button', { name: 'Открыть результат' });
  expect(fetch.mock.calls[0][1]?.body).toBeInstanceOf(FormData);
  fireEvent.click(screen.getByRole('button', { name: 'Открыть результат' }));
  expect(screen.getByText('Аудио подготовлено. Локальные модели распознавания ещё не настроены')).toBeTruthy();
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

it('blocks deletion while preparation is active', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ id: 'j', meeting_id: 'm', status: 'preparing_audio', message: 'Подготовка', error_code: null }) }));
  render(<App />);
  fill();
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Подготовка'));
  fireEvent.click(screen.getByRole('button', { name: '2. Результат подготовки' }));
  expect((screen.getByRole('button', { name: 'Удалить встречу' }) as HTMLButtonElement).disabled).toBe(true);
});
