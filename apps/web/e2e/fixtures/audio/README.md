# Local codec fixtures

These synthetic 250 ms, stereo 48 kHz 440 Hz tones were generated locally, without
third-party recordings. They exercise native decode, downmix and resampling of
actual WebM/Opus and M4A/AAC containers. No spoken content or credentials.

Reproduce (FFmpeg is a local fixture tool, not an application dependency):

```sh
ffmpeg -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=0.25' -ac 2 -c:a libopus tone.webm
ffmpeg -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=0.25' -ac 2 -c:a aac tone.m4a
```

Run `pnpm exec playwright test --config=playwright.voice.config.ts`. This isolated
test starts local Vite, uses a separate Chromium with fake microphone and mocks
the recognition request. WebKit separately exercises the M4A/AAC fixture at both
rates, without microphone permissions. It never contacts Tencent. These codec
checks do not claim physical-device or native Safari microphone/permission UI
coverage; those remain manual smoke boundaries.
