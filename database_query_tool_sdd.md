# SDD：铁路知识库 Database Query Tool
添加一个tool : 数据库查询 ，并在意图识别中添加，先从本地数据库中识别，再调用tavily检索行业信息
## 1. 目标

在现有铁路知识库系统中新增一个只读数据库查询 Tool。

用户提出问题后，由 Agent 判断是否需要查询 MySQL。需要查询时：

```text
用户问题
  ↓
Agent 判断
  ↓
Database Query Tool
  ↓
获取真实 MySQL Schema
  ↓
Qwen3.8-Flash
  ↓
生成标准 MySQL SELECT
  ↓
SQL 安全校验
  ↓
MySQL 查询
  ↓
结构化结果
  ↓
Agent
  ↓
用户回答
```

本任务只负责新增数据库查询能力，不修改已经完成的 PDF Parser 和 Excel Parser。

---

## 2. 数据库配置

数据库使用本地 MySQL：

```text
Host: 127.0.0.1
Port: 3306
User: root
Database: tetsudou
```

数据库密码必须只配置在项目本地 `.env` 中，禁止写入代码、SDD、Git 或日志。

`.env`：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<实际数据库密码>
MYSQL_DATABASE=tetsudou

QWEN_MODEL=qwen3.8-flash

MAX_QUERY_ROWS=100
QUERY_TIMEOUT_SECONDS=30

# 可选：限制允许查询的表
ALLOWED_TABLES=
```

`.env.example`：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=tetsudou

QWEN_MODEL=qwen3.8-flash

MAX_QUERY_ROWS=100
QUERY_TIMEOUT_SECONDS=30

ALLOWED_TABLES=
```

`.gitignore` 必须包含：

```gitignore
.env
```

---

## 3. Tool 使用决策

不是所有用户问题都调用 Database Query Tool。

### 应调用

用户问题需要读取 MySQL 中真实铁路数据，例如：


```text
统计2015年至2024年的铁路客流量
```

```text
查询数据库中有哪些铁路公司
```



### 不应调用

例如：

```text
什么是新干线？
```

```text
什么是 MySQL？
```

```text
帮我写一个 Python 函数
```

判断原则：

> 只有当回答问题需要读取本地 `tetsudou` MySQL 数据时，才调用该 Tool。

---

## 4. Tool 接口

推荐：

```python
def database_query(question: str) -> dict:
    """根据自然语言问题查询铁路 MySQL 数据库。"""
```

输入：

```json
{
  "question": "查询2020年东京站的客流量"
}
```

成功：

```json
{
  "success": true,
  "sql": "SELECT ...",
  "columns": ["year", "station_name", "passenger_count"],
  "rows": [
    {
      "year": 2020,
      "station_name": "東京",
      "passenger_count": 123456
    }
  ],
  "row_count": 1
}
```

失败：

```json
{
  "success": false,
  "sql": null,
  "columns": [],
  "rows": [],
  "row_count": 0,
  "error_type": "DATABASE_ERROR",
  "error": "数据库查询失败"
}
```

---

## 5. 内部处理流程

```text
database_query(question)
        │
        ├── 获取数据库 Schema
        │
        ├── 调用 Qwen3.8-Flash
        │
        ├── 生成 SQL
        │
        ├── SQL 安全校验
        │
        ├── 执行 SQL
        │
        └── 格式化结果
```

推荐拆分：

```python
get_database_schema()
generate_sql(question, schema)
validate_sql(sql, schema)
execute_query(sql)
format_query_result(result)
```

不要将所有逻辑写在一个函数中。

---

## 6. 数据库连接

推荐使用：

```text
SQLAlchemy
+
PyMySQL
+
python-dotenv
```

新增：

```text
database.py
```

负责：

1. 读取 `.env`
2. 创建 SQLAlchemy Engine
3. 提供数据库连接
4. 获取数据库 Schema

推荐结构：

```text
database.py
├── load_config()
├── create_engine()
├── get_connection()
└── get_database_schema()
```

不要在 `database_query.py` 中重复实现数据库连接。

---

## 7. 获取真实数据库 Schema

Qwen3.8-Flash 不允许凭模型知识猜测数据库结构。

必须先读取当前 MySQL 的真实 Schema。

可以使用：

```text
information_schema
```

获取：

```text
数据库
表名
字段名
字段类型
主键
唯一键
```

流程：

```text
MySQL
 ↓
information_schema
 ↓
真实 Schema
 ↓
Qwen3.8-Flash
```

如果存在表关系，也应提供给模型。

---

## 8. 允许查询的表

建议支持：

```env
ALLOWED_TABLES=table_a,table_b
```

如果配置为空，可以根据项目实际情况决定是否允许全部业务表。

原则：

> Database Query Tool 不得访问系统配置表、用户表、密码表或其他非业务敏感表。

最终 SQL Validator 必须检查 SQL 实际访问的表。

---

## 9. Qwen3.8-Flash

模型：

```text
qwen3.8-flash
```

模型职责：

```text
用户自然语言
+
真实数据库 Schema
+
SQL 规则
        ↓
MySQL SELECT
```

模型不能：

- 直接访问数据库；
- 直接执行 SQL；
- 修改数据库；
- 绕过 SQL Validator。

---

## 10. SQL Generator Prompt

SQL 生成 Prompt 至少包含：

```text
用户问题
数据库 Schema
允许访问的表
SQL 安全规则
```

核心 Prompt：

```text
你是 MySQL SQL 生成器。

根据用户问题和提供的数据库 Schema，
生成能够回答用户问题的 MySQL 查询。

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
```

---

## 11. SQL 安全校验

模型生成的 SQL 不得直接执行。

必须：

```text
LLM
 ↓
SQL Validator
 ↓
MySQL
```

而不是：

```text
LLM
 ↓
MySQL
```

允许：

```sql
SELECT
```

以及：

```text
WHERE
JOIN
GROUP BY
HAVING
ORDER BY
LIMIT
COUNT
SUM
AVG
MAX
MIN
```

禁止：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
CREATE
TRUNCATE
REPLACE
CALL
SET
GRANT
REVOKE
```

---

## 12. 多语句禁止

以下必须拒绝：

```sql
SELECT * FROM railway_station_statistics;
DELETE FROM railway_station_statistics;
```

不能仅使用：

```python
sql.lower().startswith("select")
```

作为安全检查。

推荐使用 SQL Parser / AST 检查：

1. 是否只有一个 SQL Statement；
2. 根节点是否为 SELECT；
3. 是否存在写操作；
4. 是否访问允许的表；
5. 是否访问其他数据库；
6. 是否包含危险操作。

---

## 13. 查询结果限制

默认：

```env
MAX_QUERY_ROWS=100
```

普通查询默认最多返回 100 行。

对于：

```sql
COUNT(...)
SUM(...)
AVG(...)
MAX(...)
MIN(...)
GROUP BY
```

等聚合查询，不应机械修改 SQL 导致业务语义改变。

---

## 14. 查询超时

默认：

```env
QUERY_TIMEOUT_SECONDS=30
```

超过超时时间：

```text
停止查询
 ↓
返回错误
```

避免低效 SQL 长时间占用数据库连接。

---

## 15. 查询无结果

数据库查询成功但没有记录，不属于数据库异常。

Tool 应返回：

```json
{
  "success": true,
  "columns": [],
  "rows": [],
  "row_count": 0
}
```

上层 Agent 应向用户说明数据库中没有找到符合条件的数据。

不能自行编造数据。

---

## 16. 用户问题不明确

例如：

```text
查询一下铁路数据
```

如果无法确定：

```text
铁路公司
年份
地区
指标
车站
```

则不要让模型随意生成 SQL。

返回：

```json
{
  "success": false,
  "error_type": "NEED_CLARIFICATION",
  "message": "无法确定需要查询的数据范围"
}
```

由 Agent 继续向用户询问。

---

## 17. 数据库异常

数据库连接失败：

```json
{
  "success": false,
  "error_type": "DATABASE_CONNECTION_ERROR",
  "message": "无法连接数据库"
}
```

SQL 执行失败：

```json
{
  "success": false,
  "error_type": "SQL_EXECUTION_ERROR",
  "message": "数据库查询执行失败"
}
```

不得向用户暴露：

```text
数据库密码
完整连接 URL
内部异常堆栈
```

---

## 18. 查询审计日志

建议记录：

```text
query_time
user_question
generated_sql
execution_time
row_count
success
error_type
```

禁止记录：

```text
MYSQL_PASSWORD
完整 DATABASE_URL
```

---

## 19. 推荐项目结构

```text
tetsudou/
├── knowledge/
│   ├── pdf/
│   ├── excel/
│   └── knowledge.txt
│
├── pdf_parser.py
├── excel_parser.py
│
├── database.py
├── database_query.py
├── sql_generator.py
├── sql_validator.py
│
├── .env
├── .env.example
├── .gitignore
│
└── ...
```

### database.py

负责：

```text
环境变量
数据库连接
Schema 获取
```

### sql_generator.py

负责：

```text
Question + Schema
        ↓
Qwen3.8-Flash
        ↓
SQL
```

### sql_validator.py

负责：

```text
SQL
 ↓
安全解析
 ↓
允许 / 拒绝
```

### database_query.py

负责：

```text
Tool 对外入口
 ↓
Schema
 ↓
SQL Generator
 ↓
SQL Validator
 ↓
MySQL
 ↓
结构化结果
```

---

## 20. Agent 与 Tool 职责

### Agent

负责：

```text
理解用户问题
判断是否需要数据库
调用 Tool
理解查询结果
生成最终回答
```

### Database Query Tool

负责：

```text
获取 Schema
生成 SQL
校验 SQL
执行 SQL
返回结果
```

### Qwen3.8-Flash

负责：

```text
自然语言 → SQL
```

### MySQL

负责：

```text
真实数据查询
```

---


### Step 1：Agent 判断

判断需要数据库查询。

### Step 2：获取 Schema

例如：

```text
railway_station_statistics

station_name
railway_company
line_name
year
passenger_count
```

### Step 3：Qwen3.8-Flash 生成 SQL

```sql
SELECT
    year,
    station_name,
    passenger_count
FROM railway_station_statistics
WHERE year = 2020
  AND station_name = '東京'
LIMIT 100
```

### Step 4：SQL Validator

检查：

```text
SELECT？
单语句？
允许表？
无写操作？
```

通过后才执行。

### Step 5：MySQL

执行查询。

### Step 6：返回 Tool

```json
{
  "success": true,
  "columns": [
    "year",
    "station_name",
    "passenger_count"
  ],
  "rows": [
    {
      "year": 2020,
      "station_name": "東京",
      "passenger_count": 123456
    }
  ],
  "row_count": 1
}
```

### Step 7：Agent

Agent 只能根据真实查询结果生成最终回答。

---

## 22. 验收标准

### Tool

- [ ] 可以接收自然语言问题。
- [ ] Agent 可以决定是否调用。
- [ ] 可以获取真实数据库 Schema。
- [ ] 使用 `qwen3.8-flash` 生成 SQL。
- [ ] 可以执行 MySQL 查询。
- [ ] 返回结构化结果。

### SQL 安全

- [ ] 只允许 SELECT。
- [ ] 禁止 INSERT。
- [ ] 禁止 UPDATE。
- [ ] 禁止 DELETE。
- [ ] 禁止 DROP。
- [ ] 禁止 ALTER。
- [ ] 禁止 CREATE。
- [ ] 禁止 TRUNCATE。
- [ ] 禁止 REPLACE。
- [ ] 禁止 CALL。
- [ ] 禁止 SET。
- [ ] 禁止多语句。
- [ ] 禁止访问未授权表。

### 数据安全

- [ ] 数据库密码只存在 `.env`。
- [ ] `.env` 不提交 Git。
- [ ] 日志不记录密码。
- [ ] Tool 不允许修改数据库。

### 查询能力

- [ ] 支持条件查询。
- [ ] 支持多条件查询。
- [ ] 支持 JOIN。
- [ ] 支持 GROUP BY。
- [ ] 支持 COUNT。
- [ ] 支持 SUM。
- [ ] 支持 AVG。
- [ ] 支持 MAX / MIN。
- [ ] 支持 ORDER BY。
- [ ] 支持 LIMIT。
- [ ] 支持中文。
- [ ] 支持日文。
- [ ] 支持 NULL。

### 异常

- [ ] 数据库连接失败可处理。
- [ ] SQL 生成失败可处理。
- [ ] SQL 校验失败可处理。
- [ ] SQL 执行失败可处理。
- [ ] 查询超时可处理。
- [ ] 查询无结果可处理。
- [ ] 用户问题不明确时可以请求澄清。

---



> **Qwen3.8-Flash 只负责将自然语言转换为 SQL；SQL Validator 决定 SQL 是否允许执行；MySQL 只提供真实数据；Database Query Tool 永远保持只读。**
