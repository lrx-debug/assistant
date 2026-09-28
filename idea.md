我要设计一个系统，用create_agent进行搭建

意图识别由提示词的方式进行，比如
"""
你是一个日本铁路咨询进行客户意图识别的专家，对用户问题进行如下分类：
1、铁路发展历史相关
2、铁路数据相关
3、其他问题
“”“

意图识别完成后交给agent处理，其中
from langchain.agent import create_agent
from langchain_openai import ChatOpenAI
from langchain.tools import tool

model=ChatOpenAI (model="deepseek-v4-pro"
     api_key=os.environ.get('DEEPSEEK_API_KEY')
)

agent = create_agent (model,tools=tools)

工具链路有三个
第一个tool :rag 信息检索  根据本地知识库文件进行切块与向量化，切块按照固定大小500个字符进行切分

向量模型调用千问的dashcope

embedding_model = DashScopeEmbeddingModel(
    model="qwen3.7-text-embedding",
    api_key=os.environ.get("DASHSCOPE_API_KEY")
)

向量数据库用chromadb

第二个tool : tavily检索行业信息

第三个tool 正常回复

不做异常处理，只实现最简单的核心流程，用langchain搭建 https://langchain-doc.cn/v1/python/langchain/agents.html


----
添加一个tool : 数据库查询
根据用户问题，查询数据库中的相关数据
（已完成）
----
添加上下文管理，记录用户问题与数据库查询结果，用于后续问题理解

采用对话历史管理即可，例如：history = [
    {"role": "user", "content": "什么是 Python？"},
    {"role": "assistant", "content": "Python 是一种编程语言。"},
    {"role": "user", "content": "它有什么应用？"}
]

Agent 调用模型时，将 history 作为上下文传入，模型就可以根据之前的对话回答问题。

----
添加简单UI界面，用户可以在界面上输入问题，系统会根据问题调用agent进行处理，返回结果。

采用gradio搭建界面，并将流式输出添加到对话中
