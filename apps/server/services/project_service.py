"""项目创建的共享实现：新项目与它的默认文件夹结构。

Web 端 `POST /api/v1/projects` 与 Agent API `POST /api/v1/agent/projects` 都走这里，
保证两个入口创建出的项目有同一套按 project_type 生成的根文件夹（设定/角色/素材/大纲/正文……）。
历史上 Agent API 只插入 Project 行，外部 Agent 建出的项目是空的，只能自己手建文件夹，
而站内 Agent 工具依赖可预测的根文件夹 id（`{project_id}-draft-folder`）。

按套餐的项目数上限（402 QUOTA_PROJECTS_EXCEEDED）和 `project_created` 激活事件也在这里，
而不是留给每个入口各写一遍：Agent API 早期版本就漏掉了上限检查，外部 Agent 可以无限建项目。
入口只负责鉴权和把其它异常翻译成自己的错误契约（配额的 APIException 需原样放行）。
项目行与默认文件夹在同一个事务里：文件夹初始化失败时项目行也不会落库。

复制灵感（api/inspirations.py）不走这里，它自己做一次上限检查。
"""

import logging

from sqlmodel import Session

from config.project_templates import get_folders_for_type
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import ACTIVATION_EVENT_PROJECT_CREATED, File, Project
from services.features.activation_event_service import activation_event_service
from services.quota_service import quota_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

__all__ = [
    "create_project_with_default_folders",
    "resolve_template_lang",
]

_DEFAULT_LANG = "zh"


def resolve_template_lang(accept_language: str | None) -> str:
    """从 Accept-Language 取第一个语言的主标签（'zh-CN,zh;q=0.9' -> 'zh'），缺省 zh。

    未知语言不在这里报错：模板查找会回落到中文模板。
    """
    if not accept_language:
        return _DEFAULT_LANG
    return accept_language.split(",")[0].split("-")[0].strip() or _DEFAULT_LANG


def create_project_with_default_folders(
    session: Session,
    project: Project,
    lang: str = _DEFAULT_LANG,
) -> list[File]:
    """检查项目数上限，持久化 `project` 并按其 project_type 创建默认根文件夹（一次提交），
    再记录 `project_created` 激活事件（尽力而为，失败只记日志）。

    文件夹 id 可预测：`{project_id}-{folder_id}`（例如 `{project_id}-draft-folder`）。

    Returns:
        新建的文件夹（按模板 order 排列）。

    Raises:
        APIException: 402 QUOTA_PROJECTS_EXCEEDED——所有者已达套餐项目数上限（未写入任何数据）。
        其它异常都会先回滚事务再原样抛出，调用方负责翻译成自己的错误契约。
    """
    try:
        allowed, existing_count, max_projects = quota_service.check_project_limit(
            session, project.owner_id, for_creation=True
        )
        if not allowed:
            raise APIException(
                error_code=ErrorCode.QUOTA_PROJECTS_EXCEEDED,
                status_code=402,
                detail=f"Project limit reached ({existing_count}/{max_projects}). Please upgrade your plan.",
            )

        session.add(project)
        session.flush()  # 先拿到 project.id，文件夹 id 依赖它

        folders: list[File] = []
        for folder_config in get_folders_for_type(project.project_type, lang):
            folder = File(
                id=f"{project.id}-{folder_config['id']}",
                project_id=project.id,
                title=folder_config["title"],
                file_type=folder_config["file_type"],
                order=folder_config["order"],
                parent_id=None,
            )
            session.add(folder)
            folders.append(folder)

        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(project)
    for folder in folders:
        session.refresh(folder)

    try:
        activation_event_service.record_once(
            session,
            user_id=project.owner_id,
            event_name=ACTIVATION_EVENT_PROJECT_CREATED,
            project_id=project.id,
            event_metadata={"project_type": project.project_type},
        )
    except Exception as e:
        log_with_context(
            logger,
            logging.WARNING,
            "Failed to record project_created activation event",
            user_id=project.owner_id,
            project_id=project.id,
            error=str(e),
            error_type=type(e).__name__,
        )
    return folders
