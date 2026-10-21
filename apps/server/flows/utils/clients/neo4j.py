"""
Neo4j 客户端实现（归类至 clients 包）

用于存储和查询人物关系图谱。
"""
import contextlib
import random
import time
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import TransientError
from prefect import get_run_logger

from config.material_settings import material_settings as settings


class Neo4jClient:
    """Neo4j 客户端封装"""

    def __init__(self) -> None:
        """初始化 Neo4j 客户端"""
        self.driver = None
        self._connect()

    def _connect(self) -> None:
        """连接到 Neo4j 数据库"""
        try:
            self.driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            )
            # 探测连接
            with self.driver.session() as session:
                session.run("RETURN 1")
        except Exception as exc:  # noqa: BLE001 - 需兼容外部驱动异常
            logger = get_run_logger()
            logger.warning(f"Neo4j 连接失败: {exc}")
            self.driver = None

    def close(self) -> None:
        """关闭连接"""
        if self.driver:
            with contextlib.suppress(Exception):
                self.driver.close()

    def is_available(self) -> bool:
        """检查 Neo4j 是否可用"""
        return self.driver is not None

    def ensure_constraints(self) -> None:
        """确保常用唯一约束存在"""
        if not self.is_available():
            return

        queries = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Character) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (ch:Chapter) REQUIRE ch.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Novel) REQUIRE n.id IS UNIQUE",
        ]

        with self.driver.session() as session:
            for query in queries:
                try:
                    session.run(query)
                except Exception:
                    # 兼容旧版本或托管服务不支持的语法
                    continue

    def persist_chapter_relationships(
        self,
        novel_id: int,
        chapter_id: int,
        relationships: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        将人物关系的版本快照写入 Neo4j（按章节生成一次快照）。

        优化策略：
        1. 批量预创建所有 Character 节点，减少并发 MERGE 冲突
        2. 分批写入关系，控制单次事务大小
        3. 添加更长的重试退避时间和随机抖动
        """
        logger = get_run_logger()

        if not self.is_available():
            logger.warning("Neo4j 未配置或驱动不可用，跳过图谱写入。")
            return {
                "skip": True,
                "reason": "neo4j_unavailable",
                "written": 0,
                "chapter_id": chapter_id,
                "novel_id": novel_id,
            }

        # 生成版本键，包含 novel_id 避免跨小说冲突
        version_key = f"novel:{novel_id}:chapter:{chapter_id}"

        written = 0
        try:
            with self.driver.session() as session:
                self.ensure_constraints()

                # 先 MERGE 层级节点（一次即可）
                session.run(
                    """
MERGE (n:Novel {id: $novel_id})
MERGE (ch:Chapter {id: $chapter_id})
MERGE (n)-[:HAS_CHAPTER]->(ch)
""",
                    novel_id=novel_id,
                    chapter_id=chapter_id,
                )

                # 【优化1】批量预创建所有涉及的 Character 节点，减少后续 MERGE 冲突
                if relationships:
                    character_map = {}  # {id: name}
                    for item in relationships:
                        a_id = item.get("character_a_id")
                        b_id = item.get("character_b_id")
                        if isinstance(a_id, int):
                            character_map[a_id] = item.get("character_a") or ""
                        if isinstance(b_id, int):
                            character_map[b_id] = item.get("character_b") or ""

                    # 批量创建节点（UNWIND 批量操作，减少网络往返和锁竞争）
                    if character_map:
                        batch_create_query = """
UNWIND $characters AS char
MERGE (c:Character {id: char.id})
SET c.name = CASE WHEN char.name IS NOT NULL AND char.name <> '' THEN char.name ELSE c.name END
"""
                        character_list = [{"id": cid, "name": cname} for cid, cname in character_map.items()]

                        # 批量创建节点也可能有冲突，添加重试
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                session.run(batch_create_query, characters=character_list)
                                logger.debug(f"批量创建 {len(character_list)} 个角色节点")
                                break
                            except Exception as exc:
                                if attempt < max_retries - 1:
                                    delay = (2 ** attempt) * 0.2 + random.uniform(0, 0.1)
                                    logger.warning(f"批量创建节点失败，重试 {attempt+1}/{max_retries}，延迟 {delay:.3f}s")
                                    time.sleep(delay)
                                else:
                                    logger.error(f"批量创建节点最终失败: {exc}")
                                    raise

                # 【优化2】简化关系写入查询（节点已预创建，只需 MATCH）
                upsert_rel_query = """
MATCH (a:Character {id: $a_id})
MATCH (b:Character {id: $b_id})

MERGE (a)-[r:RELATES {version_key: $version_key}]->(b)
SET r.chapter_id = $chapter_id,
    r.novel_id = $novel_id,
    r.relationship_type = $relationship_type,
    r.sentiment = $sentiment,
    r.description = $description,
    r.current = true
WITH a,b,r
MATCH (a)-[r2:RELATES]->(b)
WHERE r2.version_key = $version_key AND r2 <> r
SET r2.current = false
WITH a,b,r
MATCH (b)-[r3:RELATES]->(a)
WHERE r3.version_key = $version_key
SET r3.current = false
"""

                for item in relationships or []:
                    params: dict[str, Any] = {}
                    try:
                        a_id = item.get("character_a_id")
                        b_id = item.get("character_b_id")

                        if not isinstance(a_id, int) or not isinstance(b_id, int):
                            continue

                        # 跳过自环
                        if a_id == b_id:
                            logger.warning(f"检测到自环关系，跳过：a_id=b_id={a_id}")
                            continue

                        # 统一方向，避免同一对人物生成双向重复边（无向语义）
                        if a_id > b_id:
                            a_id, b_id = b_id, a_id

                        params = {
                            "a_id": a_id,
                            "b_id": b_id,
                            "chapter_id": chapter_id,
                            "novel_id": novel_id,
                            "version_key": version_key,
                            "relationship_type": item.get("relationship_type") or "",
                            "sentiment": item.get("sentiment") or "",
                            "description": item.get("description") or "",
                        }

                        # 【优化3】增强重试策略：更多次数、更长退避、更多抖动
                        max_retries = 8  # 从 5 增加到 8
                        attempt = 0
                        while True:
                            try:
                                session.run(upsert_rel_query, **params)
                                written += 1
                                break
                            except Exception as exc:  # noqa: BLE001 - 记录并按需重试
                                code = getattr(exc, "code", "") or str(exc)
                                is_deadlock = (
                                    isinstance(exc, TransientError)
                                    or "DeadlockDetected" in code
                                    or "TransientError" in code
                                )
                                if is_deadlock and attempt < max_retries:
                                    # 指数退避 + 更大的随机抖动（0.2 ~ 0.5秒基数）
                                    base_delay = 0.2 * (2 ** attempt)
                                    jitter = random.uniform(0.1, 0.3)
                                    delay = base_delay + jitter

                                    # 只在前3次重试时打印警告，避免日志刷屏
                                    if attempt < 3:
                                        logger.warning(
                                            f"Neo4j 瞬时错误/死锁，准备重试 attempt={attempt+1}/{max_retries}, "
                                            f"delay={delay:.3f}s | params={{a_id:{params.get('a_id')},b_id:{params.get('b_id')},chapter_id:{chapter_id},novel_id:{novel_id}}}"
                                        )

                                    time.sleep(delay)
                                    attempt += 1
                                    continue
                                # 不可重试或达到上限：记录并跳过
                                logger.error(
                                    f"写入图谱失败（重试{attempt}次后放弃），跳过一条：{exc} | "
                                    f"params={{a_id:{params.get('a_id')},b_id:{params.get('b_id')},chapter_id:{chapter_id},novel_id:{novel_id}}}"
                                )
                                break
                    except Exception as exc:  # noqa: BLE001 - 记录并继续
                        logger.error(
                            f"写入图谱失败，跳过一条：{exc} | "
                            f"params={{a_id:{params.get('a_id')},b_id:{params.get('b_id')},chapter_id:{chapter_id},novel_id:{novel_id}}}"
                        )
                        continue
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Neo4j 写入失败: {exc}")
            return {
                "skip": True,
                "reason": "write_error",
                "error": str(exc),
                "written": written,
                "chapter_id": chapter_id,
                "novel_id": novel_id,
            }

        logger.info(f"Neo4j 写入完成：chapter_id={chapter_id}, written={written}")
        return {
            "skip": False,
            "written": written,
            "version_key": version_key,
            "chapter_id": chapter_id,
            "novel_id": novel_id,
        }

    def query_character_relationships(
        self,
        novel_id: int,
        chapter_id: int | None = None,
        character_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        查询人物关系。
        """
        logger = get_run_logger()

        if not self.is_available():
            logger.warning("Neo4j 未配置或驱动不可用")
            return []

        # 构建查询条件
        where_clauses = ["r.novel_id = $novel_id", "r.current = true"]
        params: dict[str, Any] = {"novel_id": novel_id}

        if chapter_id is not None:
            where_clauses.append("r.chapter_id = $chapter_id")
            params["chapter_id"] = chapter_id

        if character_id is not None:
            where_clauses.append("(a.id = $character_id OR b.id = $character_id)")
            params["character_id"] = character_id

        where_clause = " AND ".join(where_clauses)

        query = f"""
MATCH (a:Character)-[r:RELATES]->(b:Character)
WHERE {where_clause}
RETURN a.id AS character_a_id, a.name AS character_a,
       b.id AS character_b_id, b.name AS character_b,
       r.relationship_type AS relationship_type,
       r.sentiment AS sentiment,
       r.description AS description,
       r.chapter_id AS chapter_id
ORDER BY r.chapter_id
"""

        try:
            with self.driver.session() as session:
                result = session.run(query, **params)
                relationships: list[dict[str, Any]] = []
                for record in result:
                    relationships.append(
                        {
                            "character_a_id": record["character_a_id"],
                            "character_a": record["character_a"],
                            "character_b_id": record["character_b_id"],
                            "character_b": record["character_b"],
                            "relationship_type": record["relationship_type"],
                            "sentiment": record["sentiment"],
                            "description": record["description"],
                            "chapter_id": record["chapter_id"],
                        }
                    )
                return relationships
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Neo4j 查询失败: {exc}")
            return []


# 全局单例
_neo4j_client: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient:
    """获取 Neo4j 客户端单例"""
    global _neo4j_client
    if _neo4j_client is None:
        _neo4j_client = Neo4jClient()
    return _neo4j_client


