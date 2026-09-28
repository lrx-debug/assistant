"""意图识别模块：通过提示词让 LLM 对用户问题进行分类。"""

INTENT_PROMPT = """你是一个日本铁路咨询进行客户意图识别的专家，对用户问题进行如下分类：
1、铁路发展历史相关（铁路起源、新干线历史、国铁民营化历史、公司发展沿革等知识性问题）
2、铁路数据相关（需要查询真实统计数据：某年份客流量/收入、有哪些铁路公司、车辆数、
   雇员人数、走行千米数、资源支出费，以及需要最新行业数据的问题）
3、其他问题

只输出分类编号（1、2或3），不要输出任何其他内容。

用户问题：{question}
"""

INTENT_MAP = {
    "1": "铁路发展历史相关",
    "2": "铁路数据相关",
    "3": "其他问题",
}


def recognize_intent(model, question: str) -> str:
    """识别用户意图，返回编号 '1' / '2' / '3'。"""
    response = model.invoke(INTENT_PROMPT.format(question=question))
    text = response.content.strip()
    for number in ("1", "2", "3"):
        if number in text:
            return number
    return "3"
