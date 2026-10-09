"""
Tests for SkillMdService.

Unit tests for the skill markdown documentation service, covering:
- Service initialization with environment variables
- Chinese documentation generation
- English documentation generation
- Language selection in generate_skill_md
- Content structure validation
"""

import os
from unittest.mock import patch

import pytest
import yaml

from services.skill_md_service import SkillMdService, skill_md_service


@pytest.mark.unit
class TestSkillMdServiceInit:
    """Tests for SkillMdService initialization."""

    def test_default_initialization(self):
        """Test initialization with default values when no env vars set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the relevant env vars if they exist
            os.environ.pop("APP_NAME", None)
            os.environ.pop("APP_VERSION", None)
            os.environ.pop("API_BASE_URL", None)

            service = SkillMdService()
            assert service.app_name == "zenstory API"
            assert service.app_version == "1.0.0"
            assert service.api_base == "https://api.zenstory.ai/api/v1"

    def test_custom_app_name(self):
        """Test initialization with custom APP_NAME."""
        with patch.dict(os.environ, {"APP_NAME": "Custom API"}):
            service = SkillMdService()
            assert service.app_name == "Custom API"

    def test_custom_app_version(self):
        """Test initialization with custom APP_VERSION."""
        with patch.dict(os.environ, {"APP_VERSION": "2.5.0"}):
            service = SkillMdService()
            assert service.app_version == "2.5.0"

    def test_custom_api_base(self):
        """Test initialization with custom API_BASE_URL."""
        with patch.dict(os.environ, {"API_BASE_URL": "https://custom.api.com/v1"}):
            service = SkillMdService()
            assert service.api_base == "https://custom.api.com/v1"

    def test_all_custom_env_vars(self):
        """Test initialization with all custom environment variables."""
        custom_env = {
            "APP_NAME": "My Custom API",
            "APP_VERSION": "3.0.0-beta",
            "API_BASE_URL": "https://myapi.example.com/api/v2",
        }
        with patch.dict(os.environ, custom_env):
            service = SkillMdService()
            assert service.app_name == "My Custom API"
            assert service.app_version == "3.0.0-beta"
            assert service.api_base == "https://myapi.example.com/api/v2"


@pytest.mark.unit
class TestGenerateSkillMd:
    """Tests for generate_skill_md method."""

    def test_default_language_is_chinese(self):
        """Test that default language is Chinese."""
        service = SkillMdService()
        content = service.generate_skill_md()
        # Check for Chinese characters in the content
        assert "小说写作" in content or "欢迎使用" in content

    def test_explicit_chinese_language(self):
        """Test explicit Chinese language selection."""
        service = SkillMdService()
        content = service.generate_skill_md(lang="zh")
        assert "小说写作" in content
        assert "AI 辅助" in content

    def test_english_language(self):
        """Test English language selection."""
        service = SkillMdService()
        content = service.generate_skill_md(lang="en")
        assert "Novel Writing" in content
        assert "AI-assisted" in content

    def test_unknown_language_defaults_to_chinese(self):
        """Test that unknown language defaults to Chinese."""
        service = SkillMdService()
        content = service.generate_skill_md(lang="fr")  # French not supported
        assert "小说写作" in content or "欢迎使用" in content

    def test_empty_language_defaults_to_chinese(self):
        """Test that empty language string defaults to Chinese."""
        service = SkillMdService()
        content = service.generate_skill_md(lang="")
        assert "小说写作" in content or "欢迎使用" in content


@pytest.mark.unit
class TestGenerateChinese:
    """Tests for Chinese documentation generation."""

    @pytest.fixture
    def service(self):
        """Create a SkillMdService instance."""
        return SkillMdService()

    def test_yml_frontmatter_present(self, service):
        """Test that YAML frontmatter is present."""
        content = service._generate_chinese()
        assert content.startswith("---\n")
        assert "---\n\n" in content

    def test_frontmatter_contains_required_fields(self, service):
        """Test that frontmatter contains all required fields."""
        content = service._generate_chinese()

        # Extract frontmatter
        frontmatter_end = content.index("---\n\n") + 4
        frontmatter = content[:frontmatter_end]

        assert "name:" in frontmatter
        assert "version:" in frontmatter
        assert "description:" in frontmatter
        assert "api_base:" in frontmatter
        assert "auth_method:" in frontmatter
        assert "auth_header:" in frontmatter
        assert "auth_prefix:" in frontmatter
        assert "rate_limit:" in frontmatter
        assert "capabilities:" in frontmatter
        assert "file_types:" in frontmatter

    def test_frontmatter_contains_capabilities(self, service):
        """Test that frontmatter lists all capabilities."""
        content = service._generate_chinese()
        frontmatter = content.split("---\n\n")[0] + "---"

        assert "project_management" in frontmatter
        assert "file_crud" in frontmatter
        assert "hybrid_search" in frontmatter
        assert "writing_context" in frontmatter
        assert "ai_chat" not in frontmatter

    def test_frontmatter_contains_file_types(self, service):
        """Test that frontmatter lists all file types."""
        content = service._generate_chinese()
        frontmatter = content.split("---\n\n")[0] + "---"

        assert "outline" in frontmatter
        assert "draft" in frontmatter
        assert "character" in frontmatter
        assert "lore" in frontmatter
        assert "material" in frontmatter

    def test_contains_authentication_section(self, service):
        """Test that authentication section is present."""
        content = service._generate_chinese()
        assert "## 认证说明" in content
        assert "X-Agent-API-Key" in content

    def test_contains_api_endpoints(self, service):
        """Test that API endpoints section is present."""
        content = service._generate_chinese()
        assert "## API 端点列表" in content
        assert "/api/v1/agent/projects" in content
        assert "/api/v1/agent/files" in content
        assert "/api/v1/agent/projects/{project_id}/search" in content

    def test_contains_file_types_section(self, service):
        """Test that file types section is present."""
        content = service._generate_chinese()
        assert "## 文件类型说明" in content
        assert "| outline" in content
        assert "| draft" in content

    def test_contains_usage_examples(self, service):
        """Test that usage examples are present."""
        content = service._generate_chinese()
        assert "## 使用示例" in content
        assert "curl -X POST" in content

    def test_contains_error_handling_section(self, service):
        """Test that error handling section is present."""
        content = service._generate_chinese()
        assert "## 错误处理" in content
        assert "AUTH_UNAUTHORIZED" in content
        assert "NOT_FOUND" in content

    def test_contains_rate_limiting_section(self, service):
        """Test that rate limiting section is present."""
        content = service._generate_chinese()
        assert "## 速率限制" in content
        assert "2000/hour" in content

    def test_api_base_in_content(self, service):
        """Test that api_base URL is included in the content."""
        content = service._generate_chinese()
        assert service.api_base in content

    def test_version_in_frontmatter(self, service):
        """Test that version is included in frontmatter."""
        content = service._generate_chinese()
        assert f'version: "{service.app_version}"' in content


@pytest.mark.unit
class TestGenerateEnglish:
    """Tests for English documentation generation."""

    @pytest.fixture
    def service(self):
        """Create a SkillMdService instance."""
        return SkillMdService()

    def test_yml_frontmatter_present(self, service):
        """Test that YAML frontmatter is present."""
        content = service._generate_english()
        assert content.startswith("---\n")
        assert "---\n\n" in content

    def test_frontmatter_contains_required_fields(self, service):
        """Test that frontmatter contains all required fields."""
        content = service._generate_english()

        # Extract frontmatter
        frontmatter_end = content.index("---\n\n") + 4
        frontmatter = content[:frontmatter_end]

        assert "name:" in frontmatter
        assert "version:" in frontmatter
        assert "description:" in frontmatter
        assert "api_base:" in frontmatter
        assert "auth_method:" in frontmatter
        assert "auth_header:" in frontmatter
        assert "auth_prefix:" in frontmatter
        assert "rate_limit:" in frontmatter
        assert "capabilities:" in frontmatter
        assert "file_types:" in frontmatter

    def test_contains_authentication_section(self, service):
        """Test that authentication section is present."""
        content = service._generate_english()
        assert "## Authentication" in content
        assert "X-Agent-API-Key" in content

    def test_contains_api_endpoints(self, service):
        """Test that API endpoints section is present."""
        content = service._generate_english()
        assert "## API Endpoints" in content
        assert "/api/v1/agent/projects" in content
        assert "/api/v1/agent/files" in content
        assert "/api/v1/agent/projects/{project_id}/search" in content

    def test_contains_file_types_section(self, service):
        """Test that file types section is present."""
        content = service._generate_english()
        assert "## File Types" in content
        assert "| outline" in content
        assert "| draft" in content

    def test_contains_usage_examples(self, service):
        """Test that usage examples are present."""
        content = service._generate_english()
        assert "## Usage Examples" in content
        assert "curl -X POST" in content

    def test_contains_error_handling_section(self, service):
        """Test that error handling section is present."""
        content = service._generate_english()
        assert "## Error Handling" in content
        assert "AUTH_UNAUTHORIZED" in content
        assert "NOT_FOUND" in content

    def test_contains_rate_limiting_section(self, service):
        """Test that rate limiting section is present."""
        content = service._generate_english()
        assert "## Rate Limiting" in content
        assert "2000/hour" in content

    def test_contains_best_practices_section(self, service):
        """Test that scope section is present."""
        content = service._generate_english()
        assert "## Scope" in content

    def test_api_base_in_content(self, service):
        """Test that api_base URL is included in the content."""
        content = service._generate_english()
        assert service.api_base in content

    def test_version_in_frontmatter(self, service):
        """Test that version is included in frontmatter."""
        content = service._generate_english()
        assert f'version: "{service.app_version}"' in content


@pytest.mark.unit
class TestContentStructure:
    """Tests for content structure validation."""

    @pytest.fixture
    def service(self):
        """Create a SkillMdService instance."""
        return SkillMdService()

    def test_chinese_content_is_markdown(self, service):
        """Test that Chinese content is valid markdown."""
        content = service._generate_chinese()
        # Check for markdown headers
        assert content.count("#") > 0
        # Check for markdown tables
        assert "|" in content
        # Check for code blocks
        assert "```" in content

    def test_english_content_is_markdown(self, service):
        """Test that English content is valid markdown."""
        content = service._generate_english()
        # Check for markdown headers
        assert content.count("#") > 0
        # Check for markdown tables
        assert "|" in content
        # Check for code blocks
        assert "```" in content

    def test_both_versions_have_same_structure(self, service):
        """Test that both language versions have similar structure."""
        zh_content = service._generate_chinese()
        en_content = service._generate_english()

        # Both should have frontmatter
        assert zh_content.startswith("---\n")
        assert en_content.startswith("---\n")

        # Both should have similar number of sections (roughly)
        zh_headers = zh_content.count("\n## ")
        en_headers = en_content.count("\n## ")
        assert abs(zh_headers - en_headers) <= 1

    def test_content_has_main_title(self, service):
        """Test that content has a main title."""
        zh_content = service._generate_chinese()
        en_content = service._generate_english()

        assert "# ZenStory" in zh_content
        assert "# ZenStory" in en_content

    def test_content_has_curl_examples(self, service):
        """Test that content contains curl examples."""
        zh_content = service._generate_chinese()
        en_content = service._generate_english()

        # Both should have multiple curl examples
        assert zh_content.count("curl -X") >= 3
        assert en_content.count("curl -X") >= 3

    def test_content_has_json_examples(self, service):
        """Test that content contains JSON examples."""
        zh_content = service._generate_chinese()
        en_content = service._generate_english()

        # Both should have JSON in examples
        assert '"title":' in zh_content
        assert '"title":' in en_content


def _parse_frontmatter(content: str) -> dict:
    assert content.startswith("---\n")
    frontmatter = content.split("---\n", 2)[1]
    return yaml.safe_load(frontmatter)


@pytest.mark.unit
class TestAgentSkillsSpecCompliance:
    """/skill.md frontmatter must follow the Agent Skills spec (agentskills.io)."""

    ALLOWED_TOP_LEVEL = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}

    @pytest.fixture(params=["zh", "en"])
    def frontmatter(self, request):
        return _parse_frontmatter(SkillMdService().generate_skill_md(lang=request.param))

    def test_only_spec_fields_at_top_level(self, frontmatter):
        assert set(frontmatter) <= self.ALLOWED_TOP_LEVEL
        assert {"name", "description", "metadata"} <= set(frontmatter)

    def test_name_is_lowercase_slug(self, frontmatter):
        assert frontmatter["name"] == "zenstory"

    def test_description_within_limit(self, frontmatter):
        description = frontmatter["description"]
        assert isinstance(description, str)
        assert 0 < len(description) <= 1024
        assert "ZenStory" in description

    def test_metadata_is_string_map_with_legacy_fields(self, frontmatter):
        metadata = frontmatter["metadata"]
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items())
        for key in (
            "version", "api_base", "auth_method", "auth_header", "auth_prefix",
            "rate_limit", "triggers", "capabilities", "file_types",
        ):
            assert key in metadata
        assert metadata["auth_header"] == "X-Agent-API-Key"
        assert metadata["auth_prefix"] == "eg_"
        assert "read=2000/hour" in metadata["rate_limit"]
        assert "writing_context" in metadata["capabilities"]

    def test_metadata_keeps_env_values(self):
        with patch.dict(os.environ, {"APP_VERSION": "2.5.0", "API_BASE_URL": "https://custom.api.com/api/v1"}):
            metadata = _parse_frontmatter(SkillMdService().generate_skill_md(lang="en"))["metadata"]
        assert metadata["version"] == "2.5.0"
        assert metadata["api_base"] == "https://custom.api.com/api/v1"

    @pytest.mark.parametrize("lang", ["zh", "en"])
    def test_body_recommends_cli(self, lang):
        content = SkillMdService().generate_skill_md(lang=lang)
        body = content.split("---\n", 2)[2]
        assert "npx zenstory login\n" in body
        assert "npx zenstory login --key" not in body
        assert "Paste API key:" in body
        assert "npx zenstory skill install" in body
        # If a key leaked into a chat, the doc tells the user to regenerate it.
        assert ("重新生成" if lang == "zh" else "regenerate it") in body
        # The CLI section sits near the top, before authentication details.
        auth_heading = "## 认证说明" if lang == "zh" else "## Authentication"
        assert body.index("npx zenstory login") < body.index(auth_heading)

    @pytest.mark.parametrize("lang", ["zh", "en"])
    def test_self_hosted_login_command_carries_api_base(self, lang):
        with patch.dict(os.environ, {"API_BASE_URL": "https://zenstory.example.com/api/v1/"}):
            body = SkillMdService().generate_skill_md(lang=lang).split("---\n", 2)[2]
        assert "npx zenstory login --api-base https://zenstory.example.com/api/v1\n" in body


@pytest.mark.unit
class TestSingletonInstance:
    """Tests for the module-level singleton instance."""

    def test_singleton_exists(self):
        """Test that the singleton instance exists."""
        from services.skill_md_service import skill_md_service
        assert skill_md_service is not None
        assert isinstance(skill_md_service, SkillMdService)

    def test_singleton_can_generate_chinese(self):
        """Test that singleton can generate Chinese documentation."""
        content = skill_md_service.generate_skill_md(lang="zh")
        assert "小说写作" in content

    def test_singleton_can_generate_english(self):
        """Test that singleton can generate English documentation."""
        content = skill_md_service.generate_skill_md(lang="en")
        assert "Novel Writing" in content

    def test_singleton_is_consistent(self):
        """Test that singleton returns consistent results."""
        content1 = skill_md_service.generate_skill_md(lang="en")
        content2 = skill_md_service.generate_skill_md(lang="en")
        assert content1 == content2


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_case_sensitive_language_param(self):
        """Test that language parameter is case sensitive."""
        service = SkillMdService()

        # 'EN' (uppercase) should not match 'en' and defaults to Chinese
        content_upper = service.generate_skill_md(lang="EN")
        assert "小说写作" in content_upper or "欢迎使用" in content_upper

        # 'en' (lowercase) should return English
        content_lower = service.generate_skill_md(lang="en")
        assert "Novel Writing" in content_lower

    def test_none_language_defaults_to_chinese(self):
        """Test that None language defaults to Chinese."""
        service = SkillMdService()
        content = service.generate_skill_md(lang=None)
        assert "小说写作" in content or "欢迎使用" in content

    def test_content_is_string(self):
        """Test that generated content is a string."""
        service = SkillMdService()

        zh_content = service.generate_skill_md(lang="zh")
        en_content = service.generate_skill_md(lang="en")

        assert isinstance(zh_content, str)
        assert isinstance(en_content, str)

    def test_content_not_empty(self):
        """Test that generated content is not empty."""
        service = SkillMdService()

        zh_content = service.generate_skill_md(lang="zh")
        en_content = service.generate_skill_md(lang="en")

        assert len(zh_content) > 1000
        assert len(en_content) > 1000

    def test_special_chars_in_env_vars(self):
        """Test handling of special characters in environment variables."""
        with patch.dict(os.environ, {"APP_VERSION": "1.0.0-beta+build.123"}):
            service = SkillMdService()
            content = service.generate_skill_md(lang="en")
            assert "1.0.0-beta+build.123" in content

    def test_unicode_in_api_base(self):
        """Test handling of unicode characters in API base URL."""
        with patch.dict(os.environ, {"API_BASE_URL": "https://api.example.com/测试"}):
            service = SkillMdService()
            content = service.generate_skill_md(lang="zh")
            assert "https://api.example.com/测试" in content
