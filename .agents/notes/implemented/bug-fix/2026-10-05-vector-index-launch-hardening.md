# Agent Note: 向量检索上线加固——embedding 时限、重建限流单飞、元数据扁平化与安全重建

Status: implemented

## Problem

上线前审查发现向量检索链路有四个独立的放大点：

1. `ZhipuEmbedding` 构造 `ZhipuAiClient` 时只传 `api_key/base_url`，沿用 zai-sdk 默认的 300 秒读超时 + 3 次重试。每轮 `/agent/stream` 组装上下文都会经 `ContextAssembler._get_retrieved_snippets` → `hybrid_search` → `semantic_search` 同步算一次 query embedding，外层没有任何时限。Zhipu 挂起（不回包也不断连）时一条消息能卡在「正在组装上下文」约 20 分钟，并占住 asyncio 默认 executor 的线程。
2. `POST /projects/{id}/vector-index/rebuild` 只校验项目归属，没有限流也没有去重；单个用户并发打几十次就会占满同步连接池与线程池，每次都付一遍全项目 embedding 费用。
3. `_entity_to_document` 把 `file_metadata` 原样并进 Document 元数据，而 llama-index 的 `ChromaVectorStore` 默认 `flat_metadata=True`，遇到 dict/list 直接 `ValueError`。上传素材超过 2 万字时写入的 `metadata["split"] = {...}`、agent 建角色卡时带的列表都会让该文件永远进不了索引。
4. `index_project` 先 `delete_collection` 再 `from_documents`：只要任何一个文件（例如上面那种元数据）让重建失败，整个项目的语义索引就被清空。

## Decision

- `services/infra/vector_search_service.build_zhipu_client_options()` 给 `ZhipuAiClient` 显式传 `timeout=httpx.Timeout(10s, connect=3s)`、`max_retries=1`（可用 `ZHIPU_EMBEDDING_CONNECT_TIMEOUT_S` / `ZHIPU_EMBEDDING_READ_TIMEOUT_S` / `ZHIPU_EMBEDDING_MAX_RETRIES` 调整，重试次数上限钳在 1）。
- `LlamaIndexService.hybrid_search` 新增 `semantic_timeout_s`：传入时语义分支放进模块级专用线程池（`HYBRID_SEMANTIC_EXECUTOR_WORKERS`，默认 4）执行，`future.result(timeout=...)` 到时按语义失败处理，只返回词法结果。`ContextAssembler._get_retrieved_snippets` 传 `AGENT_RETRIEVAL_SEMANTIC_TIMEOUT_S`（默认 5 秒）。不传时行为不变（agent 的 `hybrid_search` 工具仍走原路径，只受客户端时限约束）。
- `_entity_to_document` 把 `extra_metadata` 里非 `str/int/float/None` 的值转成 JSON 字符串（失败退回 `str()`），dict/list 都能写入 Chroma。
- `index_project` 先在临时 collection（`{原名}_rebuild_{12位hex}`）里建好新索引；成功后在缓存锁内删除旧 collection、把临时 collection `modify(name=原名)` 接替，并清掉该项目的索引缓存。建索引期间任何异常只删除临时 collection 并原样抛出，旧索引保持不变。
- 重建端点挂 `require_user_rate_limit("vector_index_rebuild", 1, 600)`（每用户每 10 分钟 1 次，超限 429），并用 `services/infra/single_flight_lock` 按项目单飞：Redis 可用时 `SET NX EX` + Lua 比对 token 释放，否则退回进程内存（后端选择与 `RATE_LIMIT_BACKEND` 同口径）。锁 TTL 1800 秒兜底；同一项目已有重建在跑时回 409 `ERR_RESOURCE_CONFLICT`；后台任务在 `finally` 里释放锁。

## Alternatives considered

- **外层 deadline 用 `asyncio.wait_for` 包住整个 `assemble_context`**。最强理由：一处改动覆盖上下文组装里所有可能变慢的步骤。被否：`assemble_context` 在 PG 上跑在 `asyncio.to_thread` 里，`wait_for` 超时只会放弃等待、线程继续卡着占 executor；而且超时后拿不到任何上下文，比「只丢语义片段」退化得多。
- **重建端点直接下线或改成 admin 专用**。最强理由：前端没有任何地方调用它，删掉就没有攻击面。被否：它是用户自助修复索引的唯一入口，运维排障也会用到；限流 + 单飞足以把成本压到每用户每 10 分钟一次。
- **元数据只保留白名单键（entity_type/entity_id/title/parent_id）**。最强理由：元数据体积最小，不会把任意用户数据塞进 Chroma。被否：现有检索结果展示与过滤会读 `file_metadata` 里的其它字段，白名单会悄悄丢信息；转 JSON 字符串既能写入又不丢内容。
- **重建前先把 embedding 全算好再删旧 collection**。最强理由：不依赖 Chroma 的改名能力。被否：要绕过 `VectorStoreIndex.from_documents` 自己做切块与批量 embedding，复制 llama-index 的内部流程；chromadb 1.5 的 `Collection.modify(name=...)` 已能原子改名。

## Consequences

- 收益：Zhipu 故障时单次请求最多多等约 5 秒就退回词法检索，不再卡住整轮对话；重建端点的成本有上界；带嵌套元数据的文件能进索引；重建失败不再清空项目索引。
- 代价：语义检索超时后，后台线程仍会在客户端时限（最长约 2 × 10 秒）内跑完才释放，专用线程池满时新的语义请求直接排队超时、只走词法。重建期间同一项目短暂存在两份 collection（磁盘占用翻倍）；进程在建好临时 collection 后、改名前崩溃会留下一个 `_rebuild_` 后缀的孤儿 collection，需要手动清理。嵌套元数据以 JSON 字符串存储，无法在 Chroma 侧按其内部字段过滤。重建限流按「请求」计数，被 409 拒绝的请求同样占用当次窗口。

## Verification

`cd apps/server && venv/bin/pytest tests/test_services/test_vector_search_launch_hardening.py tests/test_api/test_vector_index_rebuild_guard.py -q`：覆盖客户端时限参数、语义超时退回词法（0.2 秒时限、2 秒内返回词法结果）、assembler 传递时限、嵌套元数据可索引、重建失败保留旧索引且无临时 collection 残留、成功重建替换内容、重建端点 429/409 与任务失败时释放锁。
