"""FileToolExecutor: the agent's single file-tool executor.

Combines FileCRUD (create / read / update / delete / query), FileEditor
(precise edit_file) and ProjectOperations (project status, task board).
The three bases share ``session`` / ``user_id`` and define no overlapping
method names, so the MRO never shadows one with another.
"""

from .crud import FileCRUD
from .edit import FileEditor
from .project import ProjectOperations


class FileToolExecutor(FileCRUD, FileEditor, ProjectOperations):
    """File CRUD, content editing and project operations bound to one session and user."""


__all__ = [
    "FileToolExecutor",
]
