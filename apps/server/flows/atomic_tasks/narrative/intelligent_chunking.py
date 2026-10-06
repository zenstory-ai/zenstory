"""
智能分块任务

核心任务:
- intelligent_chunking_task: 基于章节摘要的智能分块
- save_chunks_task: 保存分块结果到数据库
- get_novel_chunks: 查询小说的分块结果
"""

from typing import Any

from prefect import get_run_logger
from pydantic import BaseModel, Field

from flows.database_session import get_db_session
from flows.utils import api_task, call_deepseek_api, database_task, get_deepseek_client

# ==================== 数据模型 ====================

class ChapterChunk(BaseModel):
    """章节块"""
    chunk_id: int = Field(description="块编号，从1开始")
    title: str = Field(description="块的主题标题")
    description: str = Field(description="块的内容概述，100-200字")
    start_chapter: int = Field(description="起始章节号")
    end_chapter: int = Field(description="结束章节号")
    chapter_count: int = Field(description="包含的章节数")
    key_themes: list[str] = Field(description="主要主题标签", default_factory=list)

    @property
    def chapter_range(self) -> str:
        """章节范围字符串"""
        return f"第{self.start_chapter}-{self.end_chapter}章"


class ChunkingResult(BaseModel):
    """分块结果"""
    total_chapters: int = Field(description="总章节数")
    chunk_count: int = Field(description="分块数量")
    chunks: list[ChapterChunk] = Field(description="章节块列表")
    chunking_strategy: str = Field(description="分块策略说明")

    def validate_coverage(self) -> bool:
        """验证是否覆盖所有章节"""
        covered_chapters = set()
        for chunk in self.chunks:
            covered_chapters.update(range(chunk.start_chapter, chunk.end_chapter + 1))
        return len(covered_chapters) == self.total_chapters





# ==================== 核心任务 ====================

@api_task(name="intelligent_chunking", retries=3)
def intelligent_chunking_task(
    novel_id: int,
    max_chunks: int = 15,  # 增加最大块数，减小平均块大小
    min_chapters_per_chunk: int = 25,  # 提高最小值，避免过小块
    max_chapters_per_chunk: int = 60,  # 新增：限制最大块大小
    min_total_chapters_for_chunking: int = 50,
) -> dict[str, Any]:
    """
    基于章节摘要的智能分块

    Args:
        novel_id: 小说ID
        max_chunks: 最大分块数量
        min_chapters_per_chunk: 每块最小章节数
        max_chapters_per_chunk: 每块最大章节数（新增）
        min_total_chapters_for_chunking: 启用分块的最小总章节数阈值

    Returns:
        Dict: 分块结果
    """
    logger = get_run_logger()

    logger.info(f"开始智能分块: novel_id={novel_id}")

    # 获取小说和章节摘要
    from services.material.chapters_service import ChaptersService
    from services.material.novels_service import NovelsService

    with get_db_session() as db:
        novel = NovelsService().get_by_id(db, novel_id)
        if not novel:
            raise ValueError(f"小说不存在: {novel_id}")

        # 在会话内提取需要的数据
        novel_title = novel.title

        chapters_orm = ChaptersService().list_by_novel_ordered(db, novel_id)

        if not chapters_orm:
            raise ValueError(f"小说无章节: {novel_id}")

        # 在会话关闭前提取章节数据为字典列表，避免 DetachedInstanceError
        chapters = [
            {
                "chapter_number": ch.chapter_number,
                "title": ch.title,
                "summary": ch.summary,
            }
            for ch in chapters_orm
        ]

    # chapters 现在是普通字典列表，不再依赖数据库会话

    total_chapters = len(chapters)
    logger.info(f"小说《{novel_title}》共 {total_chapters} 章")

    # 智能判断是否需要分块
    if total_chapters < min_total_chapters_for_chunking:
        logger.info(
            f"章节数 ({total_chapters}) 少于阈值 ({min_total_chapters_for_chunking})，"
            f"不进行分块，作为单一块处理"
        )
        # 直接创建单一块，不调用LLM
        chunking_result = ChunkingResult(
            total_chapters=total_chapters,
            chunk_count=1,
            chunks=[
                ChapterChunk(
                    chunk_id=1,
                    title=f"《{novel_title}》完整内容",
                    description=f"包含全部{total_chapters}章内容",
                    start_chapter=1,
                    end_chapter=total_chapters,
                    chapter_count=total_chapters,
                    key_themes=["完整故事"],
                )
            ],
            chunking_strategy=f"章节数较少({total_chapters}章)，不进行分块",
        )
    else:
        # 计算建议分块数
        suggested_chunks = max(
            2,
            min(max_chunks, total_chapters // min_chapters_per_chunk)
        )
        logger.info(f"章节数足够，建议分块数: {suggested_chunks}")

        # 构建系统提示词
        from prompts import create_intelligent_chunking_prompt
        system_prompt = create_intelligent_chunking_prompt()

        # 准备章节摘要
        chapter_summaries = _format_chapter_summaries(chapters)

        # 调用 LLM
        user_message = f"""
小说书名: {novel_title}
总章节数: {total_chapters}
建议分块数: {suggested_chunks} 个（可根据实际情况调整）

章节摘要列表:
{chapter_summaries}

请分析章节摘要，将小说智能分块。
"""

        logger.info(f"调用 LLM 进行智能分块，建议分块数: {suggested_chunks}")

        response = call_deepseek_api(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
            temperature=0.3,
            usage_novel_id=novel_id,
        )

        # 提取 JSON
        client = get_deepseek_client()
        data = client.extract_json_from_response(response)

        # 验证数据
        chunking_result = ChunkingResult(**data)

        # 验证覆盖率
        if not chunking_result.validate_coverage():
            logger.warning("分块结果未完全覆盖所有章节，尝试修复")
            chunking_result = _fix_chunking_coverage(chunking_result, total_chapters)

        # 拆分超大块（>max_chapters_per_chunk）
        chunking_result = _split_large_chunks(chunking_result, max_chapters_per_chunk, logger)

    logger.info(
        f"智能分块完成: {chunking_result.chunk_count} 个块, "
        f"策略: {chunking_result.chunking_strategy}"
    )

    return {
        "novel_id": novel_id,
        "total_chapters": total_chapters,
        "chunk_count": chunking_result.chunk_count,
        "chunking_strategy": chunking_result.chunking_strategy,
        "chunks": [chunk.model_dump() for chunk in chunking_result.chunks],
    }


@database_task(name="save_chunks", retries=2)
def save_chunks_task(
    novel_id: int,
    chunking_data: dict[str, Any],
) -> dict[str, Any]:
    """
    保存分块结果到数据库

    Args:
        novel_id: 小说ID
        chunking_data: 分块数据

    Returns:
        Dict: 保存结果
    """
    logger = get_run_logger()

    logger.info(f"开始保存分块结果: novel_id={novel_id}")

    from services.material.novels_service import NovelsService

    with get_db_session() as db:
        svc = NovelsService()
        svc.set_intelligent_chunks(db, novel_id, chunking_data)
        db.commit()

    logger.info("分块结果保存成功")

    return {
        "novel_id": novel_id,
        "saved": True,
        "chunk_count": chunking_data.get("chunk_count", 0),
    }


# ==================== 查询函数 ====================

def get_novel_chunks(novel_id: int) -> ChunkingResult | None:
    """
    获取小说的分块结果

    Args:
        novel_id: 小说ID

    Returns:
        分块结果，如果不存在返回 None
    """
    from services.material.novels_service import NovelsService

    with get_db_session() as db:
        chunks_data = NovelsService().get_intelligent_chunks(db, novel_id)
        if not chunks_data:
            return None

        return ChunkingResult(**chunks_data)


def get_chunk_chapters(novel_id: int, chunk_id: int) -> list[Any]:
    """
    获取指定块的所有章节

    Args:
        novel_id: 小说ID
        chunk_id: 块ID

    Returns:
        章节列表
    """
    from services.material.chapters_service import ChaptersService

    chunks_result = get_novel_chunks(novel_id)
    if not chunks_result:
        return []

    chunk = next((c for c in chunks_result.chunks if c.chunk_id == chunk_id), None)
    if not chunk:
        return []

    with get_db_session() as db:
        chapters = ChaptersService().list_by_novel_ordered(db, novel_id)
        return [
            ch for ch in chapters
            if chunk.start_chapter <= ch.chapter_number <= chunk.end_chapter
        ]


# ==================== 辅助函数 ====================

def _format_chapter_summaries(chapters: list[dict[str, Any]]) -> str:
    """
    格式化章节摘要为 LLM 输入

    Args:
        chapters: 章节字典列表，每个字典包含 chapter_number, title, summary

    策略:
    - 如果章节数 <= 100: 输出所有摘要
    - 如果章节数 > 100: 采样输出（每N章输出一个，保证覆盖全书）
    """
    total = len(chapters)

    if total <= 100:
        # 直接输出所有摘要
        lines = []
        for ch in chapters:
            summary = ch.get("summary") or "（无摘要）"
            lines.append(f"第{ch['chapter_number']}章 {ch['title']}: {summary[:100]}")
        return "\n".join(lines)

    else:
        # 采样输出
        sample_rate = max(2, total // 80)  # 最多输出80个摘要
        lines = []
        for i, ch in enumerate(chapters):
            if i % sample_rate == 0 or i == total - 1:  # 采样点 + 最后一章
                summary = ch.get("summary") or "（无摘要）"
                lines.append(f"第{ch['chapter_number']}章 {ch['title']}: {summary[:100]}")

        lines.insert(0, f"（共{total}章，以下为采样摘要，采样率1/{sample_rate}）\n")
        return "\n".join(lines)


def _fix_chunking_coverage(
    result: ChunkingResult,
    total_chapters: int
) -> ChunkingResult:
    """修复分块覆盖问题（将遗漏章节合并到最近的块）"""
    covered = set()
    for chunk in result.chunks:
        covered.update(range(chunk.start_chapter, chunk.end_chapter + 1))

    missing = set(range(1, total_chapters + 1)) - covered

    if not missing:
        return result

    for chapter_num in sorted(missing):
        closest_chunk = min(
            result.chunks,
            key=lambda c: min(
                abs(c.start_chapter - chapter_num),
                abs(c.end_chapter - chapter_num)
            )
        )

        if chapter_num < closest_chunk.start_chapter:
            closest_chunk.start_chapter = chapter_num
        else:
            closest_chunk.end_chapter = chapter_num

        closest_chunk.chapter_count = (
            closest_chunk.end_chapter - closest_chunk.start_chapter + 1
        )

    return result


def _split_large_chunks(
    result: ChunkingResult,
    max_chapters_per_chunk: int,
    logger
) -> ChunkingResult:
    """
    拆分超大块（>max_chapters_per_chunk）

    Args:
        result: 原始分块结果
        max_chapters_per_chunk: 最大块大小
        logger: 日志记录器

    Returns:
        拆分后的分块结果
    """
    new_chunks = []
    next_chunk_id = 1

    for chunk in result.chunks:
        chunk_size = chunk.end_chapter - chunk.start_chapter + 1

        if chunk_size <= max_chapters_per_chunk:
            # 块大小合理，直接保留
            chunk.chunk_id = next_chunk_id
            new_chunks.append(chunk)
            next_chunk_id += 1
        else:
            # 块过大，需要拆分
            logger.info(
                f"拆分超大块: {chunk.title} ({chunk_size}章) -> "
                f"目标每块约{max_chapters_per_chunk}章"
            )

            # 计算需要拆分成几个子块
            num_sub_chunks = (chunk_size + max_chapters_per_chunk - 1) // max_chapters_per_chunk
            sub_chunk_size = chunk_size // num_sub_chunks

            # 拆分
            for i in range(num_sub_chunks):
                start = chunk.start_chapter + i * sub_chunk_size
                # 最后一个子块包含剩余所有章节，否则正常计算结束章节
                end = chunk.end_chapter if i == num_sub_chunks - 1 else start + sub_chunk_size - 1

                sub_chunk = ChapterChunk(
                    chunk_id=next_chunk_id,
                    title=f"{chunk.title}（第{i+1}部分）",
                    description=f"{chunk.description}（拆分自超大块，第{i+1}/{num_sub_chunks}部分）",
                    start_chapter=start,
                    end_chapter=end,
                    chapter_count=end - start + 1,
                    key_themes=chunk.key_themes,
                )
                new_chunks.append(sub_chunk)
                next_chunk_id += 1

                logger.info(
                    f"  子块{i+1}: 第{start}-{end}章 ({end-start+1}章)"
                )

    # 更新结果
    result.chunks = new_chunks
    result.chunk_count = len(new_chunks)
    result.chunking_strategy += f" | 拆分超大块(>{max_chapters_per_chunk}章)"

    return result
