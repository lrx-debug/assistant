# 环境依赖说明（Readme1）

本文档说明日本铁路咨询系统（tetsudou）运行所需的全部环境依赖，包括运行环境、第三方库、外部服务与数据初始化步骤。

---

## 1. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11（已在 Windows 环境验证；Linux/macOS 理论兼容） |
| Python | 3.14+（本项目虚拟环境实测版本：3.14.7） |
| MySQL | 5.7+ / 8.0（需支持 utf8mb4、CTE、MAX_EXECUTION_TIME 优化） |
| 网络 | 需访问 DeepSeek / DashScope（阿里云百炼）/ Tavily 三个外部 API |
| 磁盘 | 建议 ≥ 2 GB（向量库、虚拟环境、知识库文件） |

## 2. 第三方库依赖

完整清单见 [requirements.txt](requirements.txt)，关键包及实测版本：

### 2.1 LangChain 生态

| 包 | 实测版本 | 用途 |
|------|---------|------|
| langchain | 1.4.2 | `create_agent` Agent 编排、`@tool` 工具定义 |
| langchain-openai | - | `ChatOpenAI`（DeepSeek OpenAI 兼容接口） |
| langchain-community | - | `TavilySearchResults` 联网检索、`DashScopeEmbeddings` 向量化 |
| langchain-text-splitters | - | `RecursiveCharacterTextSplitter` 500 字符切块 |
| langchain-chroma | - | ChromaDB 向量库封装 |

### 2.2 向量检索

| 包 | 实测版本 | 用途 |
|------|---------|------|
| chromadb | 1.5.9 | 向量数据库持久化存储（`chroma_db/` 目录，1,618 个文本块） |
| dashscope | - | 千问 Embedding API（模型 `text-embedding-v4`） |

### 2.3 数据库访问与 SQL 安全

| 包 | 实测版本 | 用途 |
|------|---------|------|
| SQLAlchemy | 2.1.0 | 数据库连接层（Engine、连接池 `pool_pre_ping`） |
| PyMySQL | 1.2.3 | MySQL 驱动 |
| sqlglot | 30.19.0 | SQL AST 解析与安全校验（单语句/只读/表白名单/危险函数拦截） |

### 2.4 UI 与数据预处理

| 包 | 实测版本 | 用途 |
|------|---------|------|
| gradio | 6.28.0 | Web 对话界面（流式输出，Gradio 6 默认 messages 格式） |
| unstructured / beautifulsoup4 / lxml | - | PDF 文本抽取 |
| pandas / openpyxl | - | Excel 读取与清洗 |
| tavily-python | - | Tavily 联网搜索 API |
| python-dotenv | - | 加载 `.env` 环境变量 |

### 2.5 安装

```powershell
cd tetsudou
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

## 3. 模型依赖（外部 API）

| 用途 | 模型 | 提供方 | 配置位置 |
|------|------|--------|---------|
| Agent 对话 / 意图识别 | `deepseek-v4-pro` | DeepSeek（base_url: `https://api.deepseek.com`） | main.py |
| RAG 向量化 | `text-embedding-v4` | DashScope | rag_tool.py |
| 自然语言 → SQL | `qwen3.8-flash` | DashScope | sql_generator.py |

> 注意：DeepSeek 必须显式指定 `base_url="https://api.deepseek.com"`，否则会走 OpenAI 默认端点报错；Embedding 模型应使用 `text-embedding-v4`。

## 4. 环境变量配置

复制 [.env.example](.env.example) 为 `.env` 并填写密钥（`.env` 已被 .gitignore 排除，禁止提交）：

```ini
# ---- API 密钥（必填）----
DEEPSEEK_API_KEY=          # DeepSeek 平台申请
DASHSCOPE_API_KEY=         # 阿里云百炼平台申请（Embedding + SQL生成）
TAVILY_API_KEY=            # tavily.com 申请（联网搜索）

# ---- MySQL 连接（必填密码）----
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=            # 必填，缺失将报 Access denied
MYSQL_DATABASE=railway

# ---- 查询行为（可选）----
QWEN_MODEL=qwen3.8-flash   # SQL 生成模型
MAX_QUERY_ROWS=100         # 查询结果行数上限
QUERY_TIMEOUT_SECONDS=30   # 语句超时（秒）
ALLOWED_TABLES=            # 留空使用默认 6 张业务表白名单
```

## 5. 数据初始化

系统运行依赖三份数据资产，按顺序构建：

| 步骤 | 命令 | 产物 | 说明 |
|------|------|------|------|
| ① PDF 解析 | `python pdf_parser.py` | `knowledge/knowledge.txt`（约 60 万字符） | 源文件在 `knowledge/pdf/`（17 篇文献） |
| ② Excel 入库 | `python excel_parser.py` | MySQL 6 张业务表（共 19,333 条） | 源文件在 `knowledge/excel/`（60 个报表，2012–2021）；幂等导入，可重复执行 |
| ③ 向量库构建 | 自动（首次运行 main.py/ui.py 时） | `chroma_db/`（1,618 个块） | 若目录已存在且非空则直接复用；**重建需先删除 chroma_db 目录** |

验证数据库是否就绪：

```powershell
.\.venv\Scripts\python.exe -X utf8 -c "
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from database import get_engine
tables = ['railway_passenger_revenue','railway_passenger_volume','railway_energy_expense','railway_running_distance','railway_rolling_stock','railway_staff_wages']
with get_engine().connect() as con:
    total = 0
    for t in tables:
        n = con.execute(text('SELECT COUNT(*) FROM ' + t)).scalar()
        total += n
        print(f'{t}: {n}')
    print('TOTAL:', total)
"
```

预期输出 `TOTAL: 19333`。

## 6. 启动方式

| 模式 | 命令 | 地址 |
|------|------|------|
| 命令行对话 | `.\.venv\Scripts\python.exe -X utf8 main.py` | 终端交互（exit 退出，clear 清空历史） |
| Web 界面 | `.\.venv\Scripts\python.exe -X utf8 ui.py` | http://127.0.0.1:7860 |

> `-X utf8` 用于 Windows 控制台中文输出防乱码。

## 7. 常见环境问题

| 现象 | 原因与解决 |
|------|-----------|
| `Access denied for user 'root'@'localhost' (using password: NO)` | 未配置 `MYSQL_PASSWORD`，或修改 `.env` 后未保存 |
| `401 InvalidApiKey` | `.env` 中密钥未保存生效，或密钥失效；重新保存后重启进程 |
| 向量库仍是旧数据 | ChromaDB 目录非空不会重建；删除 `chroma_db/` 后重新运行 |
| Gradio 报 `Chatbot.__init__() got an unexpected keyword argument 'type'` | Gradio 6 已默认 messages 格式，移除 `gr.Chatbot(type=...)` 参数 |
| SQL 语法错误 `near ';) AS _q LIMIT 100'` | 生成 SQL 带尾分号；已由代码去除，若复现检查 sql_generator 输出 |
| 控制台中文乱码 | 启动命令加 `-X utf8` |
| langchain-community 弃用告警 | `TavilySearchResults` 等存在 DeprecationWarning，不影响当前功能 |

## 8. 依赖关系速查

```
main.py ──► intent.py ──────────► DeepSeek API (deepseek-v4-pro)
        ──► rag_tool.py ────────► DashScope API (text-embedding-v4)
        │                    ──► chromadb（本地 chroma_db/）
        ──► database_query.py ──► sql_generator.py ─► DashScope API (qwen3.8-flash)
        │                    ──► sql_validator.py ─► sqlglot（本地 AST 校验）
        │                    ──► database.py ──────► MySQL (pymysql + SQLAlchemy)
        ──► tavily_search ─────► Tavily API
ui.py ──► main.py（chat + 流式） ──► gradio（本地 Web 服务 :7860）
```
