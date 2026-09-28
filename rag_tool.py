"""第一个工具：RAG 信息检索。

本地知识库文件 -> 固定 500 字符切块 -> 千问 DashScope 向量化 -> ChromaDB 存储与检索。
"""
import os

from langchain.tools import tool
from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
PERSIST_DIR = os.path.join(BASE_DIR, "chroma_db")

# 固定大小 500 个字符切块，不重叠
text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=0)

# 千问 dashscope 向量模型
embedding_model = DashScopeEmbeddings(model="text-embedding-v4")


def build_vectorstore() -> Chroma:
    """加载 knowledge 目录下的文本，切块后写入 ChromaDB（库为空时只执行一次）。"""
    vectorstore = Chroma(
        collection_name="railway_history",
        embedding_function=embedding_model,
        persist_directory=PERSIST_DIR,
    )
    if vectorstore._collection.count() == 0:
        loader = DirectoryLoader(
            KNOWLEDGE_DIR,
            glob="knowledge.txt",
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8"},
        )
        documents = loader.load()
        chunks = text_splitter.split_documents(documents)
        vectorstore.add_documents(chunks)
    return vectorstore


vectorstore = build_vectorstore()
retriever = vectorstore.as_retriever(search_kwargs={"k": 3})


@tool
def rag_search(query: str) -> str:
    """检索本地知识库中关于日本铁路发展历史的资料，例如铁路起源、新干线开通、国铁民营化等。"""
    documents = retriever.invoke(query)
    return "\n\n".join(doc.page_content for doc in documents)
