"""单循环写作 agent：一个模型、一份只追加的对话、写作工具集。

- engine.py：一轮的运行循环（接在 AgentService.process_stream 的生成器接口上）。
- history.py：按会话消息行重建跨轮历史（含压缩点）。
- compaction.py：轮与轮之间的交接摘要压缩。
- turn_context.py：每轮附在作者消息前的结构化上下文。
- prompts.py：工作方式、输入布局与压缩提示。
- run_support.py：收尾说明、插话追加轮提示、正文字数与事件整理。
- completion_evidence.py：回复停在 <file> 正文上、文字里没执行的工具调用的协议识别。
"""
