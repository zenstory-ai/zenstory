"""任务计划板的结构化交付契约与实际文件证据。

任务声明产物类型、稳定文件引用和编号范围；实际交付由当前项目中仍有效、非空且
类型匹配的文件证明。编号优先读取兼容的 metadata，其次只认标题开头的有界语法；
单个正文范围标题不能制造多章交付。旧任务文本的编号解析仅作明确类型下的兼容输入。

writing_graph 收尾与 ProjectOperations 共用这些证据规则。任务进度由后端从已核验
文件引用重算；新的完成声明缺少证据时不写任务板，不只是向模型发出警告。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from utils.title_sequence import parse_chinese_number

_UNITS = "章集回幕卷节话"
_NUMBER = r"\d+|[零〇一二两三四五六七八九十百千]+"
_RANGE_SEP = r"[-–—~～至到]"
# 「第2-5集」「第二至第五集」「第2集-第5集」
_RANGE_RE = re.compile(
    rf"第\s*({_NUMBER})\s*([{_UNITS}])?\s*{_RANGE_SEP}\s*第?\s*({_NUMBER})\s*([{_UNITS}])"
)
# 「卷二到卷五」「卷2-5」
_PREFIX_RANGE_RE = re.compile(
    rf"(卷)\s*({_NUMBER})\s*{_RANGE_SEP}\s*卷?\s*({_NUMBER})"
)
# 「第3章」「第十二集」
_SINGLE_RE = re.compile(rf"第\s*({_NUMBER})\s*([{_UNITS}])")
# 「卷二」「卷 3」（卷名常写成「卷二：xxx」）
_PREFIX_SINGLE_RE = re.compile(rf"(卷)\s*({_NUMBER})(?![{_UNITS}])")
# 一个任务的范围过大时（模型把整本书写成一个任务）照样按编号核对，只是不再展开超过这个数。
_MAX_RANGE_SPAN = 2000
_UNIT_VALUES = frozenset(_UNITS)
_ARTIFACT_FILE_TYPES = frozenset(
    {"outline", "draft", "script", "character", "lore", "snippet", "document"}
)
_METADATA_KEY_BY_UNIT = {
    "章": "chapter_number",
    "集": "episode_number",
    "节": "section_number",
}
_LEADING_RANGE_RE = re.compile(
    rf"^\s*第\s*({_NUMBER})\s*([{_UNITS}])?\s*{_RANGE_SEP}\s*第?\s*({_NUMBER})\s*([{_UNITS}])"
)
_LEADING_SINGLE_RE = re.compile(rf"^\s*第\s*({_NUMBER})\s*([{_UNITS}])")
_LEADING_PREFIX_RANGE_RE = re.compile(
    rf"^\s*(卷)\s*({_NUMBER})\s*{_RANGE_SEP}\s*卷?\s*({_NUMBER})"
)
_LEADING_PREFIX_SINGLE_RE = re.compile(rf"^\s*(卷)\s*({_NUMBER})(?![{_UNITS}])")
_LEADING_ENGLISH_SINGLE_RE = re.compile(
    r"^\s*(chapter|episode|ep\.?)\s+(\d+)\b(?!\s*[-–—~～]\s*\d)",
    re.IGNORECASE,
)


def _to_int(token: str) -> int | None:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
        return value if value > 0 else None
    return parse_chinese_number(token.replace("两", "二").replace("〇", "零"))


def _expand(start: int | None, end: int | None) -> set[int]:
    if start is None or end is None or end < start or end - start > _MAX_RANGE_SPAN:
        return set()
    return set(range(start, end + 1))


def _ranges(text: str) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for match in _RANGE_RE.finditer(text):
        first_unit = match.group(2)
        unit = match.group(4)
        if first_unit and first_unit != unit:
            continue
        numbers = _expand(_to_int(match.group(1)), _to_int(match.group(3)))
        if numbers:
            found.setdefault(unit, set()).update(numbers)
    for match in _PREFIX_RANGE_RE.finditer(text):
        numbers = _expand(_to_int(match.group(2)), _to_int(match.group(3)))
        if numbers:
            found.setdefault("卷", set()).update(numbers)
    return found


def _singles(text: str) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for match in _SINGLE_RE.finditer(text):
        number = _to_int(match.group(1))
        if number:
            found.setdefault(match.group(2), set()).add(number)
    for match in _PREFIX_SINGLE_RE.finditer(text):
        number = _to_int(match.group(2))
        if number:
            found.setdefault("卷", set()).add(number)
    return found


@dataclass(frozen=True)
class UnitSpec:
    """任务要交付的编号：单位（章 / 集 / 卷…）+ 编号集合。"""

    unit: str
    numbers: frozenset[int]


@dataclass(frozen=True)
class ArtifactEvidence:
    """Live typed file evidence used to verify task delivery."""

    id: str
    title: str
    file_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _valid_unit_spec(value: Any) -> UnitSpec | None:
    if not isinstance(value, Mapping) or set(value) != {"unit", "numbers"}:
        return None
    unit = value.get("unit")
    numbers = value.get("numbers")
    if not isinstance(unit, str) or unit not in _UNIT_VALUES:
        return None
    if not isinstance(numbers, list) or not numbers:
        return None
    if any(isinstance(number, bool) or not isinstance(number, int) or number <= 0 for number in numbers):
        return None
    if len(set(numbers)) != len(numbers):
        return None
    if max(numbers) - min(numbers) > _MAX_RANGE_SPAN:
        return None
    return UnitSpec(unit=unit, numbers=frozenset(numbers))


def validate_task_artifact_fields(task: Mapping[str, Any]) -> None:
    """Validate optional structured artifact evidence fields on a task."""
    if "artifact_file_type" in task:
        file_type = task.get("artifact_file_type")
        if not isinstance(file_type, str) or file_type not in _ARTIFACT_FILE_TYPES:
            raise ValueError("artifact_file_type must be a supported file type")

    if "artifact_refs" in task:
        refs = task.get("artifact_refs")
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or not ref.strip() or ref != ref.strip()
            for ref in refs
        ):
            raise ValueError("artifact_refs must be an array of non-empty file IDs")

    if "unit_spec" in task and _valid_unit_spec(task.get("unit_spec")) is None:
        raise ValueError("unit_spec must contain a valid unit and unique positive integers")


def task_artifact_type(task: Mapping[str, Any]) -> str | None:
    """Return the validated expected artifact type, failing closed if malformed."""
    file_type = task.get("artifact_file_type")
    return file_type if isinstance(file_type, str) and file_type in _ARTIFACT_FILE_TYPES else None


def task_evidence_spec(task: Mapping[str, Any]) -> UnitSpec | None:
    """Return structured units, or typed legacy range text for old task data."""
    if "unit_spec" in task:
        return _valid_unit_spec(task.get("unit_spec"))
    if task_artifact_type(task) is None:
        return None
    return task_unit_spec(task_text(dict(task)))


def _positive_metadata_unit(artifact: ArtifactEvidence, unit: str) -> int | None:
    key = _METADATA_KEY_BY_UNIT.get(unit)
    if key is None:
        return None
    if artifact.file_type == "draft" and key != "chapter_number":
        return None
    if artifact.file_type == "script" and key not in {"episode_number", "section_number"}:
        return None
    value = artifact.metadata.get(key) if isinstance(artifact.metadata, Mapping) else None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _leading_title_units(title: str, unit: str, *, allow_range: bool) -> set[int]:
    text = title or ""
    match = _LEADING_RANGE_RE.match(text)
    if match:
        if allow_range:
            first_unit = match.group(2)
            final_unit = match.group(4)
            if final_unit == unit and (first_unit is None or first_unit == unit):
                return _expand(_to_int(match.group(1)), _to_int(match.group(3)))
        return set()
    match = _LEADING_PREFIX_RANGE_RE.match(text)
    if match:
        if allow_range and unit == "卷":
            return _expand(_to_int(match.group(2)), _to_int(match.group(3)))
        return set()

    match = _LEADING_SINGLE_RE.match(text)
    if match and match.group(2) == unit:
        number = _to_int(match.group(1))
        return {number} if number is not None else set()
    match = _LEADING_PREFIX_SINGLE_RE.match(text)
    if match and unit == "卷":
        number = _to_int(match.group(2))
        return {number} if number is not None else set()
    match = _LEADING_ENGLISH_SINGLE_RE.match(text)
    if match:
        heading = match.group(1).lower()
        compatible_unit = "章" if heading == "chapter" else "集"
        if compatible_unit == unit:
            return {int(match.group(2))}
    return set()


def artifact_units(
    artifacts: list[ArtifactEvidence], unit: str, file_type: str,
) -> set[int]:
    """Collect units proven by exact-type live artifact evidence."""
    if unit not in _UNIT_VALUES or file_type not in {"outline", "draft", "script"}:
        return set()
    numbers: set[int] = set()
    for artifact in artifacts:
        if artifact.file_type != file_type:
            continue
        metadata_number = _positive_metadata_unit(artifact, unit)
        if metadata_number is not None:
            numbers.add(metadata_number)
            continue
        numbers.update(
            _leading_title_units(
                artifact.title,
                unit,
                allow_range=file_type == "outline",
            )
        )
    return numbers


def task_unit_spec(text: str) -> UnitSpec | None:
    """任务文本要交付的编号范围；取不到时返回 None。

    有范围（「第2-5集」）时只看范围，不看同一句里的单个编号（「第一卷正文（第1-119章）」
    要的是第 1–119 章，不是第一卷这个文件）；几种单位都有时取编号最多的那种。
    """
    text = text or ""
    found = _ranges(text) or _singles(text)
    if not found:
        return None
    unit, numbers = max(found.items(), key=lambda item: len(item[1]))
    return UnitSpec(unit=unit, numbers=frozenset(numbers))


def title_units(titles: list[str], unit: str) -> set[int]:
    """这些文件标题里出现的某一单位的编号（含标题里的范围，如「第1-10章大纲」）。"""
    numbers: set[int] = set()
    for title in titles:
        text = title or ""
        numbers.update(_ranges(text).get(unit, set()))
        numbers.update(_singles(text).get(unit, set()))
    return numbers


def format_units(numbers: set[int] | frozenset[int] | list[int], unit: str) -> str:
    """把编号写成「第2集」「第3–5集」「第2、4–6集」。"""
    ordered = sorted(set(numbers))
    if not ordered:
        return ""
    spans: list[str] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append(f"{start}–{previous}" if previous > start else str(start))
        start = previous = number
    spans.append(f"{start}–{previous}" if previous > start else str(start))
    return f"第{'、'.join(spans)}{unit}"


def task_text(task: dict) -> str:
    """任务里用来判断交付范围的文字：任务描述 + 产出物。"""
    parts = [str(task.get("task") or ""), str(task.get("artifact") or "")]
    return " ".join(part for part in parts if part.strip())


__all__ = [
    "ArtifactEvidence",
    "UnitSpec",
    "artifact_units",
    "format_units",
    "task_artifact_type",
    "task_evidence_spec",
    "task_text",
    "task_unit_spec",
    "title_units",
    "validate_task_artifact_fields",
]
