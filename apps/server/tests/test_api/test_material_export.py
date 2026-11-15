"""Tests for one-click material breakdown TXT export (`GET /api/v1/materials/{novel_id}/export`)."""

import json
from datetime import datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from main import all_origins
from models import User
from models.material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    GoldenFinger,
    IngestionJob,
    Novel,
    Plot,
    Story,
    StoryLine,
    WorldView,
)
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from tests.quota_periods import current_month_start


async def _create_pro_user(
    client: AsyncClient,
    db_session,
    *,
    decompositions_used: int = 0,
    decompositions_limit: int = 5,
) -> tuple[User, dict[str, str]]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"pro_export_{suffix}",
        email=f"pro_export_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).first()
    if plan is None:
        plan = SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=4900,
            price_yearly_cents=39900,
            features={
                "materials_library_access": True,
                "material_decompositions": decompositions_limit,
                "export_formats": ["txt"],
            },
            is_active=True,
        )
        db_session.add(plan)
        db_session.commit()
        db_session.refresh(plan)

    now = datetime.utcnow()
    db_session.add(
        UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
    )
    if decompositions_used:
        month_start = current_month_start(utcnow())
        db_session.add(
            UsageQuota(
                user_id=user.id,
                period_start=now,
                period_end=now + timedelta(days=1),
                material_decompositions_used=decompositions_used,
                monthly_period_start=month_start,
                monthly_period_end=month_start + timedelta(days=30),
                last_reset_at=now,
            )
        )
    db_session.commit()

    login = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    assert login.status_code == 200
    return user, {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _create_free_user(
    client: AsyncClient,
    db_session,
    *,
    trial_used: bool = False,
) -> tuple[User, dict[str, str]]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"free_export_{suffix}",
        email=f"free_export_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    if trial_used:
        now = utcnow()
        month_start = current_month_start(now)
        db_session.add(
            UsageQuota(
                user_id=user.id,
                period_start=now,
                period_end=now + timedelta(days=1),
                monthly_period_start=month_start,
                monthly_period_end=month_start + timedelta(days=30),
                last_reset_at=now,
                material_trial_used_at=now,
            )
        )
        db_session.commit()

    login = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    assert login.status_code == 200
    return user, {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.integration
async def test_export_material_breakdown_includes_all_extracted_sections_and_omits_raw_manuscript(
    client: AsyncClient,
    db_session,
):
    user, headers = await _create_pro_user(client, db_session, decompositions_used=5, decompositions_limit=5)
    novel = Novel(
        user_id=user.id,
        title="九天/斩神诀: 卷一",
        author="小知了",
        synopsis="少帅林辰蒙冤下狱，得神秘天钨修复丹田。",
        source_meta=json.dumps(
            {
                "original_filename": "jiutian.txt",
                "file_path": "s3://private-bucket/materials/u1/secret-key.txt",
                "md5_checksum": "deadbeef1234567890",
            }
        ),
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    ch2 = Chapter(
        novel_id=novel.id,
        chapter_number=2,
        title="第二章 天钨认主",
        summary="玉佩化作天钨修复丹田，林辰重回聚力境一重。",
        original_content="【绝不应出现在导出文件里的第二章原始正文段落】",
    )
    ch1 = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章 蒙冤入狱",
        summary="林辰遭陷害下狱，丹田破碎。",
        original_content="【绝不应出现在导出文件里的第一章原始正文段落】",
    )
    db_session.add_all([ch2, ch1])
    db_session.commit()
    db_session.refresh(ch1)
    db_session.refresh(ch2)

    lin_chen = Character(
        novel_id=novel.id,
        name="林辰",
        aliases='["少帅", "辰哥"]',
        description="北境少帅，重情重义。",
        archetype="protagonist",
        first_appearance_chapter_id=ch2.id,
    )
    qin_yue = Character(
        novel_id=novel.id,
        name="秦月儿",
        description="探狱索要青玄令。",
        archetype="antagonist",
        first_appearance_chapter_id=ch1.id,
    )
    db_session.add_all([lin_chen, qin_yue])
    db_session.commit()
    db_session.refresh(lin_chen)
    db_session.refresh(qin_yue)

    db_session.add(
        WorldView(
            novel_id=novel.id,
            power_system="聚力境、凝元境",
            world_structure="大炎王朝与修真宗门并立",
            key_factions=json.dumps(
                [
                    {
                        "name": "青玄宗",
                        "leader": "玄霄真人",
                        "territory": "青玄山脉",
                        "description": "大炎一流宗门",
                    }
                ]
            ),
            special_rules="持青玄令免试入门",
        )
    )
    db_session.add(
        GoldenFinger(
            novel_id=novel.id,
            name="天钨",
            type="artifact",
            description="穿透禁制、遮掩气息。",
            first_appearance_chapter_id=ch2.id,
            evolution_history=json.dumps(
                [
                    {
                        "stage": "初醒",
                        "chapter": 2,
                        "timestamp": "狱中子夜",
                        "description": "修复破碎丹田",
                    }
                ]
            ),
        )
    )
    storyline = StoryLine(
        novel_id=novel.id,
        title="绝境重修线",
        description="从死牢脱困并夺回信物",
        main_characters='["林辰", "秦月儿"]',
        themes='["逆袭", "复仇"]',
    )
    db_session.add(storyline)
    db_session.commit()
    db_session.refresh(storyline)

    db_session.add(
        Story(
            novel_id=novel.id,
            story_line_id=storyline.id,
            title="死牢摊牌",
            synopsis="未婚妻探狱逼交信物",
            core_objective="保住青玄令并脱困",
            core_conflict="修为未复与外部逼迫",
            story_type="revenge",
            chapter_range="1-2",
            themes='["背叛"]',
        )
    )
    db_session.add(
        CharacterRelationship(
            novel_id=novel.id,
            character_a_id=lin_chen.id,
            character_b_id=qin_yue.id,
            relationship_type="enemy",
            sentiment="negative",
            description="决裂对立",
        )
    )
    db_session.add(
        Plot(
            chapter_id=ch1.id,
            index=0,
            plot_type="conflict",
            description="狱卒押解林辰入死牢",
            characters='["林辰"]',
        )
    )
    db_session.add(IngestionJob(novel_id=novel.id, source_path="mem://jiutian", status="completed"))
    db_session.commit()

    origin = all_origins[0]
    response = await client.get(
        f"/api/v1/materials/{novel.id}/export",
        headers={**headers, "Origin": origin, "Accept-Language": "zh-CN"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "content-disposition" in [
        h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")
    ]
    assert (
        response.headers["content-disposition"]
        == f"attachment; filename*=UTF-8''{quote('九天 斩神诀 卷一_拆解.txt')}"
    )
    assert response.content.startswith(b"\xef\xbb\xbf")

    text = response.content.decode("utf-8-sig")
    assert "九天/斩神诀: 卷一" in text
    assert "小知了" in text
    assert "少帅林辰蒙冤下狱，得神秘天钨修复丹田。" in text
    # Chapter summaries are ordered by chapter_number (ch1 before ch2)
    assert text.index("第一章 蒙冤入狱") < text.index("第二章 天钨认主")
    assert "林辰遭陷害下狱，丹田破碎。" in text
    assert "玉佩化作天钨修复丹田，林辰重回聚力境一重。" in text
    # Character details resolve actual chapter_number (2), not internal DB id
    assert "林辰" in text
    assert "主角" in text
    assert "少帅、辰哥" in text
    assert "第 2 章" in text
    # Worldview & faction details
    assert "聚力境、凝元境" in text
    assert "青玄宗" in text
    assert "玄霄真人" in text
    assert "青玄山脉" in text
    assert "大炎一流宗门" in text
    # Golden finger & evolution details
    assert "天钨" in text
    assert "法宝" in text
    assert "初醒" in text
    assert "修复破碎丹田" in text
    # Historical/optional stages present in DB are included
    assert "绝境重修线" in text
    assert "死牢摊牌" in text
    assert "林辰 ↔ 秦月儿" in text
    assert "决裂对立" in text
    assert "狱卒押解林辰入死牢" in text
    # Never leak raw manuscript body or internal storage paths/checksums
    assert "绝不应出现在导出文件里的" not in text
    assert "s3://private-bucket" not in text
    assert "deadbeef1234567890" not in text


@pytest.mark.integration
async def test_export_material_breakdown_supports_english_labels_and_trial_coverage_note(
    client: AsyncClient,
    db_session,
):
    user, headers = await _create_free_user(client, db_session, trial_used=True)
    novel = Novel(
        user_id=user.id,
        title="Star Blade",
        synopsis="The opening three chapters.",
        source_meta=json.dumps(
            {
                "chapter_count": 3,
                "source_chapter_count": 50,
                "trial_chapter_limit": 3,
            }
        ),
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    response = await client.get(
        f"/api/v1/materials/{novel.id}/export",
        headers={**headers, "Accept-Language": "en-US"},
    )

    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == f"attachment; filename*=UTF-8''{quote('Star Blade_breakdown.txt')}"
    )
    text = response.content.decode("utf-8-sig")
    assert "Star Blade" in text
    assert "first 3 of 50 chapters" in text
    assert "The opening three chapters." in text
    # Empty optional sections are omitted rather than printed as empty headings
    assert "Characters" not in text
    assert "Worldbuilding" not in text


@pytest.mark.integration
async def test_export_material_breakdown_rejects_empty_unauthorized_and_other_users(
    client: AsyncClient,
    db_session,
):
    pro_user, pro_headers = await _create_pro_user(client, db_session)
    other_pro, other_headers = await _create_pro_user(client, db_session)
    _free_user, free_headers = await _create_free_user(client, db_session, trial_used=False)

    # Novel with raw chapters only (no summary, no synopsis, no extracted entities yet)
    empty_novel = Novel(user_id=pro_user.id, title="排队中的书")
    db_session.add(empty_novel)
    db_session.commit()
    db_session.refresh(empty_novel)
    db_session.add(
        Chapter(
            novel_id=empty_novel.id,
            chapter_number=1,
            title="第一章",
            summary=None,
            original_content="只有原文尚未拆出摘要",
        )
    )
    db_session.commit()

    empty_resp = await client.get(f"/api/v1/materials/{empty_novel.id}/export", headers=pro_headers)
    assert empty_resp.status_code == 404
    assert empty_resp.json()["error_code"] == ErrorCode.MATERIAL_EXPORT_EMPTY

    # Free user without access or used trial is blocked by entitlement gate (402)
    denied_resp = await client.get(f"/api/v1/materials/{empty_novel.id}/export", headers=free_headers)
    assert denied_resp.status_code == 402
    assert denied_resp.json()["error_code"] == ErrorCode.FEATURE_NOT_INCLUDED

    # Another user cannot export someone else's material (403 isolation matching materials API)
    ready_novel = Novel(user_id=pro_user.id, title="私有拆解", synopsis="私有梗概")
    db_session.add(ready_novel)
    db_session.commit()
    db_session.refresh(ready_novel)

    cross_user_resp = await client.get(
        f"/api/v1/materials/{ready_novel.id}/export",
        headers=other_headers,
    )
    assert cross_user_resp.status_code == 403
    assert cross_user_resp.json()["error_code"] == ErrorCode.NOT_AUTHORIZED

    # Soft-deleted material returns 403
    ready_novel.deleted_at = utcnow()
    db_session.add(ready_novel)
    db_session.commit()
    deleted_resp = await client.get(
        f"/api/v1/materials/{ready_novel.id}/export",
        headers=pro_headers,
    )
    assert deleted_resp.status_code == 403
    assert deleted_resp.json()["error_code"] == ErrorCode.NOT_AUTHORIZED
