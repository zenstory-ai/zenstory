import type { VoiceSampleRate } from './voiceApi';

function throwIfCancelled(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
}

/** Encode already-normalized mono samples as little-endian PCM16 WAV. */
export function encodeMonoWav(samples: Float32Array, sampleRate: VoiceSampleRate): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  for (const [offset, label] of [[0, 'RIFF'], [8, 'WAVE'], [12, 'fmt '], [36, 'data']] as const) {
    for (let i = 0; i < label.length; i++) view.setUint8(offset + i, label.charCodeAt(i));
  }
  view.setUint32(4, buffer.byteLength - 8, true);
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

/** Decode the browser's actual recording, then downmix/resample without live audio devices. */
export async function normalizeRecording(
  recording: Blob,
  sampleRate: VoiceSampleRate,
  signal?: AbortSignal,
): Promise<Blob> {
  throwIfCancelled(signal);
  const bytes = await recording.arrayBuffer();
  throwIfCancelled(signal);
  const decoder = new OfflineAudioContext(1, 1, sampleRate);
  const decoded = await decoder.decodeAudioData(bytes);
  throwIfCancelled(signal);
  const renderer = new OfflineAudioContext(1, Math.max(1, decoded.length), sampleRate);
  const source = renderer.createBufferSource();
  source.buffer = decoded;
  source.connect(renderer.destination);
  try {
    source.start(0);
    const mono = await renderer.startRendering();
    throwIfCancelled(signal);
    return encodeMonoWav(mono.getChannelData(0), sampleRate);
  } finally {
    source.disconnect();
  }
}
