"""SQL 生成器：将用户自然语言问题 + 真实数据库 Schema 交给 qwen3.8-flash，
生成标准 MySQL SELECT。模型不接触数据库，也不能执行 SQL。
"""
import os
import re
from typing import List

from langchain_openai import ChatOpenAI

SYSTEM_PROMPT = """你是 MySQL SQL 生成器。

根据用户问题和提供的数据库 Schema，生成能够回答用户问题的 MySQL 查询。

要求：
1. 只能生成 SELECT 查询。
2. 禁止 INSERT。
3. 禁止 UPDATE。
4. 禁止 DELETE。
5. 禁止 DROP。
6. 禁止 ALTER。
7. 禁止 CREATE。
8. 禁止 TRUNCATE。
9. 禁止 REPLACE。
10. 禁止 CALL。
11. 禁止 SET。
12. 禁止 GRANT。
13. 禁止 REVOKE。
14. 禁止执行多条 SQL。
15. 只能访问允许的表。
16. 不允许猜测不存在的表。
17. 不允许猜测不存在的字段。
18. 如果问题无法根据 Schema 确定查询对象，不要猜测。
19. 输出标准 MySQL SQL。
20. 不输出 Markdown 代码块。
21. 不输出解释文字。
22. 不要自行添加 LIMIT，系统会统一限制返回行数。
23. 公司名、线路名、运输局名是日文，WHERE 条件中必须使用 Schema 数据里的日文原名。
24. 非线路行的 line_name、局外行的 region_bureau 是空字符串 ''，不要用 NULL 匹配。
25. 统计「公司/事业者」时用 record_type='operator'；统计「线路」时用 record_type='line'。
26. 若问题缺少必要信息（公司、年份、地区、指标等均无法确定），只输出一行：
    NEED_CLARIFICATION: 后跟需要用户补充的内容（中文）。
"""

USER_TEMPLATE = """【数据库 Schema】
{schema}

【允许访问的表】
{allowed_tables}

【用户问题】
{question}

请输出 MySQL SELECT（或 NEED_CLARIFICATION）："""


class SQLGenerationError(Exception):
    """SQL 生成阶段失败。"""


class AmbiguousQuestion(Exception):
    """用户问题不明确，需要澄清。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _get_model() -> ChatOpenAI:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise SQLGenerationError("缺少 DASHSCOPE_API_KEY 环境变量")
    return ChatOpenAI(
        model=os.environ.get("QWEN_MODEL", "qwen3.8-flash"),
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=0,
        timeout=30,
    )


def _strip_code_fence(text: str) -> str:
    """模型若违反要求输出 Markdown 代码块，做防御性剥离。"""
    text = text.strip()
    match = re.fullmatch(r"```(?:sql|mysql)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def generate_sql(question: str, schema: str, allowed_tables: List[str]) -> str:
    """根据问题和真实 Schema 生成 MySQL SELECT 语句。

    raises: AmbiguousQuestion（需澄清）/ SQLGenerationError（生成失败）。
    """
    model = _get_model()
    response = model.invoke(SYSTEM_PROMPT + "\n\n" + USER_TEMPLATE.format(
        schema=schema,
        allowed_tables=", ".join(allowed_tables),
        question=question,
    ))
    output = _strip_code_fence(response.content)

    if output.upper().startswith("NEED_CLARIFICATION"):
        reason = output.split(":", 1)[1].strip() if ":" in output else "无法确定需要查询的数据范围"
        raise AmbiguousQuestion(reason or "无法确定需要查询的数据范围")
    if not output:
        raise SQLGenerationError("模型未返回 SQL")
    return output
