import { afterEach, expect, it, vi } from 'vitest';
import { request, watchStatus, type Job } from './api';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const job = (status: Job['status']) => ({ id: 'j', meeting_id: 'm', status, message: status, error_code: null });
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

it('polls at two seconds and stops at ready_for_models', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValueOnce(response(job('preparing_audio'))).mockResolvedValue(response(job('ready_for_models')));
  vi.stubGlobal('fetch', fetch);
  const update = vi.fn();
  const stop = watchStatus('m', update, vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1999);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(update.mock.calls[1][0].status).toBe('ready_for_models');
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(2);
  stop();
});

it('retries a network failure and stops when failed', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockRejectedValueOnce(new Error('Connection unavailable')).mockResolvedValue(response(job('failed')));
  vi.stubGlobal('fetch', fetch);
  const error = vi.fn();
  const stop = watchStatus('m', vi.fn(), error);
  await vi.advanceTimersByTimeAsync(0);
  expect(error).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(2);
  stop();
});

it('cancels polling on unmount', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValue(response(job('queued')));
  vi.stubGlobal('fetch', fetch);
  const stop = watchStatus('m', vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  stop();
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
});

it('shows safe API errors and accepts empty deletion responses', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 413, json: async () => ({ detail: { message: 'Размер файла превышает 200 МБ.' } }) })
    .mockResolvedValueOnce({ ok: true, status: 204 });
  vi.stubGlobal('fetch', fetch);
  await expect(request('/api/meetings')).rejects.toThrow('Размер файла превышает 200 МБ.');
  await expect(request('/api/meetings/m', { method: 'DELETE' })).resolves.toBeUndefined();
});
