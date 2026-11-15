"""
性能监控工具（helpers 归档）
"""
import time
from contextlib import contextmanager
from typing import Any

from prefect import get_run_logger

from config.datetime_utils import utcnow


class PerformanceMonitor:
    """性能监控器"""

    def __init__(self, flow_name: str):
        self.flow_name = flow_name
        self.logger = get_run_logger()
        self.metrics: dict[str, Any] = {}

    @contextmanager
    def measure(self, stage_name: str):
        """测量阶段耗时的上下文管理器"""
        start_time = time.perf_counter()

        self.logger.info(f"[性能监控] {stage_name} 开始")

        try:
            yield
        finally:
            elapsed = time.perf_counter() - start_time
            elapsed_ms = int(elapsed * 1000)

            self.metrics[stage_name] = {
                "elapsed_ms": elapsed_ms,
                "elapsed_seconds": round(elapsed, 2),
                "timestamp": utcnow().isoformat(),
            }

            self.logger.info(
                f"[性能监控] {stage_name} 完成: {elapsed_ms}ms ({elapsed:.2f}s)"
            )

    def get_summary(self) -> dict[str, Any]:
        """获取性能摘要"""
        total_time = sum(
            m.get("elapsed_ms", 0) for m in self.metrics.values() if isinstance(m, dict)
        )

        return {
            "flow_name": self.flow_name,
            "total_time_ms": total_time,
            "total_time_seconds": round(total_time / 1000, 2),
            "stages": self.metrics,
            "slowest_stage": self._get_slowest_stage(),
            "fastest_stage": self._get_fastest_stage(),
        }

    def _get_slowest_stage(self) -> dict[str, Any] | None:
        """获取最慢的阶段"""
        stages_with_time = [
            (name, metrics.get("elapsed_ms", 0))
            for name, metrics in self.metrics.items()
            if isinstance(metrics, dict) and "elapsed_ms" in metrics
        ]

        if not stages_with_time:
            return None

        slowest = max(stages_with_time, key=lambda x: x[1])
        return {"stage": slowest[0], "elapsed_ms": slowest[1]}

    def _get_fastest_stage(self) -> dict[str, Any] | None:
        """获取最快的阶段"""
        stages_with_time = [
            (name, metrics.get("elapsed_ms", 0))
            for name, metrics in self.metrics.items()
            if isinstance(metrics, dict) and "elapsed_ms" in metrics
        ]

        if not stages_with_time:
            return None

        fastest = min(stages_with_time, key=lambda x: x[1])
        return {"stage": fastest[0], "elapsed_ms": fastest[1]}

    def print_summary(self):
        """打印性能摘要"""
        summary = self.get_summary()

        self.logger.info("=" * 60)
        self.logger.info("性能监控摘要")
        self.logger.info("=" * 60)
        self.logger.info(f"流程: {summary['flow_name']}")
        self.logger.info(
            f"总耗时: {summary['total_time_seconds']}s ({summary['total_time_ms']}ms)"
        )

        if summary["slowest_stage"]:
            self.logger.info(
                f"最慢阶段: {summary['slowest_stage']['stage']} "
                f"({summary['slowest_stage']['elapsed_ms']}ms)"
            )

        if summary["fastest_stage"]:
            self.logger.info(
                f"最快阶段: {summary['fastest_stage']['stage']} "
                f"({summary['fastest_stage']['elapsed_ms']}ms)"
            )

        self.logger.info("\n各阶段详情:")
        for stage_name, metrics in summary["stages"].items():
            if isinstance(metrics, dict) and "elapsed_ms" in metrics:
                self.logger.info(f"  - {stage_name}: {metrics['elapsed_seconds']}s")

        self.logger.info("=" * 60)


def create_performance_monitor(flow_name: str) -> PerformanceMonitor:
    """创建性能监控器的工厂函数"""
    return PerformanceMonitor(flow_name)
