"""
File operations for agent tools.

- serialization.py: file serialization
- text_matching.py: fuzzy and approximate text matching
- crud.py: create, update, delete, query
- edit.py: precise content editing
- project.py: project status and task board
- executor.py: FileToolExecutor, combining the three operation classes
"""

from .crud import FileCRUD
from .edit import FileEditor
from .executor import FileToolExecutor
from .project import ProjectOperations
from .serialization import serialize_file
from .text_matching import (
    build_span_previews as _build_span_previews,
)
from .text_matching import (
    find_approximate_match as _find_approximate_match,
)
from .text_matching import (
    find_fuzzy_spans as _find_fuzzy_spans,
)
from .text_matching import (
    find_unique_line_span as _find_unique_line_span,
)

# Text matching utilities (internal helpers with underscore prefix)
from .text_matching import (
    normalize_for_fuzzy_match as _normalize_for_fuzzy_match,
)
from .text_matching import (
    suggest_similar_lines as _suggest_similar_lines,
)

__all__ = [
    # Main entry points
    "FileToolExecutor",
    # Serialization
    "serialize_file",
    # Operation classes (for direct use if needed)
    "FileCRUD",
    "FileEditor",
    "ProjectOperations",
    # Text matching utilities (internal, underscore-prefixed)
    "_normalize_for_fuzzy_match",
    "_find_fuzzy_spans",
    "_find_approximate_match",
    "_build_span_previews",
    "_suggest_similar_lines",
    "_find_unique_line_span",
]
