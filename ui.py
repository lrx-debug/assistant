"""Gradio 简单 UI：输入问题 -> Agent 处理 -> 流式输出到对话界面。

对话历史以 LangChain 消息轨迹保存在 gr.State 中（含数据库查询结果），
每轮作为上下文传入 agent，用于后续问题理解。
"""
import logging

import gradio as gr
from langchain_core.messages import AIMessageChunk, ToolMessage

from main import MAX_HISTORY_MESSAGES, build_agent

logger = logging.getLogger("ui")


def _trim_history(history: list) -> list:
    """保留最近若干条消息，且必须从用户消息开始（工具消息不能作为轨迹开头）。"""
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    while trimmed and getattr(trimmed[0], "type", "") != "human":
        trimmed = trimmed[1:]
    return trimmed


def _chunk_text(chunk) -> str:
    """提取流式 chunk 的文本内容（兼容 str / 内容列表两种形态）。"""
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def respond(question, chat_history, state_history):
    """流式回答生成器：依次产出 (聊天记录, 持久历史, 状态提示)。"""
    question = (question or "").strip()
    if not question:
        yield chat_history, state_history, ""
        return

    history = _trim_history(state_history or [])
    chat_history = list(chat_history or [])
    chat_history.append({"role": "user", "content": question})
    yield chat_history, state_history, "🤔 正在识别意图…"

    agent = build_agent(question)
    inputs = {"messages": [*history, {"role": "user", "content": question}]}

    answer = ""
    final_messages = None
    try:
        # messages 模式流式输出 token；values 模式取最终完整消息轨迹
        for mode, payload in agent.stream(inputs, stream_mode=["messages", "values"]):
            if mode == "values":
                final_messages = payload["messages"]
                continue
            chunk, meta = payload
            if isinstance(chunk, ToolMessage):
                yield chat_history, state_history, f"🔧 工具 {chunk.name} 已完成"
            elif isinstance(chunk, AIMessageChunk):
                # 只流式输出顶层模型节点；工具内部（如 SQL 生成）的 LLM token 不展示
                if meta.get("langgraph_node") != "model":
                    continue
                if chunk.tool_call_chunks:
                    yield chat_history, state_history, "🛠 正在调用工具…"
                    continue
                text = _chunk_text(chunk)
                if text:
                    answer += text
                    yield chat_history, state_history, ""
    except Exception:
        logger.exception("UI 流式回答异常")
        answer = answer or f"抱歉，处理您的问题时出现异常，请稍后重试。"

    if answer:
        chat_history.append({"role": "assistant", "content": answer})
    if final_messages is not None:
        # 持久历史记录本轮完整轨迹（用户消息 + 工具调用与数据库查询结果 + 回答）
        history.extend(final_messages[len(history):])
    yield chat_history, history, ""


with gr.Blocks(title="日本铁路咨询系统") as demo:
    gr.Markdown(
        "## 🚄 日本铁路咨询系统\n"
        "历史知识（RAG）· 统计数据（本地 MySQL）· 行业信息（Tavily）"
    )
    chatbot = gr.Chatbot(height=520, label="客服对话")  # gradio 6 默认 messages 格式
    status = gr.Markdown("")
    state = gr.State([])  # 对话历史消息轨迹（含数据库查询结果）
    with gr.Row():
        question_box = gr.Textbox(
            placeholder="请输入您的问题，例如：2020年JR东日本的旅客总收入是多少？",
            show_label=False,
            autofocus=True,
            scale=9,
        )
        send_btn = gr.Button("发送", variant="primary", scale=1)
    gr.ClearButton([question_box, chatbot, status, state], value="清空对话")

    submit_inputs = [question_box, chatbot, state]
    submit_outputs = [chatbot, state, status]
    question_box.submit(respond, submit_inputs, submit_outputs).then(
        lambda: "", None, question_box
    )
    send_btn.click(respond, submit_inputs, submit_outputs).then(
        lambda: "", None, question_box
    )


if __name__ == "__main__":
    demo.launch()
