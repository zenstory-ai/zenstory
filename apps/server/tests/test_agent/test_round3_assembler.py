"""第三轮深度 review 回归测试：上下文组装（assembler / budget / prioritizer）。

覆盖四条确认缺陷：
- #2  文件清单硬编码 5 种类型，script/document 被整体丢弃，
      剧本项目每轮被告知「正文: (暂无)」
- #8  文件清单头部不受 max_tokens 约束，把条目预算压到 512 下限，
      用户附加文件与引用文本被静默丢弃
- #19 焦点文件是 character/lore/snippet 时 is_focus 被丢弃，焦点保护整条失效
- #34 _get_files_by_types 没有 SQL LIMIT，每次请求把全部角色/设定正文读进内存

这些测试都必须在「只回退 agent/context/ 三个文件」的情况下变红。
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from agent.constants import CONTENT_FILE_TYPES, INVENTORY_FILE_TYPES
from agent.context.assembler import ContextAssembler
from agent.schemas.context import ContextPriority
from agent.utils.token_utils import estimate_text_tokens
from models import File, Project, User

MARKER_ATTACHED = "【附加文件独有标记ATTACHEDMARKER】"
MARKER_QUOTE = "【用户引用独有标记QUOTEMARKER】"
MARKER_FOCUS = "【焦点章独有标记FOCUSMARKER】"


@pytest.fixture
def owner(db_session: Session) -> User:
    user = User(
        email="round3_assembler@example.com",
        username="round3assembler",
        hashed_password="hashed",
        name="Round3 Assembler",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def make_project(db_session: Session, owner: User):
    def _make(project_type: str = "novel", **kwargs) -> Project:
        project = Project(
            name=kwargs.pop("name", "Round3 项目"),
            owner_id=owner.id,
            project_type=project_type,
            **kwargs,
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)
        return project

    return _make


@pytest.fixture
def make_file(db_session: Session, owner: User):
    def _make(project: Project, title: str, file_type: str, content: str = "", order: int = 0, **kwargs) -> File:
        file = File(
            title=title,
            content=content,
            file_type=file_type,
            project_id=project.id,
            user_id=owner.id,
            order=order,
            **kwargs,
        )
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        return file

    return _make


# ---------------------------------------------------------------------------
# #2 script / document 必须出现在文件清单里
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScriptAndDocumentInventory:
    def test_script_episodes_visible_in_inventory(self, db_session, owner, make_project, make_file):
        """短剧分集（file_type=script）必须进清单，且不得输出「正文: (暂无)」。"""
        project = make_project(project_type="screenplay")
        for i in range(1, 6):
            make_file(project, f"第{i}集 觉醒", "script", content="剧本正文。" * 50, order=i)

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, project.id)  # noqa: SLF001

        assert len(inventory["script"]) == 5
        assert "script" in INVENTORY_FILE_TYPES

        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            query="写第6集",
            max_tokens=6000,
        )

        assert "正文: (暂无)" not in result.context
        assert "第3集 觉醒" in result.context
        assert "第5集 觉醒" in result.context

    def test_document_files_visible_in_inventory(self, db_session, owner, make_project, make_file):
        """create_file 的默认类型 document 也必须可见，否则 AI 看不见自己刚建的文件。"""
        project = make_project()
        make_file(project, "人物小传", "document", content="文档内容")
        make_file(project, "灵感碎片", "snippet", content="碎片内容")

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, project.id)  # noqa: SLF001

        assert [f["title"] for f in inventory["document"]] == ["人物小传"]
        assert [f["title"] for f in inventory["snippet"]] == ["灵感碎片"]

        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            max_tokens=6000,
        )
        assert "人物小传" in result.context
        assert "灵感碎片" in result.context

    def test_unknown_file_type_is_not_silently_dropped(self, db_session, owner, make_project, make_file):
        """将来新增的类型也必须兜底展示，而不是像 script 当年那样整类消失。"""
        project = make_project()
        make_file(project, "未来类型文件", "storyboard", content="x")

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, project.id)  # noqa: SLF001
        assert [f["title"] for f in inventory.get("storyboard", [])] == ["未来类型文件"]

        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            max_tokens=4000,
        )
        assert "未来类型文件" in result.context

    def test_chapter_gap_hint_covers_script(self, db_session, owner, make_project, make_file):
        """章节缺口提醒必须比对 outline vs (draft ∪ script)，只比 draft 时对短剧恒为空。"""
        project = make_project(project_type="screenplay")
        for i in (1, 2, 3):
            make_file(project, f"第{i}集大纲", "outline", content="大纲", order=i)
        for i in (1, 3):
            make_file(project, f"第{i}集", "script", content="正文", order=i)

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, project.id)  # noqa: SLF001
        hints = "\n".join(assembler._render_chapter_gap_hints(inventory))  # noqa: SLF001

        assert "章节一致性提醒" in hints
        assert "缺少正文：2" in hints

    def test_focus_script_rendered_as_content_not_outline(self, db_session, owner, make_project, make_file):
        """script 的详情必须落在【正文详情】而不是【大纲详情】。"""
        project = make_project(project_type="screenplay")
        episode = make_file(project, "第1集", "script", content="这是分集正文。" * 20, order=1)

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            focus_file_id=episode.id,
            max_tokens=4000,
        )

        assert "【正文详情】" in result.context
        assert "script" in CONTENT_FILE_TYPES


# ---------------------------------------------------------------------------
# #8 header 必须纳入 token 预算，CRITICAL 条目要有保底
# ---------------------------------------------------------------------------


def _build_large_project(make_project, make_file, chapters: int = 60):
    project = make_project(
        summary="这是一个都市异能长篇小说的项目简介。" * 60,
        current_phase=f"正在写第 {chapters} 章",
        writing_style="第三人称限制视角。",
        notes="注意保持主角性格一致。" * 60,
    )
    drafts = []
    for i in range(1, chapters + 1):
        make_file(project, f"第{i}章大纲：风起云涌", "outline", content=f"第{i}章大纲内容。" * 20, order=i)
        drafts.append(
            make_file(
                project,
                f"第{i}章 风起云涌",
                "draft",
                content=f"第{i}章正文。" + "他缓缓抬起头，望向远处的天际线。" * 40,
                order=i,
            )
        )
    for i in range(40):
        make_file(make_project_noop := project, f"角色{i:02d}", "character", content=f"角色{i}设定。" * 10, order=i)
        make_file(make_project_noop, f"设定{i:02d}", "lore", content=f"设定{i}说明。" * 10, order=i)
    return project, drafts


@pytest.mark.unit
class TestInventoryHeaderBudget:
    def test_attached_file_and_quote_survive_large_project(
        self, db_session, owner, make_project, make_file
    ):
        """长篇项目下，用户手动附加的文件与手动选中的引用文本不得被静默丢弃。"""
        project, drafts = _build_large_project(make_project, make_file, chapters=60)
        focus, attached = drafts[-1], drafts[-2]
        focus.content = MARKER_FOCUS + focus.content
        attached.content = MARKER_ATTACHED + attached.content
        db_session.add(focus)
        db_session.add(attached)
        db_session.commit()

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            query="继续写第60章",
            focus_file_id=focus.id,
            attached_file_ids=[attached.id],
            text_quotes=[{"text": MARKER_QUOTE + "这段是用户选中的引用。" * 20, "fileTitle": "第59章"}],
            max_tokens=6000,
        )

        assert MARKER_FOCUS in result.context, "焦点文件被丢弃"
        assert MARKER_ATTACHED in result.context, "用户附加文件被丢弃"
        assert MARKER_QUOTE in result.context, "用户引用文本被丢弃"
        assert any(item["metadata"].get("attached") for item in result.items)
        assert any(item["type"] == "quote" for item in result.items)

    def test_assembled_block_respects_max_tokens(self, db_session, owner, make_project, make_file):
        """整块组装结果（含 header）必须落在 max_tokens 之内。"""
        project, drafts = _build_large_project(make_project, make_file, chapters=60)

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            query="继续写第60章",
            focus_file_id=drafts[-1].id,
            max_tokens=6000,
        )

        assert result.token_estimate <= 6000

    def test_inventory_rendering_respects_token_budget(self, db_session, owner, make_project, make_file):
        """文件清单段按额度逐条填充，装不下时显式提示省略数量。"""
        project = make_project()
        for i in range(1, 61):
            make_file(project, f"第{i}章大纲", "outline", content="大纲", order=i)
            make_file(project, f"第{i}章", "draft", content="正文", order=i)

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, project.id)  # noqa: SLF001

        unbounded = "\n".join(assembler._render_file_inventory(inventory, None))  # noqa: SLF001
        bounded = "\n".join(assembler._render_file_inventory(inventory, 600))  # noqa: SLF001

        assert estimate_text_tokens(unbounded) > 600
        assert estimate_text_tokens(bounded) <= 600 * 1.2
        assert "省略" in bounded
        # 头尾都要保留：第 1 章证明开头存在，最后一章是模型接下来要续写的
        assert "第1章" in bounded
        assert "第60章" in bounded

    def test_oversized_project_status_is_truncated(self, db_session, owner, make_project):
        """项目简介/备注同属 header，必须一起受预算约束。"""
        project = make_project(
            summary="简介" * 2000,
            notes="备注" * 2000,
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            max_tokens=2000,
        )

        assert result.token_estimate <= 2000
        assert "已截断" in result.context

    def test_items_render_id_and_explicit_completeness(
        self, db_session, owner, make_project, make_file
    ):
        """生产事故回归：条目只给标题 + 结尾裸 "..."，模型分不清是否全文，反复重读。"""
        from agent.context.assembler import FULL_TEXT_CONTEXT_NOTICE

        project = make_project()
        focus = make_file(
            project, "第9章", "draft", content="他抬起头。\n\n远处有光。" * 600, order=9
        )
        hero = make_file(project, "林小雨", "character", content="主角。\n冷静。", order=1)

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            focus_file_id=focus.id,
            max_tokens=2000,
        )

        lines = result.context.splitlines()
        assert FULL_TEXT_CONTEXT_NOTICE in lines

        focus_item = next(item for item in result.items if item["id"] == focus.id)
        assert focus_item["metadata"]["truncated"] is True
        shown = focus_item["metadata"]["shown_chars"]
        total = focus_item["metadata"]["original_chars"]
        assert total == len(focus.content) and shown < total
        focus_heading = next(line for line in lines if f"(id={focus.id}) ← 当前焦点" in line)
        assert (
            f'[已截断：显示 {shown}/{total} 字；全文用 query_files(id="{focus.id}")]'
            in focus_heading
        )

        # 没有焦点挤占预算时，角色卡整份入选并标注 [全文]
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            max_tokens=2000,
        )
        lines = result.context.splitlines()
        hero_heading = next(line for line in lines if line.startswith(f"林小雨 (id={hero.id})"))
        assert hero_heading.endswith("[全文]")
        # 正文保留换行，不被压成一行
        assert "主角。\n冷静。" in result.context


# ---------------------------------------------------------------------------
# #19 焦点保护必须覆盖所有文件类型
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFocusAcrossFileTypes:
    @pytest.mark.parametrize("file_type", ["character", "lore", "snippet", "outline", "draft", "script", "document"])
    def test_is_focus_propagated_for_every_type(self, db_session, owner, make_project, make_file, file_type):
        project = make_project()
        file = make_file(project, f"焦点-{file_type}", file_type, content="内容")

        assembler = ContextAssembler()
        item = assembler._file_to_context_item(file, is_focus=True)  # noqa: SLF001

        assert item.is_focus is True, f"{file_type} 分支丢掉了 is_focus"
        assert item.metadata["is_focus"] is True
        assert item.metadata["file_type"] == file_type
        assert item.priority == ContextPriority.CRITICAL

    @pytest.mark.parametrize(
        ("file_type", "section"),
        [("character", "【角色信息】"), ("lore", "【世界设定】"), ("snippet", "【参考素材】")],
    )
    def test_focus_marker_rendered_for_non_writing_types(
        self, db_session, owner, make_project, make_file, file_type, section
    ):
        project = make_project()
        file = make_file(project, f"焦点-{file_type}", file_type, content="这是一段内容。" * 10)

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=project.id,
            user_id=owner.id,
            focus_file_id=file.id,
            max_tokens=4000,
        )

        assert section in result.context
        assert "← 当前焦点" in result.context
        focus_items = [item for item in result.items if item["id"] == file.id]
        assert focus_items and focus_items[0]["priority"] == "critical"

    def test_focus_lore_not_evicted_by_retrieval_snippets(self, db_session, owner, make_project, make_file):
        """焦点设定不得被高分检索片段挤出上下文（焦点保护链的实际效果）。"""
        project = make_project()
        lore = make_file(project, "时间循环", "lore", content="时间循环的规则说明。" * 30)
        for i in range(10):
            make_file(project, f"干扰设定{i}", "lore", content=f"干扰内容{i}。" * 30)

        mock_results = [
            SimpleNamespace(
                entity_type="draft",
                entity_id=f"snippet-{i}",
                title=f"高分片段{i}",
                snippet="检索命中的片段内容。" * 40,
                content="检索命中的片段内容。" * 40,
                score=0.95,
                fused_score=0.99,
                line_start=1,
                sources=["semantic"],
            )
            for i in range(6)
        ]

        assembler = ContextAssembler()
        with patch("services.llama_index.get_llama_index_service") as mock_factory:
            mock_factory.return_value.hybrid_search.return_value = mock_results
            result = assembler.assemble(
                session=db_session,
                project_id=project.id,
                user_id=owner.id,
                query="帮我完善这条设定",
                focus_file_id=lore.id,
                max_tokens=4000,
            )

        assert lore.id in [item["id"] for item in result.items], "焦点设定被检索片段挤出上下文"
        assert result.budget_used["critical"] > 0


# ---------------------------------------------------------------------------
# #34 角色/设定查询必须下推 SQL LIMIT
# ---------------------------------------------------------------------------


class _CountingSession:
    """记录每条查询实际取回多少行的 Session 包装器。"""

    def __init__(self, inner: Session):
        self._inner = inner
        self.fetched_rows = 0
        self.statements: list[object] = []

    def exec(self, statement):
        self.statements.append(statement)
        rows = list(self._inner.exec(statement).all())
        self.fetched_rows += len(rows)
        return SimpleNamespace(all=lambda: rows, first=lambda: rows[0] if rows else None)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.unit
class TestFilesByTypesQueryLimit:
    def test_limit_pushed_down_to_sql(self, db_session, owner, make_project, make_file):
        project = make_project()
        for i in range(30):
            make_file(project, f"角色{i:02d}", "character", content="角色正文。" * 200, order=i)
            make_file(project, f"设定{i:02d}", "lore", content="设定正文。" * 200, order=i)

        assembler = ContextAssembler()
        counting = _CountingSession(db_session)
        items = assembler._get_files_by_types(  # noqa: SLF001
            session=counting,
            project_id=project.id,
            file_types=["character", "lore"],
            limit_per_type=10,
        )

        assert len(items) == 20
        # 修复前：一条无 LIMIT 的 select 取回全部 60 行（含 content 正文）
        assert counting.fetched_rows == 20, (
            f"取回了 {counting.fetched_rows} 行，说明 LIMIT 没有下推到 SQL"
        )
        for statement in counting.statements:
            assert statement._limit_clause is not None, "查询缺少 SQL LIMIT"

    def test_assemble_does_not_load_whole_project(self, db_session, owner, make_project, make_file):
        """整条 assemble 路径同样不得全量拉取角色/设定正文。"""
        project = make_project()
        for i in range(30):
            make_file(project, f"角色{i:02d}", "character", content="角色正文。" * 200, order=i)
            make_file(project, f"设定{i:02d}", "lore", content="设定正文。" * 200, order=i)

        assembler = ContextAssembler()
        counting = _CountingSession(db_session)
        assembler.assemble(
            session=counting,
            project_id=project.id,
            user_id=owner.id,
            max_tokens=4000,
        )

        # 清单查询会取回全部 60 行（只取列不取 content，成本可接受），
        # 正文查询则必须受 limit_per_type=10 约束，因此总行数远低于 60*2。
        loaded_files = db_session.exec(
            select(File).where(File.project_id == project.id)
        ).all()
        assert len(loaded_files) == 60
        assert counting.fetched_rows <= 60 + 20 + 5
