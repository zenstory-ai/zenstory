# M10 voice — all-leaf review and repairs

2026-10-06. Architecture approved by independent architect after source/test
review. Full M10 inventory reviewed: recognize/status routes, bounded request
route, credentials/signature/HTTP helper, schema/error/rate-limit contracts,
useVoiceInput lifecycle, voice client, VoiceInputButton/MessageInput production
consumer, dormant MobileChatInput parity, unit/API/browser test contracts.
This is one module result, not aggregate release approval.

## Architecture and performance

- Keep native MediaRecorder capture. Decode with a scratch OfflineAudioContext
  at the target sample rate; render exact decoded frames into one channel and
  encode PCM16 WAV. No new dependency, server transcode, hardware decoder context,
  AudioWorklet framework or duplicated format map. Small pure converter replaces
  the incorrect browser-container-to-provider label inference.
- Record session-local chunks, use actual recorder/chunk MIME, let the browser
  choose if no preferred MIME is advertised. Generation guards and AbortSignal
  fence asynchronous conversion/request steps and callbacks. Native in-flight
  decode/render cannot be forcibly stopped; cancelled work cannot continue to
  encode/submit or change UI state. Fetch cancellation is not a guarantee that an
  already-submitted remote ASR job has stopped or avoided a provider charge.
- Normal final stop releases stream/analyser AudioContext and both polling timers;
  track and clear the existing recovery timer. No indefinite 20 Hz volume polling
  after transcription. Unknown/error/empty/cancel paths also dispose handlers.
- Request/decoded/encoded limits bound memory. Two Base64 decodes remain bounded
  by the smaller provider limit; no measured bottleneck justifies a helper/API
  reshaping solely to remove one decode. Provider HTTP remains native async.

## Proven repairs and contracts

- Ten lifecycle/mobile REDs: late recognition success/error after cancel, late
  Base64 work, unmount completion, queued data/stop revival, analyser/timer leak,
  empty stop stuck recording, old error timer resetting a new recording, mobile
  release/cancel during permission. Initial 49 related cases then GREEN.
- Actual format regressions: WebM/Opus and MP4 must be converted to real WAV;
  unsupported MIME fallback must not force WebM; final WAV/Base64 limit checked
  before provider use. First import-missing RED was a setup failure, not behavioral
  evidence; `m10-format-behavior-red.log` is the valid existing-behavior RED.
- Frontend/backend use actual supported provider formats; remove WebM and FLAC
  declarations/relabeling. Sample rate is 8/16 kHz. Default client format is WAV;
  response nullable fields/status format types match backend.
- The actual SentenceRecognition contract is 60 seconds, 3 MB **after Base64**,
  and AudioDuration is already milliseconds. The app uses conservative decimal
  3,000,000 encoded characters, not a claim that the provider defines MiB.
  Boundary/+1 and 0/2430 ms tests cover it. The 55-second 16 kHz mono PCM16 WAV
  stays below the bound. Source: [Tencent SentenceRecognition](https://cloud.tencent.com/document/api/1093/35646).
- Empty Data, unsupported formats and malformed 200 success envelopes no longer
  trigger paid requests or falsely report success. Valid empty Result (silence)
  remains accepted by backend and handled by the existing UI no-result contract.
- Signed JSON equals transmitted bytes. A fixed Authorization vector generated
  independently using OpenSSL covers body/header/timestamp/TC3 signature; no
  credential or audio logging introduced. Existing auth and 60/hour user guard
  remain unchanged; public status exposes configuration boolean, never secrets.
- Desktop second click/right-click cancels pending permission rather than starting
  another request; both consumers cancel active work/hold timers when disabled.
  Earlier mobile fixture started in `requesting` before the initial touch; corrected
  test now transitions idle→hold→requesting→release, matching real behavior.
- New converter's initial throwIfAborted usage violated documented older-browser
  support. Two REDs now pass with an aborted-flag/DOMException compatibility guard.
  The API arrived later than those browsers: [MDN compatibility](https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal/throwIfAborted).

## Local evidence (separate runs, not summed as one suite)

- Six affected web files: **141 pass**, scoped three-file voice coverage **90.25%
  lines / 77.67% branches / 83.33% functions / 88.71% statements**, existing
  thresholds retained. This run predates the two legacy-signal cases; the final
  converter eight-case run separately passed after compatibility/exact-frame fixes.
- Backend voice API/contracts + subscription/voice E2E contracts: **46 pass**,
  `api.voice` coverage **95.45%**, existing 80% scoped gate passed.
- Native Chromium production converter: real synthetic WebM/Opus and M4A/AAC,
  8/16 kHz, meaningful non-silent PCM/duration. Production VoiceInputButton + real
  fake microphone captures multiple native chunks and submits RIFF/WAVE; request
  intercepted locally, no Tencent call. Five tests passed; final-source rerun log
  is `m10-chromium-source-final.log`.
- Native WebKit M4A/AAC conversion at 8/16 kHz: **two pass**. Three explicit
  out-of-scope skips are Chrome WebM/fake-microphone cases, not a passed Safari
  physical-device test. Log `m10-webkit-browser.log`.
- Scoped ESLint/Ruff, py_compile, `tsc -b`, `git diff --check` and final Vite
  application build passed. Full static org/docs/release build is not claimed here.
- Independent architecture review approved source, signing, lifecycle, both
  consumers and contracts. Its original WebKit codec gap is now covered by the
  actual two-case WebKit run. Physical Safari microphone/permission UI and older
  physical-browser devices remain manual smoke boundaries, not verified claims.
- Node local 25 vs CI 20 gap remains known. No package/production dependency
  changed. Local browser binary setup used existing Playwright; no Actions,
  provider request, push, PR, deployment or production cleanup performed.

## Remaining nonblocking observations

The frontend language parameter is still string while the only production caller
supplies zh/en and backend validates its four literals. A tighter frontend union
is optional type precision, not a proven runtime bug. Do not expand this repair
with a new recording framework, provider selection or billing/quota policy.
