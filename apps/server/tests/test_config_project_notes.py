"""项目备注来源分区的解析与合并（config/project_notes）。"""

import pytest

from config.project_notes import (
    CONFIRMED_HEADER,
    PROPOSALS_HEADER,
    merge_project_notes,
    presented_project_proposals,
    split_project_notes,
    verified_author_quotes,
)


@pytest.mark.unit
def test_split_legacy_notes_are_unlabeled():
    sections = split_project_notes("反派身份第20章前不得揭露\n女主第5章才出场")
    assert sections.unlabeled == ["反派身份第20章前不得揭露", "女主第5章才出场"]
    assert sections.confirmed == [] and sections.proposals == []


@pytest.mark.unit
def test_split_recognizes_both_headers():
    text = f"旧条目\n\n{CONFIRMED_HEADER}\n主角不会游泳\n\n{PROPOSALS_HEADER}\n- 北境封印牵动整个王朝"
    sections = split_project_notes(text)
    assert sections.unlabeled == ["旧条目"]
    assert sections.confirmed == ["主角不会游泳"]
    assert sections.proposals == ["- 北境封印牵动整个王朝"]


@pytest.mark.unit
def test_quotes_must_appear_in_author_messages_ignoring_punctuation():
    messages = ["记住：女主，第五章才出场！"]
    assert verified_author_quotes(["女主第五章才出场"], messages) == ["女主第五章才出场"]
    assert verified_author_quotes(["女主第三章出场"], messages) == []
    # 一个字的「原话」（如「好」）太短，不能当依据
    assert verified_author_quotes(["女"], messages) == []


@pytest.mark.unit
def test_merge_without_author_context_keeps_old_behavior():
    merged = merge_project_notes(
        None, notes="主角冷静", proposals=None, author_quotes=None, author_messages=None
    )
    # 和以前一样原样存成纯文本：不核对，也不冒称作者确认
    assert merged.text == "主角冷静"
    assert merged.downgraded == []


@pytest.mark.unit
def test_merge_mixed_line_splits_entries_by_source():
    merged = merge_project_notes(
        f"{CONFIRMED_HEADER}\n主角冷静",
        notes="主角冷静；白石镇是主角老家",
        proposals=None,
        author_quotes=None,
        author_messages=["接着写第三章"],
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == ["主角冷静"]
    assert sections.proposals == ["白石镇是主角老家"]
    assert merged.downgraded == ["白石镇是主角老家"]


@pytest.mark.unit
def test_merge_empty_notes_clears_everything():
    merged = merge_project_notes(
        f"{CONFIRMED_HEADER}\nA约定\n{PROPOSALS_HEADER}\nB提议",
        notes="",
        proposals=None,
        author_quotes=None,
        author_messages=["把备注清空"],
    )
    assert merged.text == ""


@pytest.mark.unit
def test_merge_proposals_only_keeps_confirmed_and_replaces_proposals():
    merged = merge_project_notes(
        f"{CONFIRMED_HEADER}\nA约定\n{PROPOSALS_HEADER}\n旧提议",
        notes=None,
        proposals="新提议一\n新提议二",
        author_quotes=None,
        author_messages=["你有什么想法"],
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == ["A约定"]
    assert sections.proposals == ["新提议一", "新提议二"]


@pytest.mark.unit
def test_merge_inference_cannot_ride_along_with_a_real_author_quote():
    """一句真实的作者原话只撑得住它自己那条：同一次调用里夹带的推断照样记成 AI 提议。"""
    merged = merge_project_notes(
        "反派身份第20章前不得揭露",
        notes="反派身份第20章前不得揭露；女主要到第五章才出场；沧不夺舍",
        proposals=None,
        author_quotes=["女主要到第五章才出场"],
        author_messages=["记住，女主要到第五章才出场"],
    )
    sections = split_project_notes(merged.text)
    assert merged.confirmed_added == ["女主要到第五章才出场"]
    assert merged.downgraded == ["沧不夺舍"]
    assert sections.unlabeled == ["反派身份第20章前不得揭露"]
    assert sections.confirmed == ["女主要到第五章才出场"]
    assert sections.proposals == ["沧不夺舍"]


@pytest.mark.unit
def test_merge_entry_cannot_add_words_beyond_the_author_quote():
    """条目只能取作者原话里的内容：在原话后面接上推断，整条记成 AI 提议。"""
    merged = merge_project_notes(
        None,
        notes="女主要到第五章才出场，她是前朝公主",
        proposals=None,
        author_quotes=["女主要到第五章才出场"],
        author_messages=["记住，女主要到第五章才出场"],
    )
    assert merged.confirmed_added == []
    assert merged.downgraded == ["女主要到第五章才出场，她是前朝公主"]


@pytest.mark.unit
def test_merge_entry_may_shorten_the_quote_and_spell_numbers_either_way():
    merged = merge_project_notes(
        None,
        notes="女主第5章才出场",
        proposals=None,
        author_quotes=["记住，女主第五章才出场"],
        author_messages=["记住，女主第五章才出场，别提前写她。"],
    )
    assert merged.confirmed_added == ["女主第5章才出场"]
    assert merged.downgraded == []


@pytest.mark.unit
def test_merge_existing_proposal_is_not_promoted_by_an_unrelated_quote():
    merged = merge_project_notes(
        f"{PROPOSALS_HEADER}\n沧不夺舍",
        notes="女主要到第五章才出场；沧不夺舍",
        proposals=None,
        author_quotes=["女主要到第五章才出场"],
        author_messages=["记住，女主要到第五章才出场"],
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == ["女主要到第五章才出场"]
    assert sections.proposals == ["沧不夺舍"]
    assert merged.downgraded == ["沧不夺舍"]


@pytest.mark.unit
def test_merge_proposal_is_promoted_when_the_author_repeats_its_content():
    merged = merge_project_notes(
        f"{PROPOSALS_HEADER}\n魂灯有代价：点一次少活一年",
        notes="魂灯有代价：点一次少活一年",
        proposals=None,
        author_quotes=["点一次少活一年"],
        author_messages=["灯油那个代价就按你说的，点一次少活一年。"],
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == ["魂灯有代价：点一次少活一年"]
    assert sections.proposals == []


@pytest.mark.unit
def test_merge_proposal_is_not_promoted_by_a_bare_agreement():
    """作者只回「可以」：核对不到同意的是哪一条，提议留在提议里。"""
    merged = merge_project_notes(
        f"{PROPOSALS_HEADER}\n魂灯有代价：点一次少活一年",
        notes="魂灯有代价：点一次少活一年",
        proposals=None,
        author_quotes=["可以"],
        author_messages=["可以"],
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == []
    assert sections.proposals == ["魂灯有代价：点一次少活一年"]


_BATCH = ['主角不会游泳', '魂灯点一次少活一年', '女主第五章才出场']
_SHOWN_BATCH = list(enumerate(_BATCH, start=1))

_GROUPED_PARTS = [
    '账本里每月那笔汇款的代号，是旧灯塔当年的值守编号',
    '收款方是岛外一家早已注销的船务公司，父亲十年的钱是在替某个不在岛上的人，养着一条船的名义。后续可让沈岚顺着邮局的汇款底单去查这个代号。',
    '信封上“沈岚”三字的写法，出自父亲教她写字时用的旧字帖',
    '2016年那封寄给父亲、署名“沈岚”的信，是父亲照着这个写法替不在岛上的女儿署的名，写给他自己的。后续可让沈岚在旧字帖或童年作业本上找到同一写法来印证。',
]
_GROUPED_BATCH = ['；'.join(_GROUPED_PARTS[:2]), '；'.join(_GROUPED_PARTS[2:])]
_GROUPED_CURRENT = PROPOSALS_HEADER + '\n' + '\n'.join(
    f'{index}. {text}' for index, text in enumerate(_GROUPED_BATCH, start=1)
)
_GROUPED_SHOWN = presented_project_proposals(
    '目前完整的待确认提议，共 2 条：\n\n'
    + '\n'.join(f'{index}. {text}' for index, text in enumerate(_GROUPED_BATCH, start=1))
    + '\n\n下一轮你可以回「确认第1、2条」或「确认以上全部提议」。'
)


@pytest.mark.unit
@pytest.mark.parametrize(('reply', 'accepted'), [
    ('确认以上全部提议', [0, 1, 2, 3]),
    ('确认第1条', [0, 1]),
    ('确认第2条', [2, 3]),
])
def test_reference_confirms_all_semicolon_parts_of_selected_presented_groups(reply, accepted):
    merged = merge_project_notes(
        _GROUPED_CURRENT, notes='；'.join(_GROUPED_BATCH), proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=_GROUPED_SHOWN,
    )
    assert merged.confirmed_added == [_GROUPED_PARTS[index] for index in accepted]
    remaining = [entry for index, entry in enumerate(_GROUPED_PARTS) if index not in accepted]
    sections = split_project_notes(merged.text)
    assert sections.confirmed == ['；'.join(_GROUPED_PARTS[index] for index in accepted)]
    assert len(sections.proposals) == (1 if remaining else 0)
    for entry in remaining:
        assert entry in sections.proposals[0]


@pytest.mark.unit
@pytest.mark.parametrize('shown', [
    [(1, _GROUPED_BATCH[0]), (2, _GROUPED_PARTS[2])],
    [(1, _GROUPED_BATCH[0] + '；父亲还藏着另一条船'), (2, _GROUPED_BATCH[1])],
    [(1, _GROUPED_PARTS[0] + '；' + _GROUPED_PARTS[0]), (2, _GROUPED_BATCH[1])],
    [(1, _GROUPED_BATCH[0]), (2, _GROUPED_BATCH[1].replace('旧字帖', '旧手稿'))],
    [(1, _GROUPED_BATCH[0]), (2, '；'), (3, _GROUPED_BATCH[1])],
    [(1, _GROUPED_BATCH[0]), (3, _GROUPED_BATCH[1])],
])
def test_group_reference_rejects_incomplete_changed_duplicate_or_empty_parts(shown):
    reply = '确认以上全部提议'
    merged = merge_project_notes(
        _GROUPED_CURRENT, notes='；'.join(_GROUPED_BATCH), proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=shown,
    )
    sections = split_project_notes(merged.text)
    assert merged.confirmed_added == []
    assert sections.confirmed == []
    assert sections.proposals == split_project_notes(_GROUPED_CURRENT).proposals


@pytest.mark.unit
@pytest.mark.parametrize(('reply', 'accepted'), [
    ('确认以上全部提议', [0, 1, 2, 3]),
    ('确认第1条', [2, 3]),
    ('确认第2条', [0, 1]),
])
def test_reordered_complete_groups_keep_the_displayed_selection_indices(reply, accepted):
    merged = merge_project_notes(
        _GROUPED_CURRENT, notes='；'.join(_GROUPED_BATCH), proposals=None,
        author_quotes=[reply], author_messages=[reply],
        previous_proposals=[(1, _GROUPED_BATCH[1]), (2, _GROUPED_BATCH[0])],
    )
    assert merged.confirmed_added == [_GROUPED_PARTS[index] for index in accepted]


@pytest.mark.unit
@pytest.mark.parametrize(
    ('reply', 'accepted'),
    [
        ('确认第1、3条', [0, 2]),
        ('确认第一、三条', [0, 2]),
        ('1', [0]),
        ('第2条', [1]),
        ('确认以上全部提议', [0, 1, 2]),
        ('全部确认。', [0, 1, 2]),
        ('Confirm all proposals.', [0, 1, 2]),
        ('Confirm 1 and 3', [0, 2]),
    ],
)
def test_merge_author_can_reference_exact_current_presented_proposal_batch(reply, accepted):
    merged = merge_project_notes(
        PROPOSALS_HEADER + '\n' + '\n'.join(_BATCH),
        notes='\n'.join(_BATCH), proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=_SHOWN_BATCH,
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == [_BATCH[index] for index in accepted]
    assert sections.proposals == [entry for index, entry in enumerate(_BATCH) if index not in accepted]


@pytest.mark.unit
@pytest.mark.parametrize('reply', [
    '好', '可以', '谢谢', '确认以上全部提议？', '确认第1条？',
    '不要确认第1条', '不确认全部提议', '确认第1条，但第2条不要',
    '确认第1条\n1. 另一份清单', '确认第4条', '确认第1、4条',
])
def test_merge_does_not_guess_ambiguous_negative_or_unrelated_batch_consent(reply):
    merged = merge_project_notes(
        PROPOSALS_HEADER + '\n' + '\n'.join(_BATCH),
        notes='\n'.join(_BATCH), proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=_SHOWN_BATCH,
    )
    assert split_project_notes(merged.text).confirmed == []
    assert split_project_notes(merged.text).proposals == _BATCH


@pytest.mark.unit
@pytest.mark.parametrize('shown', [
    None,
    [(1, _BATCH[0])],
    [(1, _BATCH[0]), (2, _BATCH[1]), (3, '女主第三章才出场')],
    [(1, _BATCH[0]), (2, _BATCH[0]), (3, _BATCH[2])],
    [(2, _BATCH[0]), (3, _BATCH[1]), (4, _BATCH[2])],
])
def test_merge_reference_cannot_confirm_missing_changed_or_different_proposal_batch(shown):
    reply = '确认以上全部提议'
    merged = merge_project_notes(
        PROPOSALS_HEADER + '\n' + '\n'.join(_BATCH),
        notes='\n'.join(_BATCH), proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=shown,
    )
    assert split_project_notes(merged.text).confirmed == []


@pytest.mark.unit
@pytest.mark.parametrize(('messages', 'quotes'), [
    (['继续写'], ['确认以上全部提议']),
    (['不要确认第1条'], ['确认第1条']),
    (['确认以上全部提议', '先不确认'], ['确认以上全部提议']),
    (['确认第1条，但第2条不要'], ['确认第1条']),
])
def test_merge_reference_needs_complete_current_author_response_not_model_claim(messages, quotes):
    merged = merge_project_notes(
        PROPOSALS_HEADER + '\n' + '\n'.join(_BATCH),
        notes='\n'.join(_BATCH), proposals=None,
        author_quotes=quotes, author_messages=messages, previous_proposals=_SHOWN_BATCH,
    )
    assert split_project_notes(merged.text).confirmed == []


@pytest.mark.unit
def test_merge_reference_cannot_confirm_new_inference_riding_with_selected_proposal():
    reply = '确认第1条'
    merged = merge_project_notes(
        PROPOSALS_HEADER + '\n' + '\n'.join(_BATCH),
        notes=_BATCH[0] + '\n主角其实是前朝王子', proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=_SHOWN_BATCH,
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == [_BATCH[0]]
    assert '主角其实是前朝王子' in sections.proposals


@pytest.mark.unit
@pytest.mark.parametrize('lead', ['待确认提议如下：', '请确认以下提议：', 'Pending proposals:', 'Please confirm the following proposals:'])
def test_presented_proposals_preserves_full_list_and_ignores_topic_headings(lead):
    reply = lead + '\n**一、人物**\n1. 主角不会游泳\n**二、道具**\n2. 魂灯点一次少活一年\n说明：请在对话中确认。'
    assert presented_project_proposals(reply) == [(1, _BATCH[0]), (2, _BATCH[1])]


@pytest.mark.unit
@pytest.mark.parametrize('reply', [
    '这轮做了：\n1. 主角不会游泳\n2. 魂灯点一次少活一年',
    '待确认提议已全部确认，本轮完成：\n1. 主角不会游泳\n2. 魂灯点一次少活一年',
    '待确认提议仍保留。\n这次改好了。\n要我怎么走：\n1. 主角不会游泳\n2. 魂灯点一次少活一年',
    'Pending proposals are already confirmed:\n1. 主角不会游泳\n2. 魂灯点一次少活一年',
    '1. 主角不会游泳\n2. 魂灯点一次少活一年',
    '待确认提议如下：\n1. 主角不会游泳\n3. 魂灯点一次少活一年',
    '待确认提议如下：\n1. 主角不会游泳\n2. 魂灯点一次少活一年\n下一步：\n1. 继续写',
])
def test_presented_proposals_rejects_reports_and_incomplete_or_multiple_lists(reply):
    assert presented_project_proposals(reply) == []


@pytest.mark.unit
def test_complex_revisions_and_remaining_confirmation_do_not_guess_old_proposal_scope():
    reply = '魂灯改为不消耗寿命，主角改为会游泳，除了我说的修改其余全部确定'
    new_rule = '魂灯改为不消耗寿命'
    merged = merge_project_notes(
        PROPOSALS_HEADER + '\n' + '\n'.join(_BATCH),
        notes='\n'.join([*_BATCH, new_rule]), proposals=None,
        author_quotes=[reply], author_messages=[reply], previous_proposals=_SHOWN_BATCH,
    )
    sections = split_project_notes(merged.text)
    assert sections.confirmed == [new_rule]
    assert sections.proposals == _BATCH
