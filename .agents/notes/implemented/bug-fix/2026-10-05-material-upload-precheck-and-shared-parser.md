# Agent Note: 素材上传先验权益再读文件，并用与 worker 相同的解码与分章做预检

Status: implemented

## Problem

开放注册后，`POST /api/v1/materials/upload` 有四个上线前必须处理的问题：

- 处理顺序是先 `await file.read()` 把整份文件（上限 100MB）读进内存、在事件循环里同步解码，然后才检查素材库权益。任何免费账号并发上传大文件就能让单 worker 的 API 进程 OOM，整站一起中断。没有 Content-Length 预检，也没有按用户限流。
- 上传接口和 worker 各用一套编码判断。API 在 chardet 置信度低时依次尝试 utf-8、gb18030，最后 `errors="ignore"`，几乎什么都能通过；worker 的 `detect_encoding` 置信度低于 0.7 就固定用 utf-8，短的 GBK 文件会在阶段0抛 `UnicodeDecodeError`，而且每次重试都要再扣一次额度。
- 分章规则只认「第X章/节」「Chapter 数字」「数字. 标题」，章回体（第X回）、卷章同一行、楔子/序章/尾声都识别不出来，结果是 0 章。0 章也照样扣额度、显示完成；「3.5亿年前…」「1、他说道…」这类正文会被误判成标题；第一个标题前的内容和少于 100 字的章节被静默丢弃。
- 没有章节数上限，大量极短章节可以把单次拆解的 LLM 调用放大一个数量级。

## Decision

- **先验权益与限流，再读请求体**：`/upload` 不再声明 `UploadFile` 参数（声明了 FastAPI 就会先解析 multipart），依赖依次是 `require_materials_library_access` 和 `require_user_rate_limit("materials_upload", 20, 3600)`；`/{novel_id}/retry` 也挂 `require_user_rate_limit("materials_retry", 20, 3600)`。通过后才检查 `Content-Length`（超过 `MAX_UPLOAD_REQUEST_BYTES` 回 413 `ERR_FILE_TOO_LARGE`；没有该头的分块请求交给全局请求体上限，因为代理可能把请求重新分块，直接拒绝会误伤正常上传），再用 `request.form(max_files=1)` 解析，并以 `file.read(MAX_FILE_SIZE + 1)` 限量读取。全局请求体上限 middleware 由另一个 PR 处理，这里不改 `main.py`。
- **上限**：`MAX_FILE_SIZE` 为 20MB（`api/materials/constants.py`，与 `apps/web/src/lib/materialUploadValidation.ts` 的 `MATERIALS_UPLOAD_MAX_BYTES` 一致）。30 万字的 UTF-8 文本约 1.2MB，20MB 留出了余量。
- **共用解码与分章**：`services/material/novel_text.py` 是唯一实现，不依赖 Prefect，API 与 worker 都能导入。`decode_novel_bytes` 的顺序是：BOM、置信度 ≥0.7 且不是拉丁单字节编码的 chardet 猜测、严格 utf-8、严格 gb18030；全部失败抛 `NovelDecodeError`，不再用 `errors="ignore"`。worker 的 `flows/utils/helpers/novel_parser.py` 只是包装这套函数，阶段0改用 `read_novel_text` 解码，不再走 `detect_encoding`。
- **分章规则**（`split_novel_text`）：识别「第X章/节/節/回」（可前置「第X卷」同一行，或包在【】里）、`Chapter 数字/英文数字/罗马数字`、`数字. 标题`（标题不能以数字开头）、楔子/序章/序言/引子/前言/尾声/后记/终章/番外。标题行不超过 40 字，且不以「。，；」结尾；「第一回合」不算标题。单独一行的卷名是分隔，不算正文。第一个标题之前的内容如果够长，存成「序章」；全文找不到任何标题则是 0 章。短于 100 字的章节并入上一章（排在最前面时并入下一章），不再丢弃。
- **上传预检**：解码、计数、分章放在 `run_in_threadpool` 里执行，写文件也是；都在扣额度之前完成。无法解码回 400 `ERR_FILE_ENCODING_UNSUPPORTED`，0 章回 400 `ERR_MATERIAL_NO_CHAPTERS`，超过 `MATERIAL_MAX_CHAPTERS_PER_NOVEL`（默认 3000，worker 环境变量可调）回 400 `ERR_MATERIAL_TOO_MANY_CHAPTERS`，这几种情况都不扣额度、不建记录。阶段0 用同样的条件抛 `MaterialPipelineError` 兜底。
- 梗概 JSON：`novel_synopsis.j2`（web_long / web_short）提示词改成「换行用 `\n` 转义」，`extract_json_from_response` 改用 `json.loads(strict=False)`，字符串内的真实换行不会再让整个阶段2失败。

## Alternatives considered

- **只把 `require_materials_library_access` 挂到 `upload_router`**。最强理由：一行改动，和其他子 router 写法一致。被否：FastAPI 会先解析 multipart、再跑依赖，挂了依赖也挡不住内存读取和解码；而且 `upload_router` 上还有 worker 用的内部下载端点（用内部 token，没有用户权益），整组挂上去会把 worker 下载也挡掉。
- **上限降到 2–4MB**（审查建议）。最强理由：更贴近 30 万字的实际体积，被滥用时的代价更小。被否：任务要求定为 20MB；UTF-16 或混有大量空白的文件在 4MB 附近就可能被误拒，而前置的权益检查、Content-Length 预检和限量读取已经把内存峰值压到单请求约 20MB。
- **上传时把文件统一转存成 UTF-8**。最强理由：worker 根本不必再判断编码。被否：要改落盘格式，已上传的原文件和重试路径仍是原始字节；共用一个严格的解码函数就能保证两边结果一致，改动更小。
- **找不到标题时把整本当成一章**。最强理由：没有标题的短篇也能拆解。被否：30 万字会变成一个超大章节，超出上下文后只会反复重试、白白消耗 token。直接拒绝并提示补上章节标题，用户不会因此被扣额度。

## Consequences

- 收益：免费账号和超大请求在读请求体前就被拦下；API 接受的文件，worker 一定能用同一编码解码，并切出相同的章节；章回体、卷章同行、楔子尾声都能识别，0 章和超出章节上限会在扣额度前被拒绝。
- 代价：上传接口的 OpenAPI 改由 `openapi_extra` 手写 multipart schema；没有 `Content-Length` 的分块上传在全局请求体上限上线前，仍可能让 Starlette 把整个请求体写入临时磁盘（内存仍受限量读取约束）。标题启发式仍可能把不超过 40 字、以「1、」开头的短句当成标题。没有任何标题的纯文本短篇现在无法上传。单章字数上限（`NOVEL_MAX_CHARACTERS`）仍然没有生效，单个超大章节的风险还在。

## Verification

`cd apps/server && pytest tests/test_api/test_materials.py tests/test_api/test_materials_retry.py tests/test_services/test_novel_text.py tests/test_flows/unit/test_llm_failure_policy.py -q`；`cd apps/web && pnpm exec vitest run src/lib/__tests__/materialUploadValidation.test.ts`。
