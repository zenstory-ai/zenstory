# Agent Note: steering 的 Redis 健康缓存按 URL 失效，并在每个测试用例前复位

Status: implemented

## Problem

本地用 `pytest -n 8` 跑后端测试会随机失败或挂起，单独跑同一个用例却能通过。典型的失败用例是 `TestRound3ExceptionPathCompensation`、`test_steering_concurrency.py::test_concurrent_sessions_isolated`、`TestRound3LateSteering`。CI 上没有这个问题。

根因是 `agent/core/steering.py` 的 Redis 健康缓存在用例之间泄漏：

- `_redis_available_sync()` 把「Redis 可用」的结论存在两个模块全局变量 `_redis_health_checked_at`、`_redis_is_healthy` 里，30 秒内不再 ping。缓存不记录检查的是哪个 `REDIS_URL`。
- `test_steering_redis`、`test_round3_gap_closure`、`test_round3_steering`、`test_stream_launch_hardening` 的 fake Redis 夹具用 `monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")` 把 `get_redis_client` 换成 fake，然后直接给这两个全局变量赋值。用例结束后环境变量和 `get_redis_client` 都被还原了，缓存里的「健康」却还在。
- 开发者的 `apps/server/.env` 里有 `REDIS_URL=redis://localhost:6380/1`（这个 Redis 平时没在跑），`main.py` 的 `load_dotenv()` 会把它写进测试进程。同一个 xdist worker 上的下一个用例读到 30 秒内的「健康」，跳过 ping，直接连 6380：要么 `ConnectionRefusedError` 失败，要么在 `process_stream` 到达假 workflow 之前就出错。后一种情况下，用例里的 `await started.wait()` 永远等不到信号，整个 worker 挂起。
- CI 的测试进程没有 `REDIS_URL`（CI 只写 `.env.test`，`load_dotenv()` 不读它；真实 Redis 只通过 `ZENSTORY_TEST_REDIS_URL` 使用），`_redis_available_sync()` 第一行就返回 False，所以 CI 碰不到这个问题。

另有一个与此无关、但同样表现为「本地必失败」的环境漂移：本地共享 venv 里 fastapi 是 0.138.0（starlette 1.3.1、prefect 3.7.5），而 `requirements.txt` 锁的是 fastapi 0.123.7。新版 fastapi 在 `app.routes` 里放入 `_IncludedRouter`，`tests/test_api/test_agent_file_precondition.py` 和 `tests/test_api/test_export_worker.py` 里按路由查找端点的 `next()` 抛 `StopIteration`（被包成 `RuntimeError: coroutine raised StopIteration`），这些用例在本地必然失败，之前被误当成「Python 3.14 下的 worker 线程问题」。

## Decision

- `agent/core/steering.py` 新增模块全局 `_redis_health_url`，与健康结论一起记录检查时的 `REDIS_URL`。`_redis_available_sync()` 只有在当前 `os.getenv("REDIS_URL")` 与缓存时的地址相同、且未过 30 秒 TTL 时才复用缓存，地址不同就重新 ping。生产环境 `REDIS_URL` 不变，行为与之前相同：TTL 内只 ping 一次。
- `tests/conftest.py`：
  - 新增 autouse 夹具 `_reset_steering_redis_health`：每个用例开始前用 `monkeypatch.setattr` 把 `_redis_health_checked_at` 设为 `0.0`、`_redis_is_healthy` 设为 `False`、`_redis_health_url` 设为 `None`，用例结束后由 monkeypatch 还原。即使某个用例仍直接给这些全局变量赋值，也影响不到下一个用例。
  - `from main import app` 之后执行 `os.environ.pop("REDIS_URL", None)`，除非设置了 `ZENSTORY_TEST_KEEP_REDIS_URL=1`。开发者 `.env` 里的 `REDIS_URL` 不再进入测试，本地与 CI 的环境一致；需要真实 Redis 的用例继续用 `ZENSTORY_TEST_REDIS_URL`。
- 上述四个测试文件的 fake Redis / 内存回退夹具，改为 `monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)` 和 `monkeypatch.setattr(st, "_redis_is_healthy", False)`，不再直接赋值。
- `tests/test_agent/test_service.py` 新增 `wait_for_stream_start(started, task, timeout=5.0)`：同时等 `started` 事件和 consume 任务，任务先结束就重新抛出它的异常，5 秒内都没发生就取消任务并报 `AssertionError`。`test_service.py` 里四处 `await started.wait()` 和 `test_round3_service.py` 的 `test_cancel_before_any_content_writes_no_empty_assistant` 改用它。前置步骤失败时用例立即报出真实异常，不再挂起。
- `pytest.ini` 增加 `faulthandler_timeout = 120`（pytest 内置）：单个用例超过 120 秒就输出全部线程栈，以后再出现挂起能直接看到卡在哪里。
- 环境漂移不改代码：在本地 venv 执行 `pip install -r requirements.txt`，恢复为 fastapi 0.123.7、starlette 0.50.0、prefect 3.6.28 后，`test_agent_file_precondition.py` 与 `test_export_worker.py` 共 31 个用例全部通过，不需要另行修复。本地 venv 是 Python 3.14.5，CI 是 3.12；本次在 3.14 下复现和验证，3.12 由 CI 覆盖。

新增回归测试 `tests/test_agent/test_steering_health_cache.py`：

- fake Redis 在地址 A 下判为健康后，换到连不上的地址 B，必须重新检查并返回 False。
- 同一地址在 TTL 内只 ping 一次（保证生产行为不变）。
- 前一个用例直接给全局变量赋值、留下「健康」状态后，下一个用例在同一地址、Redis 不可达时必须得到 False（依赖 autouse 复位）。

修复前这四个用例中有三个失败（`assert True is False` 等），修复后全部通过。

## Alternatives considered

- **只在 conftest 里删掉 `REDIS_URL`，不改 steering.py**。最强理由：改动最小，生产代码一行不动，就能让本地与 CI 一致。被否：它只消除了泄漏的触发条件，缓存本身仍然不区分地址；只要有人设置 `ZENSTORY_TEST_KEEP_REDIS_URL=1` 或者在用例里 setenv 一个真实地址，fake 夹具留下的「健康」照样会被误用。按 URL 失效对生产没有代价，所以两处一起改。
- **只加 autouse 复位，保留开发者 `.env` 的 `REDIS_URL`**。最强理由：保留开发者显式配置的环境，测试更接近本地运行时。被否：大量用例默认假设没有 `REDIS_URL`（走内存 steering、内存限流），本地有、CI 没有会让两边跑出不同的代码路径，本地失败无法在 CI 复现，反之亦然；需要真实 Redis 的用例已经有 `ZENSTORY_TEST_REDIS_URL` 这条专门通道。

## Consequences

- 收益：本地 `pytest -n 8` 不再因执行顺序随机失败或挂起，结果与 CI 一致；失败时直接报出真实异常；以后再有挂起会自动输出线程栈；生产 steering 在 `REDIS_URL` 不变时行为不变。
- 代价：测试进程默认看不到 `.env` 里的 `REDIS_URL`，想在本地用真实 Redis 跑全套测试要显式设 `ZENSTORY_TEST_KEEP_REDIS_URL=1`。`test_round3_service.py` 从 `test_service.py` 导入辅助函数，两个测试模块之间多了一条依赖。本地共享 venv 被恢复为锁定版本，与锁文件不一致的手动升级会被覆盖。

## Verification

环境：macOS，Python 3.14.5，本地共享 venv；worktree 的 `apps/server/.env` 只写了 `REDIS_URL=redis://localhost:6380/1`，6380 端口确认没有服务监听，模拟开发者本地的 `.env`。

修复前基线：

- HEAD 61beb6d，`pytest tests/test_agent -n 8 --no-cov -p no:cacheprovider`：4 次里 3 次失败、1 次挂起（被 150 秒看门狗杀掉）；main 0b2637a：3 次里 1 次失败、2 次挂起。本次在 61beb6d 上再跑 1 次：1 个失败（`test_steering_concurrency.py::TestConcurrentAccess::test_concurrent_sessions_isolated`，`ConnectionRefusedError: [Errno 61]` 来自 `_redis_create_sync` 连接 6380）。
- fastapi 0.138.0 下，`test_agent_file_precondition.py` 与 `test_export_worker.py` 必失败（`StopIteration`）。

修复后（保留上述 `.env`，venv 已恢复锁定版本）：

- `test_steering_health_cache.py` 在修复前 3 个失败、修复后 4 个全部通过。
- 本包改动的 7 个测试文件串行运行（`-n 0`）：120 passed。
- `pytest tests/test_agent -n 8 --no-cov -p no:cacheprovider` 连跑 5 次：每次 1656 passed、22 skipped、0 failed；单个用例最长 1.06–1.93 秒，没有超过 30 秒的用例；整轮墙钟 29–40 秒（机器同时在跑其它并行任务）。
- 全量 `pytest -n 8 --no-cov -p no:cacheprovider` 连跑 3 次：三次都正常结束，没有挂起，每次 4953 passed、285 skipped、0 failed，整轮墙钟 184、241、372 秒（墙钟差异来自机器上同时运行的其它任务）；单个用例最长 3.62–9.50 秒，远低于 120 秒的 faulthandler 阈值。
- `ruff check agent/`：All checks passed。
