export type Job = {
  id: string;
  meeting_id: string;
  status: 'queued' | 'preparing_audio' | 'ready_for_models' | 'transcribing' | 'diarizing' | 'saving_transcript' | 'analyzing' | 'ready' | 'failed';
  stage?: string | null;
  message: string;
  error_code: string | null;
};

export type MeetingResult = {
  meeting: { id: string; title: string; approved_at: string | null };
  job: Job;
  detected_language: string | null;
  speakers: { id: string; label: string; name: string | null }[];
  utterances: { id: string; speaker_id: string | null; start_seconds: number; end_seconds: number; text: string; requires_review: boolean }[];
  summary: string;
  analysis_completed: boolean;
  requires_review: boolean;
  key_points: { id: string; direction: string; metric: string; problem: string; source_utterance_ids: string[]; requires_review: boolean }[];
  topics: { id: string; position: number; title: string; summary: string; source_utterance_ids: string[]; requires_review: boolean }[];
  action_items: { id: string; topic_id: string; text: string; responsible: string; deadline_original: string; deadline_date?: string | null; source_utterance_ids: string[]; requires_review: boolean }[];
};

export async function downloadExport(meetingId: string, format: 'docx' | 'pdf') {
  const response = await fetch(`/api/meetings/${encodeURIComponent(meetingId)}/exports/${format}`);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail?.message === 'string' ? body.detail.message : `Не удалось скачать документ (HTTP ${response.status}).`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  // Never use user-provided meeting titles or response filenames as download paths.
  link.download = `meeting-${meetingId.replace(/[^a-zA-Z0-9-]/g, '').slice(0, 36)}.${format}`;
  document.body.append(link);
  try { link.click(); } finally {
    link.remove();
    // Let the browser begin consuming the blob before releasing it.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof body?.detail?.message === 'string'
      ? body.detail.message : `Не удалось выполнить запрос (HTTP ${response.status}).`);
  }
  return body as T;
}

export function active(job: Job | null): boolean {
  return !!job && (['queued', 'preparing_audio', 'transcribing', 'diarizing', 'saving_transcript', 'analyzing'].includes(job.status)
    || (job.status === 'ready_for_models' && job.stage === 'models'));
}

export function watchStatus(meetingId: string, onJob: (job: Job) => void, onError: (message: string) => void) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function poll() {
    try {
      const job = await request<Job>(`/api/meetings/${meetingId}/status`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      onJob(job);
      if (!active(job)) return;
    } catch (error) {
      if (controller.signal.aborted) return;
      onError(error instanceof Error ? error.message : 'Не удалось получить статус. Повторяем запрос…');
    }
    if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
  }
  void poll();
  return () => { controller.abort(); clearTimeout(timer); };
}
