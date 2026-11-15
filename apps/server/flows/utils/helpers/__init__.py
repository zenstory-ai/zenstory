#!/usr/bin/env python3
"""
辅助工具模块
"""

from .aliases import merge_aliases, normalize_alias
from .checkpoint_manager import CheckpointManager, create_checkpoint_manager
from .filters import filter_character_data, is_generic_title, is_valid_character_name
from .index import AliasEntity, build_alias_index
from .ingestion import (
    calculate_checksum,
    validate_input,
)
from .logging import get_logger, log_error_with_context, log_execution_time
from .novel_parser import parse_novel_chapters
from .performance_monitor import PerformanceMonitor, create_performance_monitor
from .plot_calculator import calculate_plots_range
from .scan import scan_chapter_for_aliases
from .text import truncate

__all__ = [
    # Aliases
    "normalize_alias",
    "merge_aliases",
    "build_alias_index",
    "AliasEntity",
    "scan_chapter_for_aliases",
    # Filters
    "is_generic_title",
    "is_valid_character_name",
    "filter_character_data",
    # Text utilities
    "truncate",
    # Logging
    "get_logger",
    "log_error_with_context",
    "log_execution_time",
    # Checkpoint management
    "CheckpointManager",
    "create_checkpoint_manager",
    # Performance monitoring
    "PerformanceMonitor",
    "create_performance_monitor",
    # Ingestion utilities
    "validate_input",
    "calculate_checksum",
    # Novel parsing
    "parse_novel_chapters",
    # Plot calculation (新增)
    "calculate_plots_range",
]
