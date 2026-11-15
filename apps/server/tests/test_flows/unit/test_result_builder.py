from flows.pipelines.helpers.result_builder import ResultBuilder


class TestResultBuilder:
    def test_build_final_result_carries_retryable_meta_failure(self):
        outcome = {
            "executed": True,
            "succeeded": False,
            "error": "provider unavailable",
            "retry_available": True,
        }
        result = ResultBuilder.build_final_result(
            novel_id=1,
            job_id=2,
            chapter_ids=[3],
            stage1_result={"failed_count": 0, "meta_extraction": outcome},
            story_result={},
            relationship_result={},
            character_entity_result={},
            status="completed_with_errors",
            elapsed_ms=1,
        )

        assert result["meta_extraction"] == outcome
        assert result["failed_count"] == 1

    def test_build_final_result_with_full_inputs(self):
        result = ResultBuilder.build_final_result(
            novel_id=101,
            job_id=202,
            chapter_ids=[1, 2, 3],
            stage1_result={
                "summaries_count": 3,
                "plots_count": 8,
                "mentions_extracted": True,
            },
            story_result={
                "synopsis_generated": True,
                "stories_count": 2,
                "storylines_count": 4,
                "failed_stories": ["story-3"],
            },
            relationship_result={
                "relationships_count": 9,
            },
            character_entity_result={
                "created_count": 5,
                "updated_count": 6,
                "failed_count": 1,
                "failed_characters": ["赵四"],
            },
            status="completed_with_errors",
            elapsed_ms=1234,
        )

        assert result == {
            "novel_id": 101,
            "job_id": 202,
            "chapters_count": 3,
            "summaries_count": 3,
            "plots_count": 8,
            "mentions_extracted": True,
            "synopsis_generated": True,
            "stories_count": 2,
            "storylines_count": 4,
            "relationships_count": 9,
            "characters_created": 5,
            "characters_updated": 6,
            "failed_count": 2,
            "failed_chapters": [],
            "failed_mention_chapters": [],
            "failed_stories": ["story-3"],
            "failed_characters": ["赵四"],
            "status": "completed_with_errors",
            "elapsed_ms": 1234,
        }

    def test_build_final_result_handles_none_stage1(self):
        result = ResultBuilder.build_final_result(
            novel_id=1,
            job_id=None,
            chapter_ids=[11],
            stage1_result=None,
            story_result={},
            relationship_result={},
            character_entity_result={},
            status="completed",
            elapsed_ms=0,
        )

        assert result["summaries_count"] == 0
        assert result["plots_count"] == 0
        assert result["mentions_extracted"] is False
        assert result["chapters_count"] == 1
        assert result["failed_count"] == 0
        assert result["status"] == "completed"
