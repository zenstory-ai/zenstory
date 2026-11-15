from __future__ import annotations

from config.material_settings import MaterialSettings


class TestMaterialSettings:
    def test_relationship_extraction_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MATERIAL_ENABLE_RELATIONSHIP_EXTRACTION", raising=False)

        settings = MaterialSettings(_env_file=None)

        assert settings.ENABLE_RELATIONSHIP_EXTRACTION is False

    def test_relationship_extraction_can_be_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MATERIAL_ENABLE_RELATIONSHIP_EXTRACTION", "true")

        settings = MaterialSettings(_env_file=None)

        assert settings.ENABLE_RELATIONSHIP_EXTRACTION is True


_STAGE_ENV_VARS = (
    "MATERIAL_ENABLE_CHAPTER_SUMMARIES",
    "MATERIAL_ENABLE_PLOT_EXTRACTION",
    "MATERIAL_ENABLE_CHARACTER_EXTRACTION",
    "MATERIAL_ENABLE_META_EXTRACTION",
    "MATERIAL_ENABLE_NOVEL_SYNOPSIS",
    "MATERIAL_ENABLE_STORY_AGGREGATION",
    "MATERIAL_ENABLE_STORYLINE_GENERATION",
    "MATERIAL_ENABLE_RELATIONSHIP_EXTRACTION",
)


def _clean_settings(monkeypatch, **overrides) -> MaterialSettings:
    for name in _STAGE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return MaterialSettings(_env_file=None, **overrides)


class TestResolveEnabledStages:
    def test_token_saving_defaults(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        settings = _clean_settings(monkeypatch)
        stages = resolve_enabled_stages(settings)

        assert settings.ENABLE_PLOT_EXTRACTION is False
        assert settings.ENABLE_STORY_AGGREGATION is False
        assert settings.ENABLE_STORYLINE_GENERATION is False
        assert stages.as_snapshot() == {
            "chapter_summaries": True,
            "plots": False,
            "characters": True,
            "meta": True,
            "synopsis": True,
            "stories": False,
            "storylines": False,
            "relationships": False,
        }
        assert stages.dropped == {}
        assert stages.story_flow_needed is True

    def test_synopsis_requires_summaries(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        stages = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_CHAPTER_SUMMARIES=False, ENABLE_NOVEL_SYNOPSIS=True)
        )

        assert stages.synopsis is False
        assert "chapter_summaries" in stages.dropped["synopsis"]
        assert stages.story_flow_needed is False

    def test_stories_run_without_storylines_and_storylines_require_stories(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        only_aggregation = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_PLOT_EXTRACTION=True, ENABLE_STORY_AGGREGATION=True)
        )
        only_storylines = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_PLOT_EXTRACTION=True, ENABLE_STORYLINE_GENERATION=True)
        )
        both = resolve_enabled_stages(
            _clean_settings(
                monkeypatch,
                ENABLE_PLOT_EXTRACTION=True,
                ENABLE_STORY_AGGREGATION=True,
                ENABLE_STORYLINE_GENERATION=True,
            )
        )

        assert only_aggregation.stories is True
        assert only_aggregation.storylines is False
        assert only_aggregation.dropped == {}
        assert only_aggregation.story_flow_needed is True
        assert only_storylines.stories is False
        assert only_storylines.storylines is False
        assert only_storylines.dropped == {"storylines": "requires stories"}
        assert both.stories is True
        assert both.storylines is True
        assert both.dropped == {}

    def test_stories_require_plots_and_summaries(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        no_plots = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_STORY_AGGREGATION=True, ENABLE_STORYLINE_GENERATION=True)
        )
        no_summaries = resolve_enabled_stages(
            _clean_settings(
                monkeypatch,
                ENABLE_CHAPTER_SUMMARIES=False,
                ENABLE_PLOT_EXTRACTION=True,
                ENABLE_STORY_AGGREGATION=True,
                ENABLE_STORYLINE_GENERATION=True,
            )
        )

        assert no_plots.stories is False
        assert no_plots.storylines is False
        assert no_plots.dropped["stories"] == "requires plots"
        assert no_plots.dropped["storylines"] == "requires stories"
        assert no_summaries.stories is False
        assert no_summaries.dropped["stories"] == "requires chapter_summaries"

    def test_relationships_require_plots_and_characters(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        no_plots = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_RELATIONSHIP_EXTRACTION=True)
        )
        no_characters = resolve_enabled_stages(
            _clean_settings(
                monkeypatch,
                ENABLE_RELATIONSHIP_EXTRACTION=True,
                ENABLE_PLOT_EXTRACTION=True,
                ENABLE_CHARACTER_EXTRACTION=False,
            )
        )
        ok = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_RELATIONSHIP_EXTRACTION=True, ENABLE_PLOT_EXTRACTION=True)
        )

        assert no_plots.relationships is False
        assert no_plots.dropped["relationships"] == "requires plots"
        assert no_characters.relationships is False
        assert no_characters.dropped["relationships"] == "requires characters"
        assert ok.relationships is True

    def test_character_and_meta_are_independent(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        stages = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_CHARACTER_EXTRACTION=False, ENABLE_META_EXTRACTION=True)
        )

        assert stages.characters is False
        assert stages.meta is True

    def test_describe_lists_requested_effective_and_dropped(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        text = resolve_enabled_stages(
            _clean_settings(monkeypatch, ENABLE_STORY_AGGREGATION=True, ENABLE_STORYLINE_GENERATION=True)
        ).describe()

        assert "requested={chapter_summaries, characters, meta, synopsis, stories, storylines}" in text
        assert "effective={chapter_summaries, characters, meta, synopsis}" in text
        assert "stories: requires plots" in text


class TestDroppedStageWarnings:
    def test_only_explicitly_requested_dropped_stages_are_flagged(self, monkeypatch):
        from config.material_settings import resolve_enabled_stages

        # synopsis is requested by default only; dropping it is not a config conflict.
        implicit = resolve_enabled_stages(_clean_settings(monkeypatch, ENABLE_CHAPTER_SUMMARIES=False))
        assert "synopsis" in implicit.dropped
        assert implicit.explicitly_dropped == ()

        explicit = resolve_enabled_stages(
            _clean_settings(
                monkeypatch,
                ENABLE_CHAPTER_SUMMARIES=False,
                ENABLE_NOVEL_SYNOPSIS=True,
                ENABLE_STORYLINE_GENERATION=True,
                ENABLE_RELATIONSHIP_EXTRACTION=True,
            )
        )
        assert set(explicit.explicitly_dropped) == {"synopsis", "storylines", "relationships"}

    def test_warn_helper_logs_warning_only_for_explicit_drops(self, monkeypatch, caplog):
        import logging

        from config.material_settings import resolve_enabled_stages, warn_explicitly_dropped_stages

        log = logging.getLogger("test.dropped_stages")
        with caplog.at_level("WARNING", logger="test.dropped_stages"):
            warn_explicitly_dropped_stages(
                resolve_enabled_stages(_clean_settings(monkeypatch)), log, "ctx"
            )
            assert caplog.records == []
            warn_explicitly_dropped_stages(
                resolve_enabled_stages(_clean_settings(monkeypatch, ENABLE_STORY_AGGREGATION=True)),
                log,
                "ctx",
            )
        assert [r.levelname for r in caplog.records] == ["WARNING"]
        assert "stories: requires plots" in caplog.records[0].getMessage()

    def test_settings_load_warns_once_for_explicit_drop(self, tmp_path):
        """Importing the settings module (settings load) emits the WARNING once."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        server_dir = Path(__file__).resolve().parents[2]
        env = {k: v for k, v in os.environ.items() if not k.startswith("MATERIAL_")}
        env["MATERIAL_ENABLE_STORYLINE_GENERATION"] = "true"
        env["PYTHONPATH"] = str(server_dir)
        code = (
            "import logging, sys; logging.basicConfig(stream=sys.stdout, level=logging.WARNING, force=True);"
            "import config.material_settings"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, check=True
        ).stdout
        assert out.count("storylines: requires stories") == 1, out
