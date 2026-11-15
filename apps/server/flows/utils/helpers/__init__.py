#!/usr/bin/env python3
"""
辅助工具模块
"""

from .aliases import merge_aliases, normalize_alias
from .checkpoint_manager import CheckpointManager, create_checkpoint_manager
from .filters import filter_character_data, is_generic_title, is_valid_character_name
from .ingestion import (
    calculate_checksum,
    validate_input,
)
from .logging import get_logger, log_error_with_context
from .novel_parser import parse_novel_chapters
from .performance_monitor import PerformanceMonitor, create_performance_monitor
from .plot_calculator import calculate_plots_range

__all__ = [
    # Aliases
    "normalize_alias",
    "merge_aliases",
    # Filters
    "is_generic_title",
    "is_valid_character_name",
    "filter_character_data",
    # Logging
    "get_logger",
    "log_error_with_context",
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
    # Plot calculation
    "calculate_plots_range",
]
