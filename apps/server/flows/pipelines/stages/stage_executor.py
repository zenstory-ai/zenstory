#!/usr/bin/env python3
"""
阶段执行器

统一阶段执行逻辑，消除主流程中的重复代码。
提供清晰的接口来执行各个阶段。
"""

from __future__ import annotations

import json
import time
from typing import Any

from prefect import get_run_logger, task

from config.material_settings import material_settings as settings
from config.material_settings import resolve_enabled_stages, warn_explicitly_dropped_stages
from core.error_codes import ErrorCode
from flows.database_session import get_prefect_db_session
from flows.pipelines.helpers import ProgressPublisher, ResultBuilder

META_EXTRACTION_ERROR = "Metadata extraction failed"
META_EXTRACTION_ERROR_CODE = "ERR_MATERIAL_META_EXTRACTION_FAILED"


# 本地实现 _elapsed_ms 函数
def _elapsed_ms(start: float) -> int:
    """计算从 start 到现在的毫秒数"""
    return int((time.perf_counter() - start) * 1000)


def _parse_cp_data(checkpoint) -> dict[str, Any]:
    """安全解析 checkpoint_data（可能是 JSON 字符串或 dict）"""
    if not checkpoint or not getattr(checkpoint, "checkpoint_data", None):
        return {}
    data = checkpoint.checkpoint_data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}

# 任务包装器函数定义在此处，避免循环导入
@task(name="run_story_aggregate_subflow", persist_result=False)
def _task_run_story_aggregate(
    novel_id: int,
    chapter_ids: list[int],
    correlation_id: str | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """任务包装器：执行剧情聚合子流程"""
    from flows.pipelines.subflows.story_aggregate_flow import story_aggregate_flow
    return story_aggregate_flow(
        novel_id=novel_id,
        chapter_ids=chapter_ids,
        correlation_id=correlation_id,
        job_id=job_id,
    )

@task(name="run_relationship_subflow", persist_result=False)
def _task_run_relationship(
    novel_id: int,
    chapter_ids: list[int],
    correlation_id: str | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """任务包装器：执行人物关系子流程"""
    from flows.pipelines.subflows.relationship_flow import relationship_flow
    return relationship_flow(
        novel_id=novel_id,
        chapter_ids=chapter_ids,
        correlation_id=correlation_id,
        job_id=job_id,
    )

@task(name="run_character_entity_build_subflow", persist_result=False)
def _task_run_character_entity_build(novel_id: int, correlation_id: str | None = None) -> dict[str, Any]:
    """任务包装器：执行角色实体构建子流程"""
    from flows.pipelines.subflows.character_entity_build_flow import (
        character_entity_build_flow,
    )
    return character_entity_build_flow(novel_id=novel_id, correlation_id=correlation_id)


class StageExecutor:
    """
    阶段执行器

    封装阶段1和阶段2的执行逻辑，提供统一的接口。
    消除主流程和恢复函数之间的重复代码。

    使用示例:
        >>> executor = StageExecutor(novel_id, chapter_ids, checkpoint_manager, correlation_id)
        >>> stage1_result = executor.execute_stage1()
        >>> final_result = executor.execute_stage2(stage1_result, flow_start)
    """

    def __init__(
        self,
        novel_id: int,
        chapter_ids: list[int],
        checkpoint_manager: Any,
        correlation_id: str | None = None,
        job_id: int | None = None,
    ):
        """
        初始化阶段执行器

        Args:
            novel_id: 小说ID
            chapter_ids: 章节ID列表
            checkpoint_manager: checkpoint管理器
            correlation_id: 关联ID（用于进度推送）
        """
        self.novel_id = novel_id
        self.chapter_ids = chapter_ids
        self.checkpoint_manager = checkpoint_manager
        self.correlation_id = correlation_id
        self.job_id = job_id
        self.logger = get_run_logger()
        self.publisher = ProgressPublisher(correlation_id, self.logger)
        # 所有阶段门控统一读取生效后的阶段集合（已应用依赖约束）
        self.stages = resolve_enabled_stages(settings)
        self._meta_extraction: dict[str, Any] = {
            "executed": False,
            "succeeded": None,
            "error": None,
            "error_code": None,
            "retry_available": False,
        }

    def record_enabled_stages(self) -> dict[str, bool]:
        """记录本次运行的阶段：INFO 日志 + 写入 IngestionJob.stage_progress["enabled_stages"]。

        每次流程启动（含断点恢复 / 重试）调用一次；失败只记 warning，不影响流程。
        写入的快照语义是"该阶段可能产出过数据"：与该小说此前任一次运行的快照
        按阶段取 OR（见 IngestionJobsService.set_enabled_stages），因此部署后
        关闭某阶段再重试，不会把之前已产出数据的阶段标成"未启用"。

        Returns:
            本次运行实际生效的阶段（未合并历史快照）。
        """
        snapshot = self.stages.as_snapshot()
        self.logger.info("[阶段开关] novel_id=%s %s", self.novel_id, self.stages.describe())
        warn_explicitly_dropped_stages(self.stages, self.logger, f"novel_id={self.novel_id}")
        try:
            from services.material.ingestion_jobs_service import IngestionJobsService

            with get_prefect_db_session() as session:
                svc = IngestionJobsService()
                job = self._resolve_job(session, svc)
                if job:
                    svc.set_enabled_stages(session, job.id, snapshot)
                    session.commit()
        except Exception as e:
            self.logger.warning(f"[阶段开关] 写入阶段快照失败: {e}")
        return snapshot

    def execute_stage1(self) -> dict[str, Any]:
        """
        执行阶段1：章节并行提取

        执行章节摘要、情节点、角色提及、元信息的并行提取。
        统一处理阶段1的逻辑，供主流程和恢复函数共用。

        Returns:
            Dict[str, Any]: 阶段1结果
        """
        self.logger.info("=" * 60)
        self.logger.info("[阶段1] 按章节并行提取 (章节摘要 + 情节点 + 实体)")
        self.logger.info(f"[阶段1] novel_id={self.novel_id}, chapters_count={len(self.chapter_ids)}")
        self.logger.info("=" * 60)
        self.publisher.publish("stage1_started", status="processing", novel_id=self.novel_id)
        self._sync_job_stage(
            "stage1",
            "processing",
            payload={"chapters_total": len(self.chapter_ids)},
        )

        from flows.pipelines.subflows.chapter_extraction_flow import (
            chapter_extraction_flow,
        )

        stage1_start = time.perf_counter()

        # 并行触发元信息提取（统一逻辑）
        meta_future = self._handle_meta_extraction(is_parallel=True)

        # 执行章节提取流程
        stage1_result = chapter_extraction_flow(
            novel_id=self.novel_id,
            chapter_ids=self.chapter_ids,
            correlation_id=self.correlation_id,  # 【修复】统一传递关联ID
            job_id=self.job_id,
        )

        # 处理元信息提取结果
        if meta_future is not None:
            try:
                meta_result = meta_future.result()
                self._complete_meta_extraction(meta_result)
            except Exception as e:
                self.logger.warning(f"[阶段1] 元信息并行提取失败: {e}")
                self._record_meta_failure(e)

        stage1_result["meta_extraction"] = self._meta_extraction.copy()

        stage1_elapsed = int((time.perf_counter() - stage1_start) * 1000)
        failed_count = stage1_result.get("failed_count", 0)
        stage1_status = stage1_result.get("status", "completed")

        # 更新 checkpoint
        self.checkpoint_manager.mark_stage_completed("stage1", {
            "summaries_count": stage1_result.get("summaries_count", 0),
            "plots_count": stage1_result.get("plots_count", 0),
            "failed_count": failed_count,
            "failed_chapters": stage1_result.get("failed_chapters", []),
            "failed_mention_chapters": stage1_result.get("failed_mention_chapters", []),
            "completed_chapter_ids": stage1_result.get("completed_chapter_ids", []),
            "failed_chapter_ids": stage1_result.get("failed_chapter_ids", []),
            "completed_plot_chapter_ids": stage1_result.get("completed_plot_chapter_ids", []),
            "failed_plot_chapter_ids": stage1_result.get("failed_plot_chapter_ids", []),
            "completed_mention_chapter_ids": stage1_result.get("completed_mention_chapter_ids", []),
            "failed_mention_chapter_ids": stage1_result.get("failed_mention_chapter_ids", []),
            "meta_extraction": stage1_result["meta_extraction"],
            "status": stage1_status,
        })

        # 记录日志
        self.logger.info(
            "[阶段1] 完成: summaries=%s, plots=%s, mentions=%s, failed=%s, elapsed_ms=%s, status=%s",
            stage1_result.get("summaries_count", 0),
            stage1_result.get("plots_count", 0),
            stage1_result.get("mentions_extracted", False),
            failed_count,
            stage1_elapsed,
            stage1_result.get("status", "completed"),
        )

        # 发布进度
        self.publisher.publish(
            "stage1_completed",
            status="processing",
            novel_id=self.novel_id,
            elapsed_ms=stage1_elapsed,
            summaries_count=stage1_result.get("summaries_count", 0),
            plots_count=stage1_result.get("plots_count", 0),
            failed_count=failed_count,
            progress=50,
            message=f"阶段1完成: 已提取 {stage1_result.get('summaries_count', 0)} 个章节摘要"
        )
        self._sync_job_stage(
            "stage1",
            stage1_status,
            payload={
                "summaries_count": stage1_result.get("summaries_count", 0),
                "plots_count": stage1_result.get("plots_count", 0),
                "failed_count": failed_count,
                "failed_mention_chapters": stage1_result.get("failed_mention_chapters", []),
                "meta_extraction": stage1_result["meta_extraction"],
                "elapsed_ms": stage1_elapsed,
            },
            processed_chapters=stage1_result.get(
                "processed_chapters", max(0, len(self.chapter_ids) - failed_count)
            ),
            status="processing",
        )

        if failed_count > 0:
            self.logger.warning(
                "[阶段1] 部分章节处理失败: failed_count=%s, failed_chapters=%s",
                failed_count,
                stage1_result.get("failed_chapters", []),
            )

        return stage1_result

    def execute_stage2(
        self,
        stage1_result: dict[str, Any] | None = None,
        flow_start: float | None = None,
    ) -> dict[str, Any]:
        """
        执行阶段2：剧情聚合 + 角色实体 + 人物关系

        当检测到阶段1已完成但阶段2未完成时，从此处恢复。
        执行三个子阶段：
        - 阶段2A（剧情聚合）和 阶段2C（角色实体）并行执行
        - 阶段2B（人物关系）依赖 阶段2C 完成后执行

        Args:
            stage1_result: 阶段1的结果（如果为None，会从checkpoint或数据库加载）
            flow_start: 流程开始时间（用于计算总耗时）

        Returns:
            Dict[str, Any]: 最终结果（包含所有阶段的统计信息）
        """
        # 如果没有 stage1_result，优先从 checkpoint 获取，降级到数据库统计
        if stage1_result is None:
            stage1_result = self._load_stage1_result()

        self.logger.info("=" * 60)
        self.logger.info("[阶段2] 并行执行剧情聚合和人物关系")
        self.logger.info(f"[阶段2] novel_id={self.novel_id}, chapters_count={len(self.chapter_ids)}")
        self.logger.info("=" * 60)
        self.publisher.publish(
            "stage2_started",
            status="processing",
            novel_id=self.novel_id,
            progress=60,
            message="阶段2: 开始剧情聚合和人物关系分析..."
        )
        self._sync_job_stage(
            "stage2",
            "processing",
            payload={"chapters_total": len(self.chapter_ids)},
            status="processing",
        )

        stage2_start = time.perf_counter()

        # 获取阶段2各个子阶段的完成状态
        stage2a_done, stage2b_done, stage2c_done = self._check_stage2_completion()

        # 并行执行阶段2A（剧情）和阶段2C（角色实体）
        story_result, character_entity_result = self._execute_parallel_stages(
            stage2a_done, stage2c_done
        )

        # 执行阶段2B（人物关系），依赖阶段2C完成
        relationship_result = self._execute_relationship_stage(stage2b_done)

        stage2_elapsed = int((time.perf_counter() - stage2_start) * 1000)

        # 更新所有子阶段的 checkpoint
        self._update_stage2_checkpoints(story_result, relationship_result, character_entity_result)

        # 记录日志
        self.logger.info(
            "[阶段2] 完成: stories=%s, storylines=%s, relationships=%s, characters=%s/%s, elapsed_ms=%s",
            story_result.get("stories_count", 0),
            story_result.get("storylines_count", 0),
            relationship_result.get("relationships_count", 0),
            character_entity_result.get("created_count", 0),
            character_entity_result.get("updated_count", 0),
            stage2_elapsed,
        )

        # 发布进度
        self.publisher.publish(
            "stage2_completed",
            status="processing",
            novel_id=self.novel_id,
            elapsed_ms=stage2_elapsed,
            stories_count=story_result.get("stories_count", 0),
            storylines_count=story_result.get("storylines_count", 0),
            relationships_count=relationship_result.get("relationships_count", 0),
            neo4j_persisted=relationship_result.get("neo4j_persisted", False),
            characters_created=character_entity_result.get("created_count", 0),
            characters_updated=character_entity_result.get("updated_count", 0),
            progress=90,
            message=f"阶段2完成: 已生成 {story_result.get('stories_count', 0)} 个剧情点, {character_entity_result.get('created_count', 0) + character_entity_result.get('updated_count', 0)} 个角色"
        )
        self._sync_job_stage(
            "stage2",
            self._derive_final_status(
                stage1_result,
                story_result,
                relationship_result,
                character_entity_result,
            ),
            payload={
                "stories_count": story_result.get("stories_count", 0),
                "storylines_count": story_result.get("storylines_count", 0),
                "relationships_count": relationship_result.get("relationships_count", 0),
                "failed_stories": story_result.get("failed_stories", []),
                "neo4j_failed_chapters": relationship_result.get("neo4j_failed_chapters", []),
                "character_failed_count": character_entity_result.get("failed_count", 0),
                "meta_extraction": (stage1_result or {}).get("meta_extraction"),
                "elapsed_ms": stage2_elapsed,
            },
            status="processing",
        )

        final_status = self._derive_final_status(
            stage1_result,
            story_result,
            relationship_result,
            character_entity_result,
        )

        # 更新 IngestionJob 状态
        self._update_job_status(
            final_status,
            stage1_result,
            story_result,
            relationship_result,
            character_entity_result,
        )

        # 构建最终结果（使用 ResultBuilder）
        elapsed_ms = _elapsed_ms(flow_start) if flow_start else 0
        result = ResultBuilder.build_final_result(
            novel_id=self.novel_id,
            job_id=self._get_job_id(),
            chapter_ids=self.chapter_ids,
            stage1_result=stage1_result,
            story_result=story_result,
            relationship_result=relationship_result,
            character_entity_result=character_entity_result,
            status=final_status,
            elapsed_ms=elapsed_ms,
        )

        # 标记完成，保存完整的结果数据到 checkpoint
        self._save_final_checkpoint(stage1_result, story_result, relationship_result, character_entity_result)

        # 发送完成消息（使用 ProgressPublisher）
        self.publisher.publish_completion(self.novel_id, self.chapter_ids, result)

        return result

    def _handle_meta_extraction(self, is_parallel: bool = True) -> Any | None:
        """
        统一处理元信息提取逻辑

        Args:
            is_parallel: 是否并行执行（True=submit, False=直接执行）

        Returns:
            Future对象（并行模式）或 None
        """
        if not self.stages.meta:
            return None

        # 检查是否已完成
        resume_cp = self.checkpoint_manager.get_checkpoint("stage2")
        cp_data = _parse_cp_data(resume_cp)

        if cp_data.get("meta_extracted"):
            self.logger.info("[元信息] 已完成，跳过")
            self._meta_extraction["succeeded"] = True
            return None

        try:
            from flows.atomic_tasks.entities import extract_novel_meta_task
            self.logger.info("[元信息] 开始提取...")
            self._meta_extraction["executed"] = True

            if is_parallel:
                # 并行模式：返回 Future
                return extract_novel_meta_task.submit(novel_id=self.novel_id)
            else:
                # 同步模式：直接执行
                meta_result = extract_novel_meta_task(novel_id=self.novel_id)
                self._complete_meta_extraction(meta_result)
                return None
        except Exception as e:
            self.logger.warning(f"[元信息] 提取失败: {e}")
            self._record_meta_failure(e)
            return None

    def _record_meta_failure(self, _error: Exception | str) -> None:
        self._meta_extraction.update(
            executed=True,
            succeeded=False,
            error=META_EXTRACTION_ERROR,
            error_code=META_EXTRACTION_ERROR_CODE,
            retry_available=True,
        )

    def _complete_meta_extraction(self, meta_result: dict[str, Any]) -> None:
        if self._save_meta_result(meta_result) is False:
            if self._meta_extraction.get("succeeded") is not False:
                self._record_meta_failure("metadata persistence failed")
            return
        self._meta_extraction.update(
            executed=True,
            succeeded=True,
            error=None,
            error_code=None,
            retry_available=False,
        )

    def _save_meta_result(self, meta_result: dict[str, Any]) -> bool:
        """
        保存元信息提取结果

        Args:
            meta_result: 元信息提取结果
        """
        try:
            from sqlmodel import select

            from flows.atomic_tasks.entities import build_meta_entities_task
            from models.material_models import Chapter

            # 1. 查询 first_chapter
            with get_prefect_db_session() as session:
                first_chapter = session.exec(
                    select(Chapter).where(Chapter.novel_id == self.novel_id).order_by(Chapter.chapter_number)
                ).first()
                first_chapter_id = first_chapter.id if first_chapter else None

            # 2. 构建实体（可能失败）
            build_meta_entities_task(
                meta_data=meta_result,
                novel_id=self.novel_id,
                first_chapter_id=first_chapter_id
            )

            # 【修复 Bug #3】只有成功后才更新 checkpoint
            self.checkpoint_manager.update_checkpoint(
                "stage2",
                status="processing",
                data={"meta_extracted": True}
            )
            self.logger.info("[元信息] 提取与入库完成")
            return True

        except Exception as e:
            self.logger.error(f"[元信息] 保存失败: {e}", exc_info=True)
            self._record_meta_failure(e)
            # 【修复 Bug #3】不要 raise，让上层流程继续（元信息是可选的）
            # 标记为部分失败
            try:
                self.checkpoint_manager.update_checkpoint(
                    "stage2",
                    status="processing",
                    data={
                        "meta_extracted": False,
                        "meta_error": META_EXTRACTION_ERROR,
                        "meta_error_code": META_EXTRACTION_ERROR_CODE,
                    }
                )
            except Exception as cp_err:
                self.logger.warning(f"[元信息] 更新 checkpoint 失败: {cp_err}")
            return False

    def _load_stage1_result(self) -> dict[str, Any]:
        """
        从 checkpoint 或数据库加载阶段1结果

        Returns:
            阶段1结果字典
        """
        self.logger.info("[阶段2] 从 checkpoint 和数据库加载阶段1统计信息")

        # 1. 优先从 checkpoint 获取
        stage1_cp = self.checkpoint_manager.get_checkpoint("stage1")
        stage1_cp_data = _parse_cp_data(stage1_cp)
        if stage1_cp_data:
            stage1_result = stage1_cp_data.copy()
            self.logger.debug(f"[阶段2] 从 checkpoint 加载: {stage1_result}")
        else:
            # 2. 降级到数据库统计
            from services.material.stats_service import StatsService
            stats_service = StatsService()
            with get_prefect_db_session() as session:
                stage1_result = stats_service.count_stage1(session, self.novel_id)
            self.logger.debug(f"[阶段2] 从数据库加载: {stage1_result}")

        # 3. 检查角色提及提取状态（从 stage1 checkpoint）
        mentions_extracted = stage1_result.get("mentions_extracted", False)
        if not mentions_extracted:
            # 降级：从 checkpoint 获取
            stage1_cp = self.checkpoint_manager.get_checkpoint("stage1")
            _cp_data = _parse_cp_data(stage1_cp)
            mentions_extracted = _cp_data.get("mentions_extracted", False)

        if stage1_result:
            stage1_result["mentions_extracted"] = mentions_extracted
        else:
            stage1_result = {"mentions_extracted": mentions_extracted}
        self.logger.debug(f"[阶段2] 最终 stage1_result: {stage1_result}")

        return stage1_result

    def _check_stage2_completion(self) -> tuple[bool, bool, bool]:
        """
        检查阶段2各个子阶段的完成状态

        Returns:
            (stage2a_done, stage2b_done, stage2c_done)
        """
        # 获取各子阶段的 checkpoint
        stage2a_cp = self.checkpoint_manager.get_checkpoint("stage2a")
        stage2b_cp = self.checkpoint_manager.get_checkpoint("stage2b")
        stage2c_cp = self.checkpoint_manager.get_checkpoint("stage2c")

        required_a = {
            name
            for name, enabled in (
                ("synopsis", self.stages.synopsis),
                ("stories", self.stages.stories),
                ("storylines", self.stages.storylines),
            )
            if enabled
        }
        required_b = {"relationships"} if self.stages.relationships else set()
        required_c = {"characters"} if self.stages.characters else set()

        def _done(checkpoint: Any, required: set[str]) -> bool:
            if not required:
                return True
            if not checkpoint or getattr(checkpoint, "stage_status", None) != "completed":
                return False
            data = _parse_cp_data(checkpoint)
            if data.get("disabled") or data.get("skipped"):
                return False
            if data.get("status") not in (None, "completed"):
                return False
            if (
                data.get("failed_stories")
                or data.get("neo4j_failed_chapters")
                or data.get("failed_characters")
                or data.get("failed_count", 0) > 0
            ):
                return False
            executed = set(data.get("executed_capabilities") or [])
            return required.issubset(executed)

        stage2a_done = _done(stage2a_cp, required_a)
        stage2b_done = _done(stage2b_cp, required_b)
        stage2c_done = _done(stage2c_cp, required_c)

        return stage2a_done, stage2b_done, stage2c_done

    def _execute_parallel_stages(self, stage2a_done: bool, stage2c_done: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        并行执行阶段2A（剧情）和阶段2C（角色实体）

        Args:
            stage2a_done: 阶段2A是否已完成
            stage2c_done: 阶段2C是否已完成

        Returns:
            (story_result, character_entity_result)
        """
        # 使用本地定义的任务包装器

        story_future = None
        character_entity_future = None

        # 启动任务
        if not stage2a_done:
            self.logger.info("[阶段2-剧情] 子流未完成，并行执行 story_aggregate_flow")
            story_args = (self.novel_id, self.chapter_ids, self.correlation_id)
            if self.job_id is None:
                story_future = _task_run_story_aggregate.submit(*story_args)
            else:
                story_future = _task_run_story_aggregate.submit(*story_args, job_id=self.job_id)
        else:
            self.logger.info("[阶段2-剧情] 子流已完成，跳过执行")

        if not stage2c_done and self.stages.characters:
            self.logger.info("[阶段2-角色] 子流未完成，并行执行 character_entity_build_flow")
            character_entity_future = _task_run_character_entity_build.submit(self.novel_id, self.correlation_id)  # 【修复】传递关联ID
        else:
            if not self.stages.characters:
                self.logger.info("[阶段2-角色] 角色提取功能未启用，跳过执行")
            else:
                self.logger.info("[阶段2-角色] 子流已完成，跳过执行")

        # 获取结果
        if story_future is not None:
            story_result = story_future.result()
            story_result["_executed"] = True
            story_result["executed_capabilities"] = [
                name
                for name in ("synopsis", "stories", "storylines")
                if getattr(self.stages, name)
            ]
            self.logger.info("[阶段2-剧情] 完成")
        else:
            if not self.stages.story_flow_needed:
                story_result = {
                    "status": "skipped",
                    "_executed": False,
                    "executed_capabilities": [],
                }
            else:
                # 从 checkpoint 获取结果
                stage2a_data = _parse_cp_data(self.checkpoint_manager.get_checkpoint("stage2a"))
                story_result = {
                    "synopsis_generated": stage2a_data.get("synopsis_generated", False),
                    "stories_count": stage2a_data.get("stories_count", 0),
                    "storylines_count": stage2a_data.get("storylines_count", 0),
                    "failed_stories": stage2a_data.get("failed_stories", []),
                    "status": stage2a_data.get("status", "completed"),
                    "executed_capabilities": stage2a_data.get("executed_capabilities", []),
                    "_executed": False,
                }

        if character_entity_future is not None:
            character_entity_result = character_entity_future.result()
            character_entity_result["_executed"] = True
            character_entity_result["executed_capabilities"] = ["characters"]
            self.logger.info("[阶段2-角色] 完成")
        else:
            if not self.stages.characters:
                character_entity_result = {
                    "status": "skipped",
                    "_executed": False,
                    "executed_capabilities": [],
                }
            else:
                # 从 checkpoint 获取结果
                stage2c_data = _parse_cp_data(self.checkpoint_manager.get_checkpoint("stage2c"))
                character_entity_result = {
                    "created_count": stage2c_data.get("created_count", 0),
                    "updated_count": stage2c_data.get("updated_count", 0),
                    "failed_count": stage2c_data.get("failed_count", 0),
                    "failed_characters": stage2c_data.get("failed_characters", []),
                    "status": stage2c_data.get("status", "completed"),
                    "executed_capabilities": stage2c_data.get("executed_capabilities", []),
                    "_executed": False,
                }

        self.logger.info("[阶段2] 剧情聚合和角色实体构建完成，开始人物关系提取...")

        return story_result, character_entity_result

    def _execute_relationship_stage(self, stage2b_done: bool) -> dict[str, Any]:
        """
        执行阶段2B（人物关系）

        Args:
            stage2b_done: 阶段2B是否已完成

        Returns:
            relationship_result: 关系结果
        """
        # 使用本地定义的任务包装器

        if not self.stages.relationships:
            self.logger.info("[阶段2-关系] 人物关系功能未启用，跳过执行")
            return {
                "status": "skipped",
                "_executed": False,
                "executed_capabilities": [],
            }

        if not stage2b_done:
            self.logger.info("[阶段2-关系] 子流未完成，执行 relationship_flow")
            relationship_args = (self.novel_id, self.chapter_ids, self.correlation_id)
            if self.job_id is None:
                relationship_future = _task_run_relationship.submit(*relationship_args)
            else:
                relationship_future = _task_run_relationship.submit(
                    *relationship_args,
                    job_id=self.job_id,
                )
            relationship_result = relationship_future.result()
            relationship_result["_executed"] = True
            relationship_result["executed_capabilities"] = ["relationships"]
            self.logger.info("[阶段2-关系] 完成")
        else:
            self.logger.info("[阶段2-关系] 子流已完成，跳过执行")
            # 从 checkpoint 获取结果
            stage2b_data = _parse_cp_data(self.checkpoint_manager.get_checkpoint("stage2b"))
            relationship_result = {
                "relationships_count": stage2b_data.get("relationships_count", 0),
                "neo4j_persisted": stage2b_data.get("neo4j_persisted", False),
                "neo4j_failed_chapters": stage2b_data.get("neo4j_failed_chapters", []),
                "status": stage2b_data.get("status", "completed"),
                "executed_capabilities": stage2b_data.get("executed_capabilities", []),
                "_executed": False,
            }

        return relationship_result

    def _update_stage2_checkpoints(
        self,
        story_result: dict[str, Any],
        relationship_result: dict[str, Any],
        character_entity_result: dict[str, Any],
    ) -> None:
        """
        更新阶段2所有子阶段的 checkpoint

        Args:
            story_result: 剧情结果
            relationship_result: 关系结果
            character_entity_result: 角色实体结果
        """
        story_payload = {
            "synopsis_generated": story_result.get("synopsis_generated", False),
            "stories_count": story_result.get("stories_count", 0),
            "storylines_count": story_result.get("storylines_count", 0),
            "failed_stories": story_result.get("failed_stories", []),
            "status": story_result.get("status", "completed"),
            "executed_capabilities": story_result.get("executed_capabilities", []),
        }
        relationship_payload = {
            "relationships_count": relationship_result.get("relationships_count", 0),
            "neo4j_persisted": relationship_result.get("neo4j_persisted", False),
            "neo4j_failed_chapters": relationship_result.get("neo4j_failed_chapters", []),
            "status": relationship_result.get("status", "completed"),
            "executed_capabilities": relationship_result.get("executed_capabilities", []),
        }
        character_payload = {
            "characters_built": True,
            "created_count": character_entity_result.get("created_count", 0),
            "updated_count": character_entity_result.get("updated_count", 0),
            "failed_count": character_entity_result.get("failed_count", 0),
            "failed_characters": character_entity_result.get("failed_characters", []),
            "status": character_entity_result.get("status", "completed"),
            "executed_capabilities": character_entity_result.get("executed_capabilities", []),
        }
        story_complete = (
            not self.stages.story_flow_needed
            or (
                story_payload["status"] == "completed"
                and not story_payload["failed_stories"]
            )
        )
        relationship_complete = (
            not self.stages.relationships
            or (
                relationship_payload["status"] == "completed"
                and not relationship_payload["neo4j_failed_chapters"]
            )
        )
        character_complete = (
            not self.stages.characters
            or (
                character_payload["status"] == "completed"
                and character_payload["failed_count"] == 0
                and not character_payload["failed_characters"]
            )
        )

        # Disabled or already-completed stages are read-only here. This avoids
        # manufacturing a completed capability that did not execute in this run.
        if story_result.get("_executed", True):
            if story_complete:
                self.checkpoint_manager.mark_stage_completed("stage2a", story_payload)
            else:
                self.checkpoint_manager.update_checkpoint(
                    "stage2a", status="failed", data=story_payload
                )

        if relationship_result.get("_executed", True):
            if relationship_complete:
                self.checkpoint_manager.mark_stage_completed(
                    "stage2b", relationship_payload
                )
            else:
                self.checkpoint_manager.update_checkpoint(
                    "stage2b", status="failed", data=relationship_payload
                )

        if character_entity_result.get("_executed", True):
            if character_complete:
                self.checkpoint_manager.mark_stage_completed(
                    "stage2c", character_payload
                )
            else:
                self.checkpoint_manager.update_checkpoint(
                    "stage2c", status="failed", data=character_payload
                )

        # 汇总到 stage2（用于整体状态追踪）
        executed_capabilities = sorted(
            set(story_result.get("executed_capabilities", []))
            | set(relationship_result.get("executed_capabilities", []))
            | set(character_entity_result.get("executed_capabilities", []))
        )
        stage2_payload = {
            "synopsis_generated": story_result.get("synopsis_generated", False),
            "stories_count": story_result.get("stories_count", 0),
            "storylines_count": story_result.get("storylines_count", 0),
            "relationships_count": relationship_result.get("relationships_count", 0),
            "neo4j_persisted": relationship_result.get("neo4j_persisted", False),
            "characters_built": "characters" in executed_capabilities,
            "characters_created": character_entity_result.get("created_count", 0),
            "characters_updated": character_entity_result.get("updated_count", 0),
            "failed_count": len(story_result.get("failed_stories", []))
            + len(relationship_result.get("neo4j_failed_chapters", []))
            + character_entity_result.get("failed_count", 0),
            "failed_stories": story_result.get("failed_stories", []),
            "neo4j_failed_chapters": relationship_result.get("neo4j_failed_chapters", []),
            "failed_chapters": [],
            "failed_mention_chapters": [],
            "failed_characters": character_entity_result.get("failed_characters", []),
            "executed_capabilities": executed_capabilities,
        }
        if story_complete and relationship_complete and character_complete:
            self.checkpoint_manager.mark_stage_completed("stage2", stage2_payload)
        else:
            self.checkpoint_manager.update_checkpoint(
                "stage2", status="failed", data=stage2_payload
            )

    def _get_job_id(self) -> int | None:
        """
        获取当前任务ID；只有旧调用缺少ID时才兼容查询最新任务

        Returns:
            job_id 或 None
        """
        try:
            from services.material.ingestion_jobs_service import IngestionJobsService
            with get_prefect_db_session() as session:
                svc = IngestionJobsService()
                job = self._resolve_job(session, svc)
                return job.id if job else None
        except Exception as e:
            self.logger.warning(f"[Job] 获取 Job ID 失败: {e}")
            return None

    def _sync_job_stage(
        self,
        stage: str,
        stage_status: str,
        *,
        payload: dict[str, Any] | None = None,
        processed_chapters: int | None = None,
        status: str | None = None,
    ) -> None:
        """同步 IngestionJob 的阶段进度，失败时仅记录 warning。"""
        try:
            from services.material.ingestion_jobs_service import IngestionJobsService

            with get_prefect_db_session() as session:
                svc = IngestionJobsService()
                job = self._resolve_job(session, svc)
                if not job:
                    return
                svc.update_processed(
                    session,
                    job.id,
                    processed_chapters=processed_chapters,
                    status=status,
                    stage=stage,
                    stage_status=stage_status,
                    stage_data=payload or {},
                )
                session.commit()
        except Exception as e:
            self.logger.warning(f"[Job] 同步阶段进度失败: stage={stage}, status={stage_status}, err={e}")

    def _derive_final_status(
        self,
        stage1_result: dict[str, Any] | None,
        story_result: dict[str, Any],
        relationship_result: dict[str, Any],
        character_entity_result: dict[str, Any],
    ) -> str:
        """Derive overall flow status from stage-level failures."""
        if (stage1_result or {}).get("failed_count", 0) > 0:
            return "completed_with_errors"
        if (stage1_result or {}).get("meta_extraction", {}).get("succeeded") is False:
            return "completed_with_errors"
        if story_result.get("failed_stories"):
            return "completed_with_errors"
        if relationship_result.get("neo4j_failed_chapters"):
            return "completed_with_errors"
        if character_entity_result.get("failed_count", 0) > 0:
            return "completed_with_errors"
        return "completed"

    def _update_job_status(
        self,
        final_status: str,
        stage1_result: dict[str, Any] | None,
        story_result: dict[str, Any],
        relationship_result: dict[str, Any],
        character_entity_result: dict[str, Any],
    ) -> None:
        """
        更新 IngestionJob 最终状态
        """
        try:
            from services.material.ingestion_jobs_service import IngestionJobsService
            with get_prefect_db_session() as session:
                svc = IngestionJobsService()
                job = self._resolve_job(session, svc)
                if job:
                    svc.update_processed(
                        session,
                        job.id,
                        processed_chapters=(stage1_result or {}).get(
                            "processed_chapters", len(self.chapter_ids)
                        ),
                        status=final_status,
                        stage="completed",
                        stage_status=final_status,
                        error_message=(
                            ErrorCode.MATERIAL_PARTIALLY_COMPLETED
                            if final_status == "completed_with_errors"
                            else None
                        ),
                        stage_data={
                            "chapters_total": len(self.chapter_ids),
                            "failed_count": (stage1_result or {}).get("failed_count", 0)
                            + character_entity_result.get("failed_count", 0)
                            + int(
                                (stage1_result or {})
                                .get("meta_extraction", {})
                                .get("succeeded")
                                is False
                            ),
                            "failed_stories": story_result.get("failed_stories", []),
                            "neo4j_failed_chapters": relationship_result.get("neo4j_failed_chapters", []),
                            "failed_chapters": (stage1_result or {}).get("failed_chapters", []),
                            "failed_mention_chapters": (stage1_result or {}).get("failed_mention_chapters", []),
                            "failed_characters": character_entity_result.get("failed_characters", []),
                            "meta_extraction": (stage1_result or {}).get("meta_extraction"),
                        },
                    )
                    session.commit()
        except Exception as e:
            self.logger.warning(f"[Job] 更新 Job 状态失败: {e}")

    def _resolve_job(self, session: Any, svc: Any) -> Any | None:
        """Resolve the exact job when supplied; legacy callers get one bounded fallback."""
        if self.job_id is not None:
            from models.material_models import IngestionJob

            job = session.get(IngestionJob, self.job_id)
            if job is None or job.novel_id != self.novel_id:
                raise ValueError(
                    f"job_id={self.job_id} 不属于 novel_id={self.novel_id}"
                )
            return job
        self.logger.warning(
            "[Job] legacy V3 caller omitted job_id; resolving latest once for novel_id=%s",
            self.novel_id,
        )
        job = svc.get_latest_by_novel(session, self.novel_id)
        if job is not None:
            self.job_id = job.id
        return job

    def _save_final_checkpoint(
        self,
        stage1_result: dict[str, Any] | None,
        story_result: dict[str, Any],
        relationship_result: dict[str, Any],
        character_entity_result: dict[str, Any],
    ) -> None:
        """
        保存最终 checkpoint

        Args:
            stage1_result: 阶段1结果
            story_result: 剧情结果
            relationship_result: 关系结果
            character_entity_result: 角色实体结果
        """
        self.checkpoint_manager.mark_stage_completed("completed", {
            "novel_id": self.novel_id,
            "chapters_count": len(self.chapter_ids),
            "summaries_count": stage1_result.get("summaries_count", 0) if stage1_result else 0,
            "plots_count": stage1_result.get("plots_count", 0) if stage1_result else 0,
            "mentions_extracted": stage1_result.get("mentions_extracted", False) if stage1_result else False,
            "failed_count": stage1_result.get("failed_count", 0) if stage1_result else 0,
            "failed_chapters": stage1_result.get("failed_chapters", []) if stage1_result else [],
            "failed_mention_chapters": stage1_result.get("failed_mention_chapters", []) if stage1_result else [],
            "synopsis_generated": story_result.get("synopsis_generated", False) if story_result else False,
            "stories_count": story_result.get("stories_count", 0) if story_result else 0,
            "storylines_count": story_result.get("storylines_count", 0) if story_result else 0,
            "failed_stories": story_result.get("failed_stories", []) if story_result else [],
            "relationships_count": relationship_result.get("relationships_count", 0) if relationship_result else 0,
            "neo4j_persisted": relationship_result.get("neo4j_persisted", False) if relationship_result else False,
            "neo4j_failed_chapters": relationship_result.get("neo4j_failed_chapters", []) if relationship_result else [],
            "characters_created": character_entity_result.get("created_count", 0) if character_entity_result else 0,
            "characters_updated": character_entity_result.get("updated_count", 0) if character_entity_result else 0,
            "character_failed_count": character_entity_result.get("failed_count", 0) if character_entity_result else 0,
            "failed_characters": character_entity_result.get("failed_characters", []) if character_entity_result else [],
            "meta_extraction": (stage1_result or {}).get("meta_extraction"),
            "status": self._derive_final_status(
                stage1_result,
                story_result,
                relationship_result,
                character_entity_result,
            ),
        })
