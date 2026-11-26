"""标准技能包（SKILL.md + references/ + assets/）的解析、zip 安全与导出往返。"""

import io
import json
import stat
import zipfile

import pytest

from agent.skills.package import (
    MAX_RESOURCE_BYTES,
    MAX_RESOURCES_PER_SKILL,
    MAX_TRIGGER_CHARS,
    MAX_TRIGGERS,
    MAX_ZIP_BYTES,
    MAX_ZIP_ENTRIES,
    ParsedSkill,
    SkillPackageError,
    build_skill_md,
    build_skill_zip,
    parse_skill_md,
    read_skill_md_upload,
    read_skill_zip,
    serialize_skill_metadata,
    slugify_skill_name,
    validate_resource_path,
)

STANDARD_SKILL_MD = """---
name: suspense-master
description: 增强钩子和悬念。在用户想让章节结尾更抓人时使用。
license: MIT
compatibility: 需要能读取项目文件
allowed-tools: query_files edit_file
metadata:
  author: someone
  zenstory:
    display_name: 悬念大师
    triggers:
      - 悬念
      - 钩子
    category: plot
---
# 悬念大师

先强化钩子，再收紧悬念。
"""


def _zip(entries: dict[str, bytes | str], *, symlinks: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name in symlinks:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, content)
    return buffer.getvalue()


# ==================== parse_skill_md ====================


@pytest.mark.unit
class TestParseSkillMd:
    def test_standard_frontmatter(self):
        skill = parse_skill_md(STANDARD_SKILL_MD)

        # metadata.zenstory.display_name 优先于 frontmatter name
        assert skill.name == "悬念大师"
        assert skill.description == "增强钩子和悬念。在用户想让章节结尾更抓人时使用。"
        assert skill.instructions == "# 悬念大师\n\n先强化钩子，再收紧悬念。"
        assert skill.triggers == ["悬念", "钩子"]
        assert skill.category == "plot"
        assert skill.license == "MIT"
        assert skill.compatibility == "需要能读取项目文件"
        assert skill.allowed_tools == ["query_files", "edit_file"]
        # display_name / triggers 已落到独立字段，不再重复存进 metadata
        assert skill.metadata == {"author": "someone", "zenstory": {"category": "plot"}}

    def test_standard_frontmatter_without_zenstory_metadata(self):
        skill = parse_skill_md(
            "---\nname: pdf-helper\ndescription: Work with PDFs.\n"
            "allowed-tools:\n  - Read\n  - Bash\n---\nDo things.\n"
        )

        assert skill.name == "pdf-helper"
        assert skill.triggers == []
        assert skill.allowed_tools == ["Read", "Bash"]
        assert skill.metadata == {}
        # allowed-tools 只存储，不授予任何工具
        assert skill.to_skill_metadata() == {"allowed_tools": ["Read", "Bash"]}

    def test_name_slug_rules_are_not_enforced(self):
        """导入宽松：名称允许中文、空格、大写。"""
        skill = parse_skill_md("---\nname: 我的 Skill\ndescription: d\n---\nbody")
        assert skill.name == "我的 Skill"

    def test_legacy_top_level_triggers_are_accepted(self):
        skill = parse_skill_md("---\nname: 旧技能\ndescription: d\ntriggers:\n  - 甲\n---\nbody")
        assert skill.triggers == ["甲"]

    def test_crlf_and_bom_are_tolerated(self):
        skill = parse_skill_md("\ufeff---\r\nname: a\r\ndescription: b\r\n---\r\nbody\r\n")
        assert (skill.name, skill.description, skill.instructions) == ("a", "b", "body")

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("---\ndescription: d\n---\nbody", "name"),
            ("---\nname: a\n---\nbody", "description"),
            ("---\nname: a\ndescription: d\n---\n   ", "正文"),
            ("---\n- a\n- b\n---\nbody", "键值映射"),
            ("---\nname: a\ndescription: d\nmetadata: nope\n---\nbody", "metadata"),
            ("---\nname: [a\n---\nbody", "YAML"),
            ("just some text", "无法识别"),
        ],
    )
    def test_invalid_skill_md(self, text: str, message: str):
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(text)
        assert message in str(exc_info.value)
        assert exc_info.value.kind == "invalid"

    def test_length_limits(self):
        with pytest.raises(SkillPackageError):
            parse_skill_md(f"---\nname: {'名' * 101}\ndescription: d\n---\nbody")
        with pytest.raises(SkillPackageError):
            parse_skill_md(f"---\nname: a\ndescription: {'述' * 1025}\n---\nbody")
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(f"---\nname: a\ndescription: d\n---\n{'文' * 50_001}")
        assert exc_info.value.kind == "too_large"
        # 描述 1024 字符正好在上限内
        assert parse_skill_md(f"---\nname: a\ndescription: {'述' * 1024}\n---\nbody").description

    def test_legacy_native_format(self):
        skill = parse_skill_md(
            "# 结构技能\n\n一句话描述。\n\n## Triggers\n- 触发词甲\n- 触发词乙\n\n"
            "## Instructions\n真正的指令。\n\n## 注意事项\n不要跑题。"
        )

        assert skill.name == "结构技能"
        assert skill.description == "一句话描述。"
        assert skill.triggers == ["触发词甲", "触发词乙"]
        assert skill.instructions == "真正的指令。\n\n## 注意事项\n不要跑题。"

    def test_legacy_fenced_code_block_headings_are_preserved(self):
        skill = parse_skill_md(
            "# 模板技能\n\n描述。\n\n## Instructions\n按以下格式输出：\n\n"
            "```\n# [标题]\n\n## Triggers\n## 小节甲\n```\n\n结束。"
        )

        assert "## Triggers" in skill.instructions
        assert "## 小节甲" in skill.instructions
        assert "# [标题]" in skill.instructions
        assert skill.triggers == []

    def test_standard_body_keeps_fenced_code_blocks_verbatim(self):
        body = "输出格式：\n\n```\n---\nname: not-frontmatter\n---\n## 小节\n```\n\n完。"
        skill = parse_skill_md(f"---\nname: a\ndescription: d\n---\n{body}\n")
        assert skill.instructions == body


# ==================== resource path ====================


@pytest.mark.unit
class TestValidateResourcePath:
    @pytest.mark.parametrize(
        "path",
        [
            "references/style.md",
            "assets/template.json",
            "examples/opening.md",
            "templates/chapter.txt",
            "references/sub/dir/data.CSV",
            "templates/nested/outline.yaml",
            " assets/a.yml ",
        ],
    )
    def test_valid_paths(self, path: str):
        assert validate_resource_path(path) == path.strip()

    def test_disallowed_root_error_lists_all_allowed_roots(self):
        with pytest.raises(SkillPackageError) as exc_info:
            validate_resource_path("notes/memo.md")
        message = str(exc_info.value)
        for root in ("references/", "assets/", "examples/", "templates/"):
            assert root in message

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "SKILL.md",
            "scripts/run.md",
            "references/../x.md",
            "/references/a.md",
            "references\\a.md",
            "C:references/a.md",
            "references/a.py",
            "references/a.png",
            "references/",
            "references//a.md",
            "references/" + "a" * 260 + ".md",
        ],
    )
    def test_invalid_paths(self, path: str):
        with pytest.raises(SkillPackageError):
            validate_resource_path(path)


# ==================== read_skill_zip ====================


@pytest.mark.unit
class TestReadSkillZip:
    def test_top_level_directory_package(self):
        data = _zip({
            "my-skill/SKILL.md": STANDARD_SKILL_MD,
            "my-skill/references/hooks.md": "钩子清单",
            "my-skill/assets/template.json": '{"a": 1}',
            "my-skill/examples/sample.md": "范例",
            "my-skill/templates/beat-sheet.yaml": "beats: []",
        })

        skill, resources, warnings = read_skill_zip(data)

        assert skill.name == "悬念大师"
        assert resources == [
            ("assets/template.json", '{"a": 1}'),
            ("examples/sample.md", "范例"),
            ("references/hooks.md", "钩子清单"),
            ("templates/beat-sheet.yaml", "beats: []"),
        ]
        assert warnings == []

    def test_files_at_root(self):
        skill, resources, warnings = read_skill_zip(_zip({
            "SKILL.md": STANDARD_SKILL_MD,
            "references/a.txt": "a",
        }))
        assert skill.name == "悬念大师"
        assert resources == [("references/a.txt", "a")]
        assert warnings == []

    def test_scripts_non_text_and_stray_files_are_dropped_with_warnings(self):
        data = _zip({
            "s/SKILL.md": STANDARD_SKILL_MD,
            "s/scripts/run.py": "import os; os.system('rm -rf /')",
            "s/scripts/notes.md": "script docs are still scripts",
            "s/references/image.png": b"\x89PNG\r\n",
            "s/README.md": "readme",
            "other/file.md": "outside",
            "__MACOSX/s/._SKILL.md": b"\x00\x01",
            "s/.DS_Store": b"\x00",
            "s/references/ok.md": "ok",
        })

        skill, resources, warnings = read_skill_zip(data)

        assert skill.name == "悬念大师"
        assert resources == [("references/ok.md", "ok")]
        joined = "\n".join(warnings)
        assert "scripts/run.py" in joined and "不执行" in joined
        assert "scripts/notes.md" in joined
        assert "references/image.png" in joined
        assert "README.md" in joined
        assert "examples/" in joined and "templates/" in joined
        assert "other/file.md" in joined
        # 系统垃圾文件静默跳过
        assert "__MACOSX" not in joined and ".DS_Store" not in joined
        assert len(warnings) == 5

    @pytest.mark.parametrize(
        "bad_name",
        ["../evil.md", "s/../../evil.md", "/abs/SKILL.md", "s\\references\\a.md", "C:/x/a.md"],
    )
    def test_path_traversal_is_rejected(self, bad_name: str):
        data = _zip({"s/SKILL.md": STANDARD_SKILL_MD, bad_name: "x"})
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "不安全" in str(exc_info.value)

    def test_symlink_is_rejected(self):
        data = _zip(
            {"s/SKILL.md": STANDARD_SKILL_MD, "s/references/link.md": "/etc/passwd"},
            symlinks=("s/references/link.md",),
        )
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "符号链接" in str(exc_info.value)

    def test_missing_skill_md(self):
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(_zip({"s/references/a.md": "a"}))
        assert "没有找到 SKILL.md" in str(exc_info.value)

    def test_multiple_skill_md(self):
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(_zip({"a/SKILL.md": STANDARD_SKILL_MD, "b/SKILL.md": STANDARD_SKILL_MD}))
        assert "多个 SKILL.md" in str(exc_info.value)

    def test_not_a_zip(self):
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(b"definitely not a zip")
        assert exc_info.value.kind == "invalid"

    def test_upload_over_size_limit(self):
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(b"PK\x03\x04" + b"\x00" * MAX_ZIP_BYTES)
        assert exc_info.value.kind == "too_large"

    def test_too_many_entries(self):
        entries = {"s/SKILL.md": STANDARD_SKILL_MD}
        entries.update({f"s/junk/{index}.bin": b"" for index in range(MAX_ZIP_ENTRIES)})
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(_zip(entries))
        assert exc_info.value.kind == "too_large"

    def test_too_many_resources(self):
        entries = {"s/SKILL.md": STANDARD_SKILL_MD}
        entries.update({f"s/references/{index}.md": "x" for index in range(MAX_RESOURCES_PER_SKILL + 1)})
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(_zip(entries))
        assert exc_info.value.kind == "too_large"

    def test_oversized_resource(self):
        data = _zip({"s/SKILL.md": STANDARD_SKILL_MD, "s/references/big.md": "a" * (MAX_RESOURCE_BYTES + 1)})
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert exc_info.value.kind == "too_large"

    def test_total_resource_size_limit(self):
        entries = {"s/SKILL.md": STANDARD_SKILL_MD}
        # 5 × 60 KiB = 300 KiB > 256 KiB，单个文件都在上限内
        entries.update({f"s/references/{index}.md": "a" * (60 * 1024) for index in range(5)})
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(_zip(entries))
        assert exc_info.value.kind == "too_large"

    def test_zip_bomb_declared_size(self):
        """高压缩比条目（声明解压后远超上限）在读取前就被拒绝。"""
        data = _zip({"s/SKILL.md": STANDARD_SKILL_MD, "s/junk.bin": b"\x00" * (5 * 1024 * 1024)})
        assert len(data) < MAX_ZIP_BYTES
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert exc_info.value.kind == "too_large"

    def test_zip_bomb_lying_about_declared_size(self):
        """声明大小造假（谎报得很小）时，读取仍被截断并拒收，不会把整个炸弹解压进内存。"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("s/SKILL.md", STANDARD_SKILL_MD)
            zf.writestr("s/references/bomb.md", "a" * (MAX_RESOURCE_BYTES * 4))
        data = bytearray(buffer.getvalue())
        _patch_declared_size(data, b"s/references/bomb.md", 10)

        with pytest.raises(SkillPackageError):
            read_skill_zip(bytes(data))

    def test_non_utf8_resource_is_rejected(self):
        data = _zip({"s/SKILL.md": STANDARD_SKILL_MD, "s/references/gbk.txt": "中文".encode("gbk")})
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "UTF-8" in str(exc_info.value)

    def test_invalid_skill_md_inside_zip(self):
        with pytest.raises(SkillPackageError):
            read_skill_zip(_zip({"s/SKILL.md": "no frontmatter here"}))


def _patch_declared_size(data: bytearray, name: bytes, fake_size: int) -> None:
    """把 zip 中某条目的「声明解压大小」改小（本地头与中央目录两处都改）。"""
    local_sig = b"PK\x03\x04"
    central_sig = b"PK\x01\x02"
    index = 0
    patched = 0
    while True:
        index = data.find(name, index)
        if index < 0:
            break
        local_start = data.rfind(local_sig, 0, index)
        central_start = data.rfind(central_sig, 0, index)
        if central_start > local_start and index - central_start == 46:
            data[central_start + 24:central_start + 28] = fake_size.to_bytes(4, "little")
            patched += 1
        elif index - local_start == 30:
            data[local_start + 22:local_start + 26] = fake_size.to_bytes(4, "little")
            patched += 1
        index += len(name)
    assert patched == 2


# ==================== .md upload ====================


@pytest.mark.unit
def test_read_skill_md_upload():
    skill = read_skill_md_upload(STANDARD_SKILL_MD.encode("utf-8"))
    assert skill.name == "悬念大师"

    with pytest.raises(SkillPackageError):
        read_skill_md_upload("# 标题\n".encode("gbk") + b"\xff\xfe")


# ==================== export ====================


@pytest.mark.unit
class TestBuildSkillZip:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Suspense Master!", "suspense-master"),
            ("  --PDF__Helper--  ", "pdf-helper"),
            ("悬念大师", "skill-abcdef12"),
            ("a" * 80, "a" * 64),
        ],
    )
    def test_slug(self, name: str, expected: str):
        assert slugify_skill_name(name, "abcdef12-3456-7890") == expected

    def test_export_import_round_trip(self):
        original = ParsedSkill(
            name="悬念大师",
            description="增强钩子和悬念",
            instructions="# 方法\n\n```\n## 模板\n```\n\n先强化钩子。",
            triggers=["悬念", "钩子"],
            category="plot",
            license="MIT",
            compatibility="zenstory",
            allowed_tools=["query_files", "edit_file"],
            metadata={"author": "someone", "zenstory": {"extra": 1}},
        )
        resources = [("references/b.md", "B"), ("assets/a.json", "{}")]

        data = build_skill_zip(original, resources, skill_id="abcdef12-0000")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            skill_md = zf.read("skill-abcdef12/SKILL.md").decode("utf-8")
        assert names == [
            "skill-abcdef12/SKILL.md",
            "skill-abcdef12/assets/a.json",
            "skill-abcdef12/references/b.md",
        ]
        assert skill_md.startswith("---\nname: skill-abcdef12\n")
        assert "allowed-tools: query_files edit_file" in skill_md

        skill, round_trip_resources, warnings = read_skill_zip(data)
        assert warnings == []
        assert round_trip_resources == sorted(resources)
        assert skill.name == original.name
        assert skill.description == original.description
        assert skill.instructions == original.instructions
        assert skill.triggers == original.triggers
        assert skill.category == original.category
        assert skill.license == original.license
        assert skill.compatibility == original.compatibility
        assert skill.allowed_tools == original.allowed_tools
        assert skill.metadata == {"author": "someone", "zenstory": {"extra": 1, "category": "plot"}}

    def test_export_is_deterministic(self):
        skill = ParsedSkill(name="Plot Helper", description="d", instructions="i")
        assert build_skill_zip(skill, [], skill_id="x") == build_skill_zip(skill, [], skill_id="x")

    def test_skill_md_uses_display_name_metadata(self):
        text = build_skill_md(
            ParsedSkill(name="Plot Helper", description="", instructions="i", triggers=["plot"]),
            skill_id="x",
        )
        parsed = parse_skill_md(text)
        # 空描述导出时以名称兜底，保证标准要求的 description 非空
        assert parsed.description == "Plot Helper"
        assert parsed.name == "Plot Helper"
        assert parsed.triggers == ["plot"]


# ==================== 安全加固回归（代码评审发现） ====================


def _frontmatter(body: str) -> str:
    return f"---\nname: x\ndescription: d\n{body}---\n正文\n"


def _zip_entries(entries: list[tuple[str, str]]) -> bytes:
    """允许重复条目名的 zip（dict 版 _zip 做不到）。"""
    import warnings

    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w") as zf:
            for name, content in entries:
                zf.writestr(name, content)
    return buffer.getvalue()


class TestFrontmatterHardening:
    def test_yaml_alias_expansion_is_rejected(self):
        """billion laughs：几百字节的别名链不能被展开存进 skill_metadata。"""
        text = _frontmatter(
            "metadata:\n"
            "  a: &a [x, x, x, x, x, x, x, x, x, x]\n"
            "  b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
            "  c: [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        )
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(text)
        assert "锚点或别名" in str(exc_info.value)

    def test_yaml_anchor_without_alias_is_rejected(self):
        with pytest.raises(SkillPackageError):
            parse_skill_md(_frontmatter("metadata: &m\n  a: 1\n"))

    def test_frontmatter_size_is_capped(self):
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(_frontmatter(f"metadata:\n  big: {'x' * (16 * 1024)}\n"))
        assert exc_info.value.kind == "too_large"

    def test_serialized_metadata_size_is_capped(self):
        # 单个 frontmatter 在 16 KiB 内，但 json 转义后超过 skill_metadata 上限
        text = _frontmatter("metadata:\n" + "".join(f"  k{i}: \"\\x01\\x01\\x01\"\n" for i in range(700)))
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(text)
        assert exc_info.value.kind == "too_large"
        assert "元数据" in str(exc_info.value)

    def test_metadata_depth_is_limited(self):
        nested = "metadata:\n  a: " + "[" * 20 + "]" * 20 + "\n"
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(_frontmatter(nested))
        assert "嵌套" in str(exc_info.value)

    def test_metadata_node_count_is_limited(self):
        items = ", ".join("1" for _ in range(1100))
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(_frontmatter(f"metadata:\n  a: [{items}]\n"))
        assert "过多" in str(exc_info.value)

    def test_metadata_dates_become_iso_strings(self):
        skill = parse_skill_md(_frontmatter(
            "metadata:\n  released: 2024-01-02\n  at: 2024-01-02 03:04:05\n  2024-05-06: key\n"
        ))
        assert skill.metadata == {
            "released": "2024-01-02",
            "at": "2024-01-02T03:04:05",
            "2024-05-06": "key",
        }
        assert json.loads(serialize_skill_metadata(skill))["metadata"]["released"] == "2024-01-02"

    @pytest.mark.parametrize(
        "yaml_value",
        ["!!set {a: null, b: null}", "!!binary aGVsbG8=", ".nan"],
        ids=["set", "binary", "nan"],
    )
    def test_non_json_metadata_types_are_rejected(self, yaml_value: str):
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(_frontmatter(f"metadata:\n  bad: {yaml_value}\n"))
        assert exc_info.value.kind == "invalid"

    @pytest.mark.parametrize(
        "field",
        ["triggers: [ok, [nested]]", "triggers: [{a: 1}]", "allowed-tools: [edit_file, {x: 1}]"],
        ids=["nested-list", "mapping", "allowed-tools-mapping"],
    )
    def test_list_items_must_be_scalars(self, field: str):
        with pytest.raises(SkillPackageError) as exc_info:
            parse_skill_md(_frontmatter(f"{field}\n"))
        assert "只能包含字符串" in str(exc_info.value)

    def test_trigger_count_and_length_limits(self):
        many = ", ".join(f"t{i}" for i in range(MAX_TRIGGERS + 1))
        with pytest.raises(SkillPackageError):
            parse_skill_md(_frontmatter(f"triggers: [{many}]\n"))
        with pytest.raises(SkillPackageError):
            parse_skill_md(_frontmatter(f"triggers: [{'长' * (MAX_TRIGGER_CHARS + 1)}]\n"))

    def test_indented_dashes_do_not_end_frontmatter(self):
        text = "---\nname: x\ndescription: |\n  第一段\n  ---\n  第二段\n---\n正文\n"
        skill = parse_skill_md(text)
        assert skill.description == "第一段\n---\n第二段"
        assert skill.instructions == "正文"

    def test_round_trip_description_with_markdown_rule(self):
        original = ParsedSkill(
            name="分段技能",
            description="第一段说明\n---\n第二段说明",
            instructions="方法正文",
        )
        skill, _resources, _warnings = read_skill_zip(build_skill_zip(original, [], skill_id="abc"))
        assert skill.description == original.description
        assert skill.instructions == original.instructions


class TestZipHardening:
    def test_duplicate_entries_are_rejected(self):
        data = _zip_entries([
            ("s/SKILL.md", STANDARD_SKILL_MD),
            ("s/references/a.md", "one"),
            ("s/references/a.md", "two"),
        ])
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "重复" in str(exc_info.value)

    def test_nfc_lookalike_entries_are_rejected(self):
        nfc = "references/café.md"
        nfd = "references/café.md"
        data = _zip_entries([("SKILL.md", STANDARD_SKILL_MD), (nfc, "a"), (nfd, "b")])
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "重复" in str(exc_info.value)

    def test_resource_paths_are_nfc_normalized(self):
        data = _zip_entries([("SKILL.md", STANDARD_SKILL_MD), ("references/café.md", "b")])
        _skill, resources, _warnings = read_skill_zip(data)
        assert resources == [("references/café.md", "b")]

    def test_root_skill_md_wins_over_references_skill_md(self):
        data = _zip_entries([
            ("SKILL.md", STANDARD_SKILL_MD),
            ("references/SKILL.md", "参考用的另一份说明"),
            ("examples/SKILL.md", "范例里的说明"),
            ("templates/SKILL.md", "模板里的说明"),
        ])
        skill, resources, warnings = read_skill_zip(data)
        assert skill.name == "悬念大师"
        assert resources == [
            ("examples/SKILL.md", "范例里的说明"),
            ("references/SKILL.md", "参考用的另一份说明"),
            ("templates/SKILL.md", "模板里的说明"),
        ]
        assert warnings == []

    def test_root_skill_md_plus_sibling_skill_dir_is_ambiguous(self):
        data = _zip_entries([("SKILL.md", STANDARD_SKILL_MD), ("other/SKILL.md", STANDARD_SKILL_MD)])
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "多个 SKILL.md" in str(exc_info.value)


class TestPathCharacters:
    @pytest.mark.parametrize(
        "path",
        [
            "references/a\x7f.md",  # DEL
            "references/a\x85.md",  # C1 NEL
            "references/a‮gnp.md",  # RTL override
            "references/a​.md",  # zero-width space
            "references/a﻿.md",  # BOM / ZWNBSP
            "references/a .md",  # line separator
        ],
        ids=["del", "c1", "bidi", "zwsp", "bom", "line-sep"],
    )
    def test_control_and_format_characters_are_rejected(self, path: str):
        with pytest.raises(SkillPackageError):
            validate_resource_path(path)

    def test_zip_entry_with_format_character_is_rejected(self):
        data = _zip_entries([("SKILL.md", STANDARD_SKILL_MD), ("references/a‮.md", "x")])
        with pytest.raises(SkillPackageError) as exc_info:
            read_skill_zip(data)
        assert "不安全的路径" in str(exc_info.value)

    def test_path_is_nfc_normalized(self):
        assert validate_resource_path("references/café.md") == "references/café.md"
