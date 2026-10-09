# Agent Note: TXT 导出保留首段缩进；项目列表按最近一次文件编辑排序

Status: implemented

## Problem

新用户审计 #33 有两处「数据看起来不可信」：

- `export_service.export_drafts_to_txt` 对每章正文调用 `str.strip()`。Python 把全角空格 U+3000 当空白，于是每章首段的「　　」段首缩进被削掉，导出的 TXT 每章第一段顶格，其余段落有缩进，和编辑器里看到的不一样。
- `GET /api/v1/projects` 直接返回 `Project.updated_at`。写章节只更新 `File.updated_at`，不碰 project 行，所以项目卡上的「x 分钟前」停在创建项目或改项目名的时候，工作台按 `updated_at` 排序也不跟着最近写的书走。

## Decision

- 导出时每章正文改为 `re.sub(r'^(?:[ \t　]*\r?\n)+', '', content).rstrip()`（`_trim_chapter_body`）：只去掉开头的整行空行和末尾空白，首段行首的全角或半角缩进原样保留。章节标题、分隔符和排序不变。
- `get_projects` 用一条 group-by 查询算出每个项目未删除文件的 `max(File.updated_at)`，返回的 `updated_at` 取它和 `Project.updated_at` 中较晚的一个。修改发生在 `Project.model_validate(project.model_dump())` 生成的脱离 session 的副本上，不写库：project 行自己的 `updated_at` 仍只表示项目元数据的修改时间。前端 `DashboardHome` / `DashboardProjects` 的排序和 `formatRelativeTime` 不用改。

## Alternatives considered

- **写文件时顺手更新 `Project.updated_at`**。最强理由：一次写入，读接口不用多查。被否：每次自动保存都要多锁一行 project，和项目级结构写入（建文件夹、快照恢复按 Project → File 顺序加锁）争锁；网页保存、agent 工具、上传、导入、快照恢复等每条写文件路径都要改，漏一条就又不准。
- **导出时只去掉半角空白（`strip(' \t\r\n')`）**。最强理由：改动最小。被否：正文开头如果是全角空格加换行组成的「空行」，仍会留在导出里；明确只删整行空行、保留首行缩进更贴近作者的排版。
- **前端自己拉每个项目的文件列表算最近编辑时间**。最强理由：后端不动。被否：项目卡列表每个项目都要多一次请求，项目多时首屏变慢；服务端一条聚合查询就够了。

## Consequences

- 收益：导出的每章首段和编辑器里一样有段首缩进；项目卡显示最近一次写作时间，最近写的书排在前面。
- 代价：`GET /projects` 多一条按 `project_id` 分组的聚合查询（`file.project_id` 有索引）。返回的 `updated_at` 语义从「项目行修改时间」变为「项目最近活动时间」，任何按这个字段判断项目元数据是否被改过的调用方会看到更新的时间（目前前端只用于排序和展示）。`test_export_service.py` 里断言「行首空格被削掉」的旧用例改为断言保留缩进。

## Verification

- pytest `tests/test_services/test_export_first_paragraph_indent.py`：首段保留 U+3000 缩进，开头的空行（含只有空格的行）和末尾空白被去掉，多章之间分隔符不变。`tests/test_api/test_projects_activity_time.py`：改文件后列表里该项目的 `updated_at` 等于这次文件保存时间并排在前面，已删除文件不算活动，数据库里 project 行的 `updated_at` 保持三天前不变。改动前两个用例都失败。
