"""日本铁路咨询系统：提示词意图识别 -> create_agent -> 按意图调用对应工具。"""
import os

from dotenv import load_dotenv

load_dotenv()  # 必须在导入 rag_tool 之前执行，rag_tool 导入时即读取环境变量

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults

from intent import INTENT_MAP, recognize_intent
from rag_tool import rag_search
from database_query import database_query

# DeepSeek（OpenAI 兼容接口）
model = ChatOpenAI(
    model="deepseek-v4-pro",
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

# 第二个工具：Tavily 检索行业信息
tavily_search = TavilySearchResults(max_results=5)


# 第三个工具：正常回复
@tool
def normal_reply(query: str) -> str:
    """当问题属于"其他问题"、与日本铁路历史和数据无关时，不做检索，用通用知识直接正常回复。"""
    return model.invoke(query).content


# 工具链路：RAG 历史检索 / 本地数据库查询 / Tavily 行业信息 / 正常回复
tools = [rag_search, database_query, tavily_search, normal_reply]

SYSTEM_PROMPT = """你是日本铁路咨询客服。系统已完成用户意图识别，请严格按照意图选择工具：
- 意图1「铁路发展历史相关」：调用 rag_search 检索本地知识库，依据检索结果回答。
- 意图2「铁路数据相关」：
  1. 先调用 database_query 查询本地 MySQL 真实统计数据（覆盖2012-2021年），
     依据返回结果回答，不得编造或修改数据；
  2. 若对话历史中已有公司、年份、指标等信息，调用 database_query 时必须把
     question 参数补全为不依赖上下文即可理解的完整独立问题；
  3. 若返回 NEED_CLARIFICATION，请向用户追问需要补充的信息；
  4. 仅当本地数据库无数据（row_count=0）、查询失败，或用户明确要求最新信息时，
     再调用 tavily_search 检索最新行业信息。
  回答时直接给出数据和结论，不要在回答中展示 SQL 语句。
- 意图3「其他问题」：调用 normal_reply 直接正常回复。

本次识别结果：意图{intent}（{intent_name}）。请使用中文回答。"""


# 对话历史上限：保留最近 N 条消息，防止上下文无限增长
MAX_HISTORY_MESSAGES = 30


def _trim_history(history: list) -> list:
    """保留最近若干条消息，且必须从用户消息开始（工具消息不能作为轨迹开头）。"""
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    while trimmed and getattr(trimmed[0], "type", "") != "human":
        trimmed = trimmed[1:]
    return trimmed


def build_agent(question: str):
    # 第一步：提示词方式做意图识别；第二步：按意图构建 agent
    intent = recognize_intent(model, question)
    return create_agent(
        model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT.format(
            intent=intent, intent_name=INTENT_MAP[intent]
        ),
    )


def chat(question: str, history: list | None = None) -> str:
    """单轮问答。history 为 LangChain 消息轨迹列表（记录用户问题、数据库查询
    结果等完整上下文），作为上下文传入 agent；结束后本轮轨迹会追加进 history。"""
    own_history = [] if history is None else _trim_history(history)
    agent = build_agent(question)
    inputs = {"messages": [*own_history, {"role": "user", "content": question}]}
    result = agent.invoke(inputs)
    if history is not None:
        history.extend(result["messages"][len(own_history):])
    for m in result["messages"]:
        for tc in getattr(m, "tool_calls", []) or []:
            print("[工具调用]", tc["name"], tc["args"])
    return result["messages"][-1].content


if __name__ == "__main__":
    print("日本铁路咨询系统（输入 exit 退出，clear 清空对话历史）")
    history: list = []
    while True:
        question = input("用户：").strip()
        if question in ("exit", "quit", "退出"):
            break
        if question in ("clear", "清空"):
            history.clear()
            print("（对话历史已清空）")
            continue
        print("客服：", chat(question, history))
