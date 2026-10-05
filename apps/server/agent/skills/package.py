"""
标准技能包（Agent Skills / SKILL.md）的解析与打包。

一个技能 = 一个目录：`SKILL.md`（YAML frontmatter 必填 `name`、`description`，
可选 `license`、`compatibility`、`metadata`、`allowed-tools`）+ 可选的
`references/`、`assets/` 文本资源。

zenstory 永不执行技能携带的代码：导入时 `scripts/` 下的文件与非文本文件一律丢弃并
在 warnings 中说明。`allowed-tools` 只做解析、存储与再导出，没有任何运行时效果。

zenstory 自有字段放在 `metadata.zenstory.*`（`display_name`、`triggers`、`category`），
导出的包其他 agent 能用，导入外部标准技能也不会因为缺少这些字段而失败。

本模块只有纯函数，不触碰数据库。
"""

from __future__ import annotations

import io
import json
import math
import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

import yaml

# ==================== 限额（前后端共享口径，改动需同步前端文案） ====================

MAX_RESOURCES_PER_SKILL = 20
MAX_RESOURCE_BYTES = 64 * 1024
MAX_TOTAL_RESOURCE_BYTES = 256 * 1024
MAX_ZIP_BYTES = 1024 * 1024
MAX_ZIP_ENTRIES = 200
# 包内所有条目「声明的」解压后总大小上限（含会被丢弃的条目），防 zip bomb。
MAX_ZIP_UNCOMPRESSED_BYTES = 4 * 1024 * 1024
MAX_INSTRUCTIONS_CHARS = 50_000
# SKILL.md 单文件上限：正文 5 万字符按 UTF-8 最坏 3 字节计，再留出 frontmatter 余量。
MAX_SKILL_MD_BYTES = 256 * 1024
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 1024
MAX_RESOURCE_PATH_CHARS = 255
MAX_SLUG_CHARS = 64
MAX_TRIGGERS = 50
MAX_TRIGGER_CHARS = 100
# YAML frontmatter 文本上限（UTF-8 字节）；frontmatter 只放元数据，正文不受此限。
MAX_FRONTMATTER_BYTES = 16 * 1024
# skill_metadata 列（license / compatibility / allowed_tools / metadata）序列化后的上限。
MAX_SKILL_METADATA_BYTES = 16 * 1024
# metadata 树的嵌套深度与节点总数上限。
MAX_METADATA_DEPTH = 10
MAX_METADATA_NODES = 1000

ALLOWED_RESOURCE_EXTENSIONS: tuple[str, ...] = (".md", ".txt", ".json", ".yaml", ".yml", ".csv")
ALLOWED_RESOURCE_ROOTS: tuple[str, ...] = ("references/", "assets/")

SKILL_MD_FILENAME = "SKILL.md"

# 打包时间固定，导出结果可复现（同一技能两次导出字节一致）。
_ZIP_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# 打包/解包时静默跳过的系统垃圾文件（不是用户内容，不值得一条 warning）。
_IGNORED_ZIP_PREFIXES = ("__MACOSX/",)
_IGNORED_ZIP_BASENAMES = {".DS_Store", "Thumbs.db"}

_DRIVE_LETTER_PATTERN = re.compile(r"^[A-Za-z]:")
_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def strip_nul_chars(text: str) -> str:
    """删除 NUL（U+0000）。PostgreSQL 的 TEXT/VARCHAR 不接受 NUL，原样写入会 500。"""
    return text.replace("\x00", "") if "\x00" in text else text


class SkillPackageError(ValueError):
    """技能包不合法。

    kind:
        - "invalid": 格式/路径/编码问题（HTTP 400）
        - "too_large": 超出数量或大小限额（HTTP 413）
    """

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class ParsedSkill:
    """解析后的技能（与存储无关的中间表示）。"""

    name: str
    description: str
    instructions: str
    triggers: list[str] = field(default_factory=list)
    category: str | None = None
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    # 标准 frontmatter 的 metadata 映射；`metadata.zenstory` 里已经落到独立字段的
    # display_name / triggers 会被剔除，其余 zenstory 扩展字段原样保留。
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_skill_metadata(self) -> dict[str, Any]:
        """转成数据库 `skill_metadata` 列存储的 JSON 对象（只保留非空键）。"""
        stored: dict[str, Any] = {}
        if self.license:
            stored["license"] = self.license
        if self.compatibility:
            stored["compatibility"] = self.compatibility
        if self.allowed_tools:
            stored["allowed_tools"] = list(self.allowed_tools)
        if self.metadata:
            stored["metadata"] = dict(self.metadata)
        return stored


# ==================== zenstory 旧原生格式（`# 标题` / `## Triggers` / `## Instructions`） ====================

# 「结构性二级标题」白名单（小写匹配）。只有白名单里的 `## xxx` 才切换解析区段；其余 `## xxx`
# 一律视为正文内容。刻意只收录原有的两个英文节名：正文里出现同名中文小标题（如「## 指令」）的
# 概率远高于它被用作结构性节标题，扩大白名单反而会把正文吃掉。
KNOWN_SECTION_NAMES: dict[str, str] = {
    "triggers": "triggers",
    "instructions": "instructions",
}

# 支持的 markdown 代码围栏标记
_FENCE_MARKERS = ("```", "~~~")


def _fence_marker(line_stripped: str) -> str | None:
    """返回该行开启/关闭的代码围栏标记，非围栏行返回 None。"""
    for marker in _FENCE_MARKERS:
        if line_stripped.startswith(marker):
            return marker
    return None


def _fenced_line_indices(lines: list[str]) -> set[int]:
    """
    计算所有位于「成对闭合」的代码围栏之内（含围栏行本身）的行号。

    只认成对的围栏：若文件末尾还有一个没闭合的围栏，就不把它之后的内容算作围栏内，
    以免一个笔误的 ``` 把后续的 `## Instructions` 节标题一起吞掉。
    """
    fenced: set[int] = set()
    open_index: int | None = None
    open_marker: str | None = None

    for index, line in enumerate(lines):
        marker = _fence_marker(line.strip())
        if marker is None:
            continue
        if open_index is None:
            open_index = index
            open_marker = marker
        elif marker == open_marker:
            fenced.update(range(open_index, index + 1))
            open_index = None
            open_marker = None

    return fenced


def _append_content_line(
    line: str,
    line_stripped: str,
    current_section: str,
    description_lines: list[str],
    triggers: list[str],
    instructions_lines: list[str],
) -> None:
    """把一行正文追加到当前区段（描述/触发词/指令）。"""
    if current_section == "description":
        if line_stripped:
            description_lines.append(line_stripped)
    elif current_section == "triggers":
        if line_stripped.startswith("- "):
            triggers.append(line_stripped[2:].strip())
    elif current_section == "instructions":
        instructions_lines.append(line)


def parse_legacy_skill_md(content: str) -> ParsedSkill | None:
    """解析 zenstory 旧原生格式；没有 `# 标题` 时返回 None。"""
    lines = content.strip().split("\n")

    name = ""
    description_lines: list[str] = []
    triggers: list[str] = []
    instructions_lines: list[str] = []

    current_section = "description"

    # 技能正文经常在代码围栏里给「输出格式模板」，模板里的 `# `/`## ` 是正文而不是节标题，
    # 必须原样保留，否则技能教给模型的结构会被静默吃掉。
    fenced_indices = _fenced_line_indices(lines)

    for index, line in enumerate(lines):
        line_stripped = line.strip()

        # 围栏内（含围栏行本身）的一切都是正文，不参与节标题判定
        if index in fenced_indices:
            _append_content_line(
                line, line_stripped, current_section,
                description_lines, triggers, instructions_lines,
            )
            continue

        if line_stripped.startswith("# ") and not name:
            name = line_stripped[2:].strip()
            continue

        if line_stripped.startswith("## "):
            section_name = line_stripped[3:].strip().lower()
            if section_name in KNOWN_SECTION_NAMES:
                current_section = KNOWN_SECTION_NAMES[section_name]
                continue
            # 非白名单的二级标题：在 instructions 里属于正文小节标题，原样保留；
            # 在 instructions 之前（描述区）仍按结构性标题忽略，保持既有行为。
            if current_section == "instructions":
                instructions_lines.append(line)
            continue

        _append_content_line(
            line, line_stripped, current_section,
            description_lines, triggers, instructions_lines,
        )

    if not name:
        return None

    return ParsedSkill(
        name=name,
        description=" ".join(description_lines),
        triggers=triggers,
        instructions="\n".join(instructions_lines).strip(),
    )


# ==================== 标准 frontmatter 格式 ====================


def _split_frontmatter(content: str) -> tuple[str, str] | None:
    """拆出 `---` 包裹的 YAML frontmatter 与正文；不是 frontmatter 格式时返回 None。"""
    # 分隔符只认顶格的 `---`：YAML 块标量里缩进的 `---`（如描述中的 markdown 分隔线）是内容，
    # 用 strip() 判定会把 frontmatter 提前截断，导出后再导入就解析失败。
    lines = content.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1:])
    return None


class _NoAliasSafeLoader(yaml.SafeLoader):
    """拒绝锚点与别名的 SafeLoader。

    别名会被展开成共享引用，几百字节的 frontmatter 就能在 json.dumps 时膨胀成 GB 级
    （billion laughs）。技能元数据没有合理的锚点用途，一律拒收。
    """

    def compose_node(self, parent, index):  # type: ignore[no-untyped-def]
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise SkillPackageError("SKILL.md 的 YAML frontmatter 不能使用锚点或别名（& / *）")
        return super().compose_node(parent, index)


def _to_json_safe(value: Any, field_name: str, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    """
    把 YAML 解析结果转换成可 JSON 序列化的树，并限制深度与节点数。

    日期/时间转 ISO 字符串；集合、二进制等其他 YAML 类型拒收（SkillPackageError → 400）。
    """
    if budget is None:
        budget = [MAX_METADATA_NODES]
    budget[0] -= 1
    if budget[0] < 0:
        raise SkillPackageError(f"SKILL.md 的 {field_name} 包含的条目过多")
    if depth > MAX_METADATA_DEPTH:
        raise SkillPackageError(f"SKILL.md 的 {field_name} 嵌套层级过深")

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SkillPackageError(f"SKILL.md 的 {field_name} 不能包含 NaN 或无穷大")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [
            _to_json_safe(item, field_name, depth=depth + 1, budget=budget) for item in value
        ]
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, (datetime, date)):
                key_text = key.isoformat()
            elif key is None or isinstance(key, (bool, int, float, str)):
                key_text = str(key)
            else:
                raise SkillPackageError(f"SKILL.md 的 {field_name} 的键必须是字符串")
            converted[key_text] = _to_json_safe(item, field_name, depth=depth + 1, budget=budget)
        return converted
    raise SkillPackageError(
        f"SKILL.md 的 {field_name} 包含不支持的 YAML 类型：{type(value).__name__}"
    )


def serialize_skill_metadata(skill: ParsedSkill) -> str:
    """序列化 `skill_metadata` 列，超过 MAX_SKILL_METADATA_BYTES 时抛 SkillPackageError。"""
    text = json.dumps(skill.to_skill_metadata(), ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_SKILL_METADATA_BYTES:
        raise SkillPackageError(
            f"SKILL.md 的元数据（license / compatibility / allowed-tools / metadata）"
            f"不能超过 {MAX_SKILL_METADATA_BYTES // 1024} KiB",
            kind="too_large",
        )
    return text


def _as_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return text or None
    raise SkillPackageError(f"SKILL.md 的 {field_name} 必须是字符串")


def _as_str_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in value.split() if item]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                raise SkillPackageError(f"SKILL.md 的 {field_name} 只能包含字符串")
            text = str(item).strip()
            if text:
                items.append(text)
        return items
    raise SkillPackageError(f"SKILL.md 的 {field_name} 必须是字符串或字符串列表")


def _parse_frontmatter_skill(frontmatter_text: str, body: str) -> ParsedSkill:
    if len(frontmatter_text.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise SkillPackageError(
            f"SKILL.md 的 YAML frontmatter 不能超过 {MAX_FRONTMATTER_BYTES // 1024} KiB",
            kind="too_large",
        )
    try:
        raw = (
            yaml.load(frontmatter_text, Loader=_NoAliasSafeLoader)  # noqa: S506 - SafeLoader 子类
            if frontmatter_text.strip()
            else {}
        )
    except yaml.YAMLError as exc:
        raise SkillPackageError(f"SKILL.md 的 YAML frontmatter 无法解析：{exc}") from exc
    if not isinstance(raw, dict):
        raise SkillPackageError("SKILL.md 的 YAML frontmatter 必须是键值映射")

    metadata_raw = raw.get("metadata")
    if metadata_raw is None:
        metadata: dict[str, Any] = {}
    elif isinstance(metadata_raw, dict):
        metadata = _to_json_safe(metadata_raw, "metadata")
    else:
        raise SkillPackageError("SKILL.md 的 metadata 必须是键值映射")

    zenstory_raw = metadata.get("zenstory")
    zenstory: dict[str, Any] = dict(zenstory_raw) if isinstance(zenstory_raw, dict) else {}

    display_name = _as_optional_str(zenstory.pop("display_name", None), "metadata.zenstory.display_name")
    frontmatter_name = _as_optional_str(raw.get("name"), "name")
    name = display_name or frontmatter_name
    if not name:
        raise SkillPackageError("SKILL.md 的 frontmatter 缺少 name")

    description = _as_optional_str(raw.get("description"), "description")
    if not description:
        raise SkillPackageError("SKILL.md 的 frontmatter 缺少 description")

    # 触发词：metadata.zenstory.triggers 优先；兼容旧版把 triggers 写在顶层的 SKILL.md。
    if "triggers" in zenstory:
        triggers = _as_str_list(zenstory.pop("triggers"), "metadata.zenstory.triggers")
    else:
        triggers = _as_str_list(raw.get("triggers"), "triggers")

    category = _as_optional_str(zenstory.get("category"), "metadata.zenstory.category")

    if zenstory:
        metadata["zenstory"] = zenstory
    else:
        metadata.pop("zenstory", None)

    allowed_tools_raw = raw.get("allowed-tools", raw.get("allowed_tools"))

    return ParsedSkill(
        name=name,
        description=description,
        instructions=body.strip(),
        triggers=triggers,
        category=category,
        license=_as_optional_str(raw.get("license"), "license"),
        compatibility=_as_optional_str(raw.get("compatibility"), "compatibility"),
        allowed_tools=_as_str_list(allowed_tools_raw, "allowed-tools"),
        metadata=metadata,
    )


def _validate_parsed_skill(skill: ParsedSkill) -> ParsedSkill:
    """只做长度与非空校验；名称不强制标准的 slug 规则（允许中文显示名）。"""
    # YAML 双引号字符串的 "\0" 转义也能产生 NUL，解码阶段清不到，这里再清一次。
    skill.name = strip_nul_chars(skill.name).strip()
    skill.description = strip_nul_chars(skill.description or "").strip()
    skill.instructions = strip_nul_chars(skill.instructions)
    skill.triggers = [strip_nul_chars(trigger) for trigger in skill.triggers]
    if not skill.name:
        raise SkillPackageError("技能名称不能为空")
    if len(skill.name) > MAX_NAME_CHARS:
        raise SkillPackageError(f"技能名称不能超过 {MAX_NAME_CHARS} 个字符")
    if len(skill.description) > MAX_DESCRIPTION_CHARS:
        raise SkillPackageError(f"技能描述不能超过 {MAX_DESCRIPTION_CHARS} 个字符")
    if not skill.instructions.strip():
        raise SkillPackageError("SKILL.md 正文（技能指令）为空")
    if len(skill.instructions) > MAX_INSTRUCTIONS_CHARS:
        raise SkillPackageError(
            f"技能指令不能超过 {MAX_INSTRUCTIONS_CHARS} 个字符",
            kind="too_large",
        )
    if len(skill.triggers) > MAX_TRIGGERS:
        raise SkillPackageError(f"触发词不能超过 {MAX_TRIGGERS} 个")
    if any(len(trigger) > MAX_TRIGGER_CHARS for trigger in skill.triggers):
        raise SkillPackageError(f"单个触发词不能超过 {MAX_TRIGGER_CHARS} 个字符")
    serialize_skill_metadata(skill)
    return skill


def parse_skill_md(text: str) -> ParsedSkill:
    """
    解析 SKILL.md 文本。

    支持两种格式：
    1. 标准格式：YAML frontmatter（`name`、`description` 必填）+ markdown 正文。
       名称优先取 `metadata.zenstory.display_name`，其次 frontmatter `name`。
    2. zenstory 旧原生格式：`# 标题` / 描述段 / `## Triggers` / `## Instructions`。

    Raises:
        SkillPackageError: 无法解析或超出长度限制
    """
    content = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")

    split = _split_frontmatter(content)
    if split is not None:
        frontmatter_text, body = split
        return _validate_parsed_skill(_parse_frontmatter_skill(frontmatter_text, body))

    legacy = parse_legacy_skill_md(content)
    if legacy is None:
        raise SkillPackageError("无法识别的 SKILL.md：缺少 YAML frontmatter（name / description）")
    return _validate_parsed_skill(legacy)


# ==================== 资源路径 ====================


# 路径里一律拒收的 Unicode 类别：控制字符（Cc，含 DEL 与 C1）、格式字符（Cf，含双向覆盖与零宽字符）、
# 代理项（Cs）、行/段分隔符（Zl / Zp）。
_UNSAFE_PATH_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def normalize_resource_path(path: str | None) -> str:
    """资源路径规整：去首尾空白 + NFC，保证外观相同的路径只有一种写法。"""
    return unicodedata.normalize("NFC", (path or "").strip())


def _is_unsafe_path(path: str) -> bool:
    """路径穿越 / 绝对路径 / 反斜杠 / 盘符 / 控制与格式字符（调用方需先做 NFC）。"""
    if not path or "\\" in path:
        return True
    if path.startswith("/") or _DRIVE_LETTER_PATTERN.match(path):
        return True
    if any(unicodedata.category(ch) in _UNSAFE_PATH_CATEGORIES for ch in path):
        return True
    return any(part in ("..", ".") for part in path.split("/"))


def validate_resource_path(path: str) -> str:
    """
    校验技能资源路径（导入与单文件编辑共用同一套规则）。

    必须位于 references/ 或 assets/ 下、扩展名在白名单内、不含穿越成分。

    Returns:
        规整后的路径（去掉首尾空白并做 NFC 归一化）

    Raises:
        SkillPackageError: 路径不合法
    """
    normalized = normalize_resource_path(path)
    if _is_unsafe_path(normalized):
        raise SkillPackageError(f"资源路径不合法：{path!r}")
    if len(normalized) > MAX_RESOURCE_PATH_CHARS:
        raise SkillPackageError(f"资源路径不能超过 {MAX_RESOURCE_PATH_CHARS} 个字符")
    if not normalized.startswith(ALLOWED_RESOURCE_ROOTS):
        raise SkillPackageError("资源路径必须以 references/ 或 assets/ 开头")
    parts = normalized.split("/")
    if any(not part for part in parts) or len(parts) < 2:
        raise SkillPackageError(f"资源路径不合法：{path!r}")
    if PurePosixPath(normalized).suffix.lower() not in ALLOWED_RESOURCE_EXTENSIONS:
        raise SkillPackageError(
            "资源文件只支持文本类型：" + " ".join(ALLOWED_RESOURCE_EXTENSIONS)
        )
    return normalized


def validate_resource_content(content: str) -> int:
    """校验单个资源内容大小，返回其 UTF-8 字节数。"""
    size = len(content.encode("utf-8"))
    if size > MAX_RESOURCE_BYTES:
        raise SkillPackageError(
            f"单个资源文件不能超过 {MAX_RESOURCE_BYTES // 1024} KiB",
            kind="too_large",
        )
    return size


# ==================== zip 导入 ====================


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int, label: str) -> bytes:
    """按上限读取条目：同时核对声明大小与实际读出字节（压缩炸弹可以谎报声明大小）。"""
    if info.file_size > limit:
        raise SkillPackageError(f"{label} 超出大小限制", kind="too_large")
    try:
        with zf.open(info) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as exc:
        # 声明大小与实际内容不符（CRC 校验失败）、压缩数据损坏、加密条目等
        raise SkillPackageError(f"{label} 已损坏或无法读取") from exc
    if len(data) > limit:
        raise SkillPackageError(f"{label} 超出大小限制", kind="too_large")
    return data


def _decode_utf8(data: bytes, label: str) -> str:
    try:
        return strip_nul_chars(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SkillPackageError(f"{label} 不是 UTF-8 文本") from exc


def _is_ignored_entry(name: str) -> bool:
    return name.startswith(_IGNORED_ZIP_PREFIXES) or PurePosixPath(name).name in _IGNORED_ZIP_BASENAMES


def _is_skill_md(info: zipfile.ZipInfo) -> bool:
    return PurePosixPath(info.filename).name.lower() == SKILL_MD_FILENAME.lower()


def _locate_skill_md(file_infos: list[zipfile.ZipInfo]) -> zipfile.ZipInfo:
    """
    找到包里唯一的技能入口 SKILL.md。

    - 根部有 SKILL.md：以它为准；`references/`、`assets/`、`scripts/` 下同名文件只是资源/脚本。
      若同时存在其他一级目录下的 SKILL.md（包里装了第二个技能），视为歧义拒收。
    - 根部没有：取一级目录下的 SKILL.md（`my-skill/SKILL.md`），多于一个视为歧义拒收。
    """
    root_candidates = [info for info in file_infos if _is_skill_md(info) and "/" not in info.filename]
    nested_candidates = [
        info for info in file_infos if _is_skill_md(info) and info.filename.count("/") == 1
    ]
    if len(root_candidates) > 1:
        raise SkillPackageError("技能包中包含多个 SKILL.md，每个包只能有一个技能")
    if root_candidates:
        resource_dirs = (*ALLOWED_RESOURCE_ROOTS, "scripts/")
        if any(not info.filename.startswith(resource_dirs) for info in nested_candidates):
            raise SkillPackageError("技能包中包含多个 SKILL.md，每个包只能有一个技能")
        return root_candidates[0]
    if not nested_candidates:
        raise SkillPackageError("技能包中没有找到 SKILL.md")
    if len(nested_candidates) > 1:
        raise SkillPackageError("技能包中包含多个 SKILL.md，每个包只能有一个技能")
    return nested_candidates[0]


def read_skill_zip(data: bytes) -> tuple[ParsedSkill, list[tuple[str, str]], list[str]]:
    """
    读取标准技能 zip 包。

    zip 里可以有一个顶层目录（`my-skill/SKILL.md`），也可以把文件直接放在根部。

    - 拒收（抛 SkillPackageError）：路径穿越/绝对路径/反斜杠/盘符、符号链接、条目数或大小超限、
      压缩炸弹、非 UTF-8 文本、找不到或找到多个 SKILL.md。
    - 丢弃并写入 warnings：`scripts/**`、非白名单扩展名、references/ 与 assets/ 之外的文件。

    Returns:
        (技能, [(资源路径, 内容)], warnings)
    """
    if len(data) > MAX_ZIP_BYTES:
        raise SkillPackageError(
            f"技能包不能超过 {MAX_ZIP_BYTES // 1024 // 1024} MiB", kind="too_large"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise SkillPackageError("不是有效的 zip 文件") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SkillPackageError(
                f"技能包内文件数不能超过 {MAX_ZIP_ENTRIES} 个", kind="too_large"
            )

        declared_total = 0
        file_infos: list[zipfile.ZipInfo] = []
        for info in infos:
            name = info.filename
            if _is_unsafe_path(unicodedata.normalize("NFC", name.rstrip("/"))):
                raise SkillPackageError(f"技能包包含不安全的路径：{name!r}")
            if _is_symlink(info):
                raise SkillPackageError(f"技能包不能包含符号链接：{name!r}")
            declared_total += max(info.file_size, 0)
            if info.is_dir() or _is_ignored_entry(name):
                continue
            file_infos.append(info)

        if declared_total > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise SkillPackageError("技能包解压后的总大小超出限制", kind="too_large")

        skill_md_info = _locate_skill_md(file_infos)
        root_prefix = skill_md_info.filename[: -len(PurePosixPath(skill_md_info.filename).name)]

        skill_md_text = _decode_utf8(
            _read_entry(zf, skill_md_info, MAX_SKILL_MD_BYTES, SKILL_MD_FILENAME),
            SKILL_MD_FILENAME,
        )
        skill = parse_skill_md(skill_md_text)

        resources: list[tuple[str, str]] = []
        warnings: list[str] = []
        total_bytes = 0
        seen_paths: set[str] = set()

        for info in file_infos:
            if info is skill_md_info:
                continue
            name = info.filename
            if not name.startswith(root_prefix):
                warnings.append(f"已忽略技能目录之外的文件：{name}")
                continue
            rel_path = name[len(root_prefix):]

            if rel_path.startswith("scripts/"):
                warnings.append(f"已忽略脚本文件：{rel_path}（zenstory 不执行技能脚本）")
                continue
            if not rel_path.startswith(ALLOWED_RESOURCE_ROOTS):
                warnings.append(f"已忽略 references/ 与 assets/ 之外的文件：{rel_path}")
                continue
            if PurePosixPath(rel_path).suffix.lower() not in ALLOWED_RESOURCE_EXTENSIONS:
                warnings.append(f"已忽略非文本文件：{rel_path}")
                continue

            path = validate_resource_path(rel_path)
            if path in seen_paths:
                raise SkillPackageError(f"技能包中包含重复的资源路径：{path}")
            seen_paths.add(path)
            raw = _read_entry(zf, info, MAX_RESOURCE_BYTES, path)
            content = _decode_utf8(raw, path)
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_RESOURCE_BYTES:
                raise SkillPackageError(
                    f"资源文件总大小不能超过 {MAX_TOTAL_RESOURCE_BYTES // 1024} KiB",
                    kind="too_large",
                )
            resources.append((path, content))
            if len(resources) > MAX_RESOURCES_PER_SKILL:
                raise SkillPackageError(
                    f"资源文件不能超过 {MAX_RESOURCES_PER_SKILL} 个", kind="too_large"
                )

    resources.sort(key=lambda item: item[0])
    return skill, resources, warnings


def read_skill_md_upload(data: bytes) -> ParsedSkill:
    """单文件 `.md` 上传：按 SKILL.md 解析，不带资源。"""
    if len(data) > MAX_SKILL_MD_BYTES:
        raise SkillPackageError(
            f"SKILL.md 不能超过 {MAX_SKILL_MD_BYTES // 1024} KiB", kind="too_large"
        )
    return parse_skill_md(_decode_utf8(data, SKILL_MD_FILENAME))


# ==================== zip 导出 ====================


def slugify_skill_name(name: str, skill_id: str) -> str:
    """标准 `name` 字段：小写 ascii、[a-z0-9-]、≤64；中文名等无 ascii 时退回 `skill-<id 前 8 位>`。"""
    slug = _SLUG_INVALID_CHARS.sub("-", (name or "").lower()).strip("-")
    slug = slug[:MAX_SLUG_CHARS].strip("-")
    if slug:
        return slug
    id_part = _SLUG_INVALID_CHARS.sub("", (skill_id or "").lower())[:8]
    return f"skill-{id_part}" if id_part else "skill"


def build_skill_md(skill: ParsedSkill, *, skill_id: str) -> str:
    """生成标准 SKILL.md 文本。"""
    zenstory_meta: dict[str, Any] = {}
    existing_zenstory = skill.metadata.get("zenstory")
    if isinstance(existing_zenstory, dict):
        zenstory_meta.update(existing_zenstory)
    zenstory_meta["display_name"] = skill.name
    zenstory_meta["triggers"] = list(skill.triggers)
    if skill.category:
        zenstory_meta["category"] = skill.category

    metadata = {key: value for key, value in skill.metadata.items() if key != "zenstory"}
    metadata["zenstory"] = zenstory_meta

    frontmatter: dict[str, Any] = {
        "name": slugify_skill_name(skill.name, skill_id),
        "description": skill.description or skill.name,
    }
    if skill.license:
        frontmatter["license"] = skill.license
    if skill.compatibility:
        frontmatter["compatibility"] = skill.compatibility
    if skill.allowed_tools:
        frontmatter["allowed-tools"] = " ".join(skill.allowed_tools)
    frontmatter["metadata"] = metadata

    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"---\n{yaml_text}---\n\n{skill.instructions.strip()}\n"


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=_ZIP_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_skill_zip(
    skill: ParsedSkill,
    resources: list[tuple[str, str]],
    *,
    skill_id: str,
) -> bytes:
    """打包为 `<slug>/SKILL.md` + 资源文件的标准技能 zip。"""
    slug = slugify_skill_name(skill.name, skill_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_zip_info(f"{slug}/{SKILL_MD_FILENAME}"), build_skill_md(skill, skill_id=skill_id))
        for path, content in sorted(resources, key=lambda item: item[0]):
            zf.writestr(_zip_info(f"{slug}/{path}"), content)
    return buffer.getvalue()


def export_filename(skill: ParsedSkill, *, skill_id: str) -> str:
    """导出文件名（ascii，适合放进 Content-Disposition）。"""
    return f"{slugify_skill_name(skill.name, skill_id)}.zip"
