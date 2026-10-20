"""审稿人 / 返修 writer 思考强度对照评估（会调用付费 DeepSeek，几元钱）。

走生产的「送审 → 审稿 → 返修」路径：AgentService.process_stream + writing_graph，临时 SQLite 库。
只把两处固定下来，让每次运行都从同一份稿子开始：路由（直接给 writer）和第一个 writer run
（把预先埋好缺陷的一章写进库，并发出正文写入事件触发自动质检门）。之后的审稿人、返修 writer
都是真实模型调用，思考强度由 AGENT_REVIEWER_REASONING_EFFORT / AGENT_REWORK_REASONING_EFFORT 控制。

每章埋 5 个客观缺陷（生产审稿真实返回过的类型）：日期矛盾、数字对不上、与上一章 15 字以上逐字重复、
残留「（本章完）」类标记、对白缺引号（剧本为台词缺角色名）。评分：审稿人是否指出每个缺陷（关键词，
报告里附原文供人工复核）、额外提出的问题（可能的误报）、返修后缺陷是否消除、未埋缺陷的对照句是否
逐字保留、前两章是否未动、输出 / 思考 token 与成本。

用法（apps/server，项目 virtualenv；只需要 DEEPSEEK_API_KEY，不读 .env、不连任何数据库）::

    DEEPSEEK_API_KEY=... python scripts/eval_review_effort.py --repeats 3 --budget-cny 10 \\
        --out /tmp/review-effort.json

结果见 .agents/notes/implemented/process/2026-10-20-reviewer-rework-reasoning-effort.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 在导入任何项目模块之前把数据库固定到临时 SQLite：本脚本绝不连接其他库。
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="review-effort-", suffix=".db")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["RATE_LIMIT_BACKEND"] = "memory"
os.environ.pop("REDIS_URL", None)
os.environ["VECTOR_EMBEDDINGS_ENABLED"] = "false"
os.environ["ASYNC_VECTOR_INDEX_ENABLED"] = "false"
for _name in ("AGENT_REASONING_EFFORT", "AGENT_REVIEWER_REASONING_EFFORT", "AGENT_REWORK_REASONING_EFFORT"):
    os.environ.pop(_name, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel  # noqa: E402

import database  # noqa: E402
from agent.core.workflow_events import StreamEvent, StreamEventType  # noqa: E402
from agent.graph import writing_graph  # noqa: E402
from agent.openai_agents import usage_hooks  # noqa: E402
from agent.service import AgentService  # noqa: E402
from agent.tools.mcp_tools import ToolContext  # noqa: E402
from config import agent_runtime  # noqa: E402
from models import File, Project, SystemPromptConfig, User  # noqa: E402
from services.usage.pricing import cost_units, price_band, units_to_cny  # noqa: E402

EFFORTS: dict[str, str | None] = {"default": None, "medium": "medium", "low": "low"}


# --------------------------------------------------------------------------- fixtures
@dataclass(frozen=True)
class Defect:
    key: str
    # 审稿人提到它的证据：任一正则命中审稿人的交接 / 回复文字即算指出。
    mention: tuple[str, ...]
    # 返修后缺陷仍在：这个正则还能在第三章 / 第三集里匹配到。
    still_present: str


@dataclass(frozen=True)
class Case:
    genre: str
    project_type: str
    file_type: str
    folder_title: str
    unit: str
    prior: tuple[tuple[str, str], ...]
    title: str
    seeded: str
    defects: tuple[Defect, ...]
    controls: tuple[str, ...]
    request: str


NOVEL_CH1 = """十月三日傍晚，林舟背着一只旧木箱下了船。雾港的石阶被潮水泡得发黑，踩上去总有一点打滑。

码头管事老周叼着烟斗，看了他的路引，又看了看他磨破的鞋。“读书人？来雾港找活路的读书人可不多。”

“找人。”林舟说，“我舅舅三年前在这里开过一家书铺。”

老周没再问，只朝街口抬了抬下巴。林舟在码头边的顺风客栈要了一间最便宜的阁楼。他把钱袋倒在床板上数了两遍，一共三百文，是母亲卖掉织机换来的全部积蓄。

夜里潮声很大，他睁着眼睛听了很久，才想起自己连舅舅长什么样都已经记不清了。"""

NOVEL_CH2 = """十月四日一早，林舟去街口的回春堂给咳了一夜的自己抓药。

河面上的雾气像一层没有拧干的旧棉絮，沉沉地压在码头上。药铺掌柜是个瘦高的女人，听他报出舅舅的名字，手里的戥子停了一下。

“那家书铺早就关了。”她说，“去年冬天失了一场火，人也不见了。”

“不见了是什么意思？”

“就是不见了。”她把三包药推过来，“一百二十文。”

林舟付了钱，钱袋里只剩一百八十文。他攥着药包走出门，听见身后的掌柜低声叹了口气。"""

NOVEL_CH3 = """十月三日清晨，林舟在顺风客栈的阁楼上醒来，喉咙比昨天好了些，大概是昨天在回春堂抓的药起了作用。窗纸被潮气浸得发软，楼下伙计正在劈柴，一斧一斧，劈得很慢。

他把剩下的两包药用油纸重新包好，塞进木箱最底下，又把舅舅那封旧信摊在膝头看了一遍。信是三年前寄到家里的，只有短短几行，说雾港的书好卖，叫他读完书就过来帮忙。信封右下角盖着一枚小小的朱印，印文已经模糊，只认得出一个“文”字。

他决定先去书铺的旧址看看。书铺在鱼市后面的窄巷里，烧剩的门框还立着，黑得发亮。巷口坐着一个补渔网的老人，手指被麻线勒出一道道白印。

“这里原来住的人呢？”林舟问。

老人头也没抬：“火是半夜起的，等大家提着水桶赶来，屋顶已经塌了。”

“有人从里面出来吗？”

“没看见。”老人把梭子从网眼里穿过去，“也没看见有人被抬出来。官府的人来翻了两天，什么都没翻到，就贴了封条走了。”

封条早被雨水泡烂了，只剩门框上几道发白的浆糊印子。林舟跨过门槛，脚下的炭灰软得像泥。书架倒了一地，烧成一排排黑色的骨头，墙角还堆着一摞没烧透的书，书页粘在一起，掀不开。

他在废墟里站了很久，只捡到半块烧焦的铜镇纸，上面刻着一个“林”字。

镇纸很沉，断口却很齐，不像是被火烧裂的，倒像是被人用凿子凿开的。他用袖子擦了擦上面的灰，想起小时候在舅舅的书桌上见过一模一样的一对，一块刻“林”，一块刻“文”。

回客栈的路上，他在米铺买了三斤糙米，花了四十文；又付给船家二十文定金，约好夜里去对岸。前后一共花去八十文，钱袋里还剩一百文。

船家是个独眼的汉子，收钱的时候多看了他两眼，问他去对岸做什么。林舟说去看亲戚，船家就不再问了，只说亥时以后风大，要早些到渡口。

河面上的雾气像一层没有拧干的旧棉絮，沉沉地压在码头上。

天擦黑时，老周在客栈门口拦住了他。

老周压低声音说：今晚别去渡口，那边不太平。

“我舅舅的事，您知道多少？”林舟问。

老周把烟斗在门框上磕了磕，火星落进雾里，一下就灭了。“我只知道，打听他的人，后来都离开了雾港。”

“离开，还是不见了？”

老周没有回答。他转身进了客栈，过了一会儿又出来，往林舟手里塞了一盏防风的马灯。“灯芯是新换的，够你走到对岸。”

林舟道了声谢。他把那半块镇纸揣进怀里，转身朝渡口走去。

（本章完）"""

SCRIPT_EP1 = """第1集 雨夜来客

1-1 夜 外 旧码头
人物：沈默、阿桂
△ 字幕：三月十二日。
△ 大雨。一艘货船靠岸，沈默披着雨衣跳下甲板。
阿桂：（撑伞迎上）老板说货今晚必须进仓。
沈默：仓库钥匙在谁手里？
阿桂：在小陈那儿，他说十点前到。
△ 沈默看了一眼腕表，九点四十。

1-2 夜 内 仓库
人物：沈默、小陈
△ 小陈浑身湿透，把一串钥匙扔在桌上。
小陈：码头那边有警察，船先别卸。
沈默：（皱眉）谁报的警？
小陈：不知道。我只知道明天上午之前，这批货不能露面。"""

SCRIPT_EP2 = """第2集 警局

2-1 日 内 警局问询室
人物：沈默、周警官
△ 字幕：三月十三日。
周警官：昨晚十一点，你在旧码头做什么？
沈默：接朋友。
周警官：接朋友要带三把钥匙？
△ 沈默沉默。

2-2 夜 外 街道
人物：沈默、阿桂
△ 霓虹灯在积水里碎成一片一片，像被人踩烂的糖纸。
阿桂：（追上来）警察放你了？
沈默：他们没有证据。
阿桂：老板说，对方要钱，不要货。"""

SCRIPT_EP3 = """第3集 赎金

3-1 日 内 废弃厂房
人物：沈默、阿桂、小陈
△ 字幕：三月十二日。
△ 厂房顶棚漏雨，地上摆着一只黑色旅行袋。
阿桂：昨天警察放你出来，就是为了跟着你找到这里。
沈默：所以我们换了三次车。
△ 小陈拉开旅行袋，里面是成捆的现金。
小陈：赎金一共六十万，我们三个人平分，每人二十五万。
沈默：钱先不分。人没见到之前，谁也不许动。
阿桂：对方说今晚十点，江堤第三个路灯底下。
小陈：（把钱塞回袋子）我不去。上回码头的警察，就是冲我来的。
沈默：你留下看钱。阿桂开车，在堤下等我。
△ 阿桂把车钥匙抛给沈默，沈默又抛了回去。
沈默：车你开。我走过去，目标小。

3-2 夜 外 江堤
人物：沈默、神秘女人
△ 江风很大。一个戴黑色口罩的女人从堤坝下走上来。
△ 霓虹灯在积水里碎成一片一片，像被人踩烂的糖纸。
△ 沈默站在第三个路灯底下，旅行袋放在脚边。
你来晚了，货已经被人提走了。
沈默：（攥紧旅行袋）我要先见人。
神秘女人：人不在我这里。
沈默：那你来做什么？
神秘女人：替人带句话。（递过一张折起来的纸条）他说，钱留下，人明天自己会回家。
△ 沈默没有接纸条。
沈默：我凭什么信你？
神秘女人：你可以不信。
△ 远处传来警笛声，女人转身就走，沈默追了两步，停下。
△ 纸条掉在地上，被风吹着贴在沈默的鞋面上。
沈默：（对着江面）阿桂，把车开过来。

3-3 夜 内 车内
人物：沈默、阿桂
△ 阿桂开车，雨刷来回摆动。沈默展开纸条，借着路灯看。
阿桂：写的什么？
沈默：一个地址。旧码头，七号仓。
阿桂：（猛地踩下刹车）那是我们自己的仓库。
△ 两人对视。车外，警笛声越来越近。
沈默：先别回厂房。给小陈打电话，叫他带着钱换个地方。
阿桂：（拨号，听筒里一直是忙音）打不通。
△ 沈默推开车门，冒雨朝来路跑去。

（本集完）"""

NOVEL_DEFECTS = (
    Defect("timeline", (r"十月三日", r"日期", r"时间线", r"时间顺序"), r"十月三日清晨"),
    Defect("arithmetic", (r"八十文", r"四十.{0,6}二十", r"六十文", r"算术", r"账目", r"钱数", r"结余", r"余额", r"一百文"), r"一共花去八十文"),
    Defect("repeat", (r"旧棉絮", r"重复", r"雷同", r"复用"), r"河面上的雾气像一层没有拧干的旧棉絮"),
    Defect("meta_marker", (r"本章完", r"标记", r"章末"), r"（本章完）"),
    Defect("dialogue_quotes", (r"引号", r"今晚别去渡口"), r"(?m)^[^“\n]*说：今晚别去渡口"),
)
SCRIPT_DEFECTS = (
    Defect("timeline", (r"三月十二日", r"日期", r"时间线", r"字幕"), r"△ 字幕：三月十二日。\n△ 厂房顶棚"),
    Defect("arithmetic", (r"二十五万", r"六十万", r"平分", r"算术", r"金额"), r"每人二十五万"),
    Defect("repeat", (r"糖纸", r"重复", r"雷同", r"复用"), r"霓虹灯在积水里碎成一片一片，像被人踩烂的糖纸"),
    Defect("meta_marker", (r"本集完", r"标记", r"集末"), r"（本集完）"),
    Defect("speaker_label", (r"角色名", r"说话人", r"人物标识", r"谁说", r"台词.{0,8}(?:没有|缺|未)", r"你来晚了"), r"(?m)^你来晚了"),
)

CASES = (
    Case(
        genre="novel",
        project_type="novel",
        file_type="draft",
        folder_title="正文",
        unit="章",
        prior=(("第一章 雾港", NOVEL_CH1), ("第二章 药铺", NOVEL_CH2)),
        title="第三章 夜渡",
        seeded=NOVEL_CH3,
        defects=NOVEL_DEFECTS,
        controls=(
            "巷口坐着一个补渔网的老人，手指被麻线勒出一道道白印。",
            "他在废墟里站了很久，只捡到半块烧焦的铜镇纸，上面刻着一个“林”字。",
            "老周把烟斗在门框上磕了磕，火星落进雾里，一下就灭了。",
            "林舟道了声谢。他把那半块镇纸揣进怀里，转身朝渡口走去。",
            "船家是个独眼的汉子，收钱的时候多看了他两眼，问他去对岸做什么。",
        ),
        request="接着写第三章《夜渡》，林舟去书铺旧址，夜里准备渡河。",
    ),
    Case(
        genre="script",
        project_type="screenplay",
        file_type="script",
        folder_title="剧本",
        unit="集",
        prior=(("第1集 雨夜来客", SCRIPT_EP1), ("第2集 警局", SCRIPT_EP2)),
        title="第3集 赎金",
        seeded=SCRIPT_EP3,
        defects=SCRIPT_DEFECTS,
        controls=(
            "△ 厂房顶棚漏雨，地上摆着一只黑色旅行袋。",
            "沈默：钱先不分。人没见到之前，谁也不许动。",
            "△ 江风很大。一个戴黑色口罩的女人从堤坝下走上来。",
            "△ 远处传来警笛声，女人转身就走，沈默追了两步，停下。",
            "阿桂：（猛地踩下刹车）那是我们自己的仓库。",
        ),
        request="写第3集《赎金》：三人在废弃厂房清点赎金，沈默夜里去江堤交易。",
    ),
)


# --------------------------------------------------------------------------- recording
@dataclass
class AgentRun:
    agent_type: str
    rework: bool
    user_message: str = ""
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    handoff: dict[str, Any] | None = None


@dataclass
class Recorder:
    runs: list[AgentRun] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def cost_cny(self) -> float:
        return sum(call["cost_cny"] for call in self.calls)


RECORDER = Recorder()
_original_on_llm_end = usage_hooks.UsageMeteringMixin.on_llm_end


async def _recording_on_llm_end(self: Any, context: Any, agent: Any, response: Any) -> None:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    cached = int(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0)
    output = int(getattr(usage, "output_tokens", 0) or 0)
    reasoning = int(getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0)
    band = price_band(datetime.now(UTC))
    run = RECORDER.runs[-1] if RECORDER.runs else None
    RECORDER.calls.append(
        {
            "agent_type": getattr(agent, "name", "?"),
            "rework": bool(run and run.rework),
            "input_tokens": input_tokens,
            "cached_tokens": cached,
            "output_tokens": output,
            "reasoning_tokens": reasoning,
            "band": band,
            "cost_cny": float(units_to_cny(cost_units(band, cached, input_tokens - cached, output))),
        }
    )
    await _original_on_llm_end(self, context, agent, response)


usage_hooks.UsageMeteringMixin.on_llm_end = _recording_on_llm_end  # type: ignore[method-assign]


def _payload_event(call_id: str, name: str, tool_input: dict[str, Any], payload: dict[str, Any]) -> list[StreamEvent]:
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": call_id, "name": name, "status": "complete", "input": tool_input},
        ),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": name,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
            },
        ),
    ]


_REAL_RUN_STREAMING_AGENT = writing_graph.run_streaming_agent


def _install_patches(case: Case, target_file_id: str) -> None:
    real_run = _REAL_RUN_STREAMING_AGENT
    scripted = {"done": False}

    async def router(_state: Any) -> dict[str, Any]:
        return {
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "eval: fixed route",
                "confidence": 1.0,
                "write_content": True,
                "read_only": False,
                "scope": f"写{case.title}",
                "requested_file_types": [case.file_type],
            },
        }

    async def run_streaming_agent(state: Any, agent_type: str, *args: Any, **kwargs: Any):
        if agent_type == "writer" and not scripted["done"]:
            # 第一个 writer：把埋好缺陷的一章写进库，等同一次成功的正文写入。
            scripted["done"] = True
            with Session(database.sync_engine) as session:
                row = session.get(File, target_file_id)
                assert row is not None
                row.content = case.seeded
                row.updated_at = datetime.now(UTC).replace(tzinfo=None)
                session.add(row)
                session.commit()
            ToolContext.remember_pre_write_content(target_file_id, "")
            state["repeat_read_guard"].files_written[target_file_id] = case.title
            RECORDER.runs.append(AgentRun(agent_type="writer(scripted)", rework=False))
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": "writer"})
            for event in _payload_event(
                "seed-write",
                "edit_file",
                {"id": target_file_id, "edits": [{"op": "append", "text": case.seeded}]},
                {"status": "success", "data": {"id": target_file_id, "title": case.title, "file_type": case.file_type}},
            ):
                yield event
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{case.title}写好了。"})
            return

        run = AgentRun(
            agent_type=agent_type,
            rework=state.get("review_rework") is True,
            user_message=str(state.get("user_message") or ""),
        )
        RECORDER.runs.append(run)
        async for event in real_run(state, agent_type, *args, **kwargs):
            data = event.data if isinstance(event.data, dict) else {}
            if event.type == StreamEventType.TEXT:
                run.text += str(data.get("text") or "")
            elif event.type == StreamEventType.TOOL_USE and data.get("status") == "complete":
                run.tool_calls.append({"name": data.get("name"), "input": data.get("input")})
            elif event.type == StreamEventType.HANDOFF:
                run.handoff = {key: data.get(key) for key in ("target_agent", "reason", "context", "handoff_packet")}
            yield event

    writing_graph.router_node = router  # type: ignore[assignment]
    writing_graph.run_streaming_agent = run_streaming_agent  # type: ignore[assignment]


# --------------------------------------------------------------------------- one run
def _seed_project(case: Case) -> tuple[str, str, str, list[str]]:
    with Session(database.sync_engine) as session:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"effort_eval_{suffix}",
            email=f"effort_eval_{suffix}@example.com",
            hashed_password="x",
            email_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        project = Project(name=f"思考强度评估 {case.genre} {suffix}", owner_id=user.id, project_type=case.project_type)
        session.add(project)
        session.commit()
        session.refresh(project)
        folder = File(project_id=project.id, title=case.folder_title, file_type="folder", order=0)
        session.add(folder)
        session.commit()
        session.refresh(folder)
        prior_ids = []
        for order, (title, content) in enumerate(case.prior):
            row = File(
                project_id=project.id, title=title, content=content, file_type=case.file_type,
                parent_id=folder.id, order=order,
            )
            session.add(row)
            session.commit()
            prior_ids.append(row.id)
        target = File(
            project_id=project.id, title=case.title, content="", file_type=case.file_type,
            parent_id=folder.id, order=len(case.prior),
        )
        session.add(target)
        session.commit()
        return user.id, project.id, target.id, prior_ids


def _seed_prompt_configs() -> None:
    with Session(database.sync_engine) as session:
        for project_type, role in (("novel", "中文长篇小说"), ("screenplay", "中文短剧剧本")):
            session.add(
                SystemPromptConfig(
                    project_type=project_type,
                    role_definition=f"你是 ZenStory 的{role}写作助手，和作者一起在项目文件里写作与修改。",
                    capabilities="读写项目文件、按作者要求创作、修改与审稿。",
                    writing_guidelines="尊重作者已写的设定与前文事实；修改只改需要改的地方。",
                )
            )
        session.commit()


def _file_content(file_id: str) -> str:
    with Session(database.sync_engine) as session:
        row = session.get(File, file_id)
        return row.content if row is not None else ""


def _reviewer_text(run: AgentRun) -> str:
    parts = [run.text]
    if run.handoff:
        parts.append(json.dumps(run.handoff, ensure_ascii=False))
    for call in run.tool_calls:
        if call.get("name") in {"handoff_to_agent", "request_clarification"}:
            parts.append(json.dumps(call.get("input"), ensure_ascii=False))
    return "\n".join(parts)


def _todo_items(run: AgentRun) -> list[str]:
    packet = (run.handoff or {}).get("handoff_packet") or {}
    return [str(item) for item in packet.get("todo") or []]


def _score(case: Case, final_text: str, prior_after: list[str]) -> dict[str, Any]:
    reviews = [run for run in RECORDER.runs if run.agent_type == "quality_reviewer"]
    reworks = [run for run in RECORDER.runs if run.agent_type == "writer" and run.rework]
    first_review = _reviewer_text(reviews[0]) if reviews else ""
    todo = _todo_items(reviews[0]) if reviews else []
    found = {d.key: any(re.search(pattern, first_review) for pattern in d.mention) for d in case.defects}
    unmatched_todo = [
        item for item in todo if not any(re.search(p, item) for d in case.defects for p in d.mention)
    ]
    fixed = {d.key: re.search(d.still_present, final_text) is None for d in case.defects}
    return {
        "reviewer_runs": len(reviews),
        "rework_runs": len(reworks),
        "handed_back": bool(reviews and (reviews[0].handoff or {}).get("target_agent") == "writer"),
        "found": found,
        "recall": sum(found.values()) / len(case.defects),
        "todo": todo,
        "todo_not_matching_a_seeded_defect": unmatched_todo,
        "fixed": fixed,
        "fixed_count": sum(fixed.values()),
        "fixed_of_found": sum(1 for key, hit in found.items() if hit and fixed[key]),
        "controls_kept": sum(1 for sentence in case.controls if sentence in final_text),
        "controls_total": len(case.controls),
        "prior_untouched": prior_after == [content for _title, content in case.prior],
        "final_chars": len(final_text),
        # 算术缺陷的修法不止一种（改总数要连带改余额），这一句留给人工核对。
        "arithmetic_line": next((line for line in final_text.splitlines() if "糙米" in line or "平分" in line), ""),
    }


async def run_once(case: Case, effort_label: str, repeat: int) -> dict[str, Any]:
    # "low" 表示两个角色同档；"default/low" 表示审稿人 default、返修 low（单独看返修）。
    reviewer_label, _, rework_label = effort_label.partition("/")
    agent_runtime.AGENT_REVIEWER_REASONING_EFFORT = EFFORTS[reviewer_label]
    agent_runtime.AGENT_REWORK_REASONING_EFFORT = EFFORTS[rework_label or reviewer_label]
    RECORDER.runs.clear()
    RECORDER.calls.clear()

    user_id, project_id, target_id, prior_ids = _seed_project(case)
    _install_patches(case, target_id)
    started = time.monotonic()
    frames: list[str] = []
    with Session(database.sync_engine) as session:
        async for frame in AgentService().process_stream(
            project_id=project_id,
            user_id=user_id,
            message=case.request,
            session=session,
            metadata={"generation_mode": "quality", "current_file_id": target_id},
            language="zh",
        ):
            frames.append(frame)
    final_text = _file_content(target_id)
    prior_after = [_file_content(file_id) for file_id in prior_ids]
    score = _score(case, final_text, prior_after)
    by_role: dict[str, dict[str, float]] = {}
    for call in RECORDER.calls:
        role = "rework" if call["rework"] else call["agent_type"]
        bucket = by_role.setdefault(role, {"calls": 0, "output_tokens": 0, "reasoning_tokens": 0, "cost_cny": 0.0})
        bucket["calls"] += 1
        bucket["output_tokens"] += call["output_tokens"]
        bucket["reasoning_tokens"] += call["reasoning_tokens"]
        bucket["cost_cny"] += call["cost_cny"]
    errors = [frame for frame in frames if frame.startswith("event: error")]
    return {
        "genre": case.genre,
        "effort": effort_label,
        "repeat": repeat,
        "seconds": round(time.monotonic() - started, 1),
        "errors": errors,
        "score": score,
        "by_role": by_role,
        "cost_cny": RECORDER.cost_cny(),
        "runs": [
            {
                "agent_type": run.agent_type,
                "rework": run.rework,
                "user_message": run.user_message,
                "text": run.text,
                "tool_calls": run.tool_calls,
                "handoff": run.handoff,
            }
            for run in RECORDER.runs
        ],
        "final_text": final_text,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--efforts", default="default,medium,low", help="逗号分隔；审稿人/返修分开时写 default/low")
    parser.add_argument("--genres", default="novel,script")
    parser.add_argument("--budget-cny", type=float, default=10.0, help="累计成本到这个数就停（含正在跑的那次）")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is required")

    SQLModel.metadata.create_all(database.sync_engine)
    _seed_prompt_configs()
    cases = [case for case in CASES if case.genre in args.genres.split(",")]
    efforts = args.efforts.split(",")
    results: list[dict[str, Any]] = []
    spent = 0.0
    out = Path(args.out)
    # 交错执行（每轮把各档各跑一次），时间段与缓存状态对各档公平。
    for repeat in range(1, args.repeats + 1):
        for case in cases:
            for effort_label in efforts:
                if spent >= args.budget_cny:
                    print(f"budget reached: ¥{spent:.3f}", flush=True)
                    break
                result = await run_once(case, effort_label, repeat)
                spent += result["cost_cny"]
                results.append(result)
                score = result["score"]
                print(
                    f"{case.genre:6} {effort_label:7} #{repeat} recall={score['recall']:.0%} "
                    f"fixed={score['fixed_count']}/{len(case.defects)} controls={score['controls_kept']}/"
                    f"{score['controls_total']} prior_ok={score['prior_untouched']} "
                    f"cost=¥{result['cost_cny']:.4f} total=¥{spent:.3f} {result['seconds']}s "
                    f"errors={len(result['errors'])}",
                    flush=True,
                )
                out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        Path(_DB_PATH).unlink(missing_ok=True)
