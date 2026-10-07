import scripts.seed_subscription_plans as seed


def test_free_plan_seed_includes_material_library_fields() -> None:
    assert seed.FREE_PLAN_FEATURES["ai_conversations_per_day"] == 10
    assert seed.FREE_PLAN_FEATURES["materials_library_access"] is False
    assert seed.FREE_PLAN_FEATURES["material_uploads"] == 0
    assert seed.FREE_PLAN_FEATURES["material_decompositions"] == 0


def test_pro_plan_seed_includes_material_library_fields() -> None:
    assert seed.PRO_PLAN_FEATURES["materials_library_access"] is True
    assert seed.PRO_PLAN_FEATURES["material_uploads"] == 5
    assert seed.PRO_PLAN_FEATURES["material_decompositions"] == 5
