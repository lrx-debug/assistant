# SDD：铁路知识库数据预处理与清洗

## 1. 文档目的

本文档定义铁路知识库的两项离线数据预处理任务：

1. 将 `knowledge/pdf` 目录下的 PDF 文档解析为统一的 `knowledge.txt`。
2. 将 `knowledge/excel` 目录下的铁路相关 Excel 数据文件读取、清洗、标准化，并写入 MySQL，形成可供后续应用查询的数据集。

本设计关注**数据处理流程、数据契约、清洗规则、异常处理和验收标准**，不规定具体函数实现方式。IDE/Coding Agent 应依据本文档完成代码实现。

---

## 2. 项目目录约定

项目根目录：

```text
C:\Users\lrx\learngit\learngit\tetsudou
```

建议目录结构：

```text
tetsudou/
├── knowledge/
│   ├── pdf/
│   │   ├── *.pdf
│   │   └── ...
│   ├── excel/
│   │   ├── *.xlsx
│   │   ├── *.xls
│   │   └── ...
│   └── knowledge.txt
├── pdf_parser.py
├── excel_parser.py
└── ...
```

> 注意：需求描述中后文出现了 `knowledge\exc`。本 SDD 统一将 Excel 输入目录定义为 `knowledge/excel`；如果实际项目目录确实为 `knowledge/exc`，应通过配置项修改，而不是在代码中散落硬编码路径。

---

# 3. 总体处理架构

```text
                         ┌────────────────────┐
                         │ knowledge/pdf      │
                         │ PDF 文件集合        │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   pdf_parser.py    │
                         │ PDF 解析 / 清洗     │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │ knowledge.txt      │
                         │ 统一文本知识文件    │
                         └────────────────────┘


                         ┌────────────────────┐
                         │ knowledge/excel    │
                         │ Excel 文件集合      │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │  excel_parser.py   │
                         │ 读取 / 清洗 / 标准化 │
                         └─────────┬──────────┘
                                   │
                         ┌─────────┴─────────┐
                         ▼                   ▼
                ┌─────────────────┐  ┌─────────────────┐
                │ 数据质量校验      │  │ 数据来源元信息   │
                └────────┬────────┘  └────────┬────────┘
                         └─────────┬──────────┘
                                   ▼
                         ┌────────────────────┐
                         │       MySQL        │
                         │ 结构化铁路数据      │
                         └────────────────────┘
```

---

# 4. 任务一：PDF 转 TXT

## 4.1 目标

使用 `unstructured` 库提供的 `partition_pdf` 对 `knowledge/pdf` 下的全部 PDF 文件进行解析。

解析目标包括：

- 正文文字
- 标题
- 段落
- 表格
- 页码等可识别的文档结构信息
- 中文
- 日语
- 英语

最终生成：

```text
knowledge/knowledge.txt
```

该文件作为后续知识库文本处理的统一输入。

---

## 4.2 核心技术要求

必须使用：

```python
from unstructured.partition.pdf import partition_pdf
```

调用 `partition_pdf` 完成 PDF 内容解析。

PDF 中包含表格，因此不能只采用简单文本抽取模式。

优先使用能够进行版面分析和表格结构识别的解析策略，例如：

```text
strategy = "hi_res"
infer_table_structure = True
```

具体参数应根据当前安装的 `unstructured` 版本进行适配。

对于扫描型 PDF 或无法直接提取文字的页面，应保留 OCR 能力的扩展点；不要假设所有 PDF 都是原生可复制文本。

---

## 4.3 PDF 文件发现规则

`pdf_parser.py` 启动后：

1. 定位项目根目录。
2. 定位 `knowledge/pdf`。
3. 遍历目录中的 `.pdf` 文件。
4. 文件处理顺序必须稳定，建议按文件名排序。
5. 单个文件处理失败时：
   - 记录错误日志；
   - 不应导致其他 PDF 无法继续处理；
   - 最终汇总失败文件列表；
   - 如果存在失败文件，程序退出时应明确提示。

不处理：

```text
.tmp
.lock
隐藏临时文件
非 PDF 文件
```

---

## 4.4 PDF 内容统一格式

最终 `knowledge.txt` 必须能够区分不同来源 PDF。

建议采用：

```text
==================================================
SOURCE_FILE: example.pdf
==================================================

[PAGE 1]

标题……

正文……

[TABLE]

列1 | 列2 | 列3
值1 | 值2 | 值3

[PAGE 2]

……
```

实际实现可以根据 `unstructured` 返回的 element 类型调整，但必须满足：

- 不同 PDF 之间有明确分隔；
- 不同页面之间有明确边界；
- 表格与普通正文可以区分；
- 原始文字顺序尽可能保持；
- 不人为改写原文语义。

---

## 4.5 表格处理规则

表格是 PDF 解析的重点。

对于 `Table` 类型元素：

1. 尽可能保留原始行列关系。
2. 不要把整个表格简单拼成一段连续文本。
3. 推荐转换为 Markdown Table 或稳定的 `列 | 列 | 列` 格式。
4. 空单元格保留为空，不要随意删除列。
5. 单元格中的换行可转换为空格或 `<br>`，但不得丢失文字。
6. 表格标题如果能够识别，应与表格关联保存。
7. 无法可靠识别结构的表格，应保留原始文本内容，并在日志中记录解析质量问题。

示例：

```text
[TABLE]
车站 | 乗車人員 | 降車人員
東京 | 10000 | 9800
新宿 | 12000 | 11500
```

---

## 4.6 多语言处理

PDF 可能包含：

- 中文
- 日本語
- English

处理要求：

- 输出统一使用 UTF-8；
- 禁止 ASCII-only 清洗；
- 不删除日文汉字、平假名、片假名；
- 不删除中文字符；
- 不将英文数字内容进行无依据翻译；
- 保留原文；
- 不进行自动语言翻译。

特别注意：

```text
日本語の駅名
中文铁路名称
English railway terminology
```

均应原样保留。

---

## 4.7 文本清洗规则

允许进行结构性清洗：

- 去除明显的空白行；
- 统一 Windows/Linux 换行；
- 清理连续无意义空格；
- 去除 PDF 解析产生的明显控制字符；
- 对页面级内容进行合理分隔。

禁止：

- 自行总结；
- 自行翻译；
- 修改数字；
- 修改日期；
- 修改单位；
- 删除看似重复但无法确认是重复的业务内容；
- 根据语义自行纠正原始数据。

原则：

> **PDF Parser 负责“解析和格式标准化”，不负责“知识理解”。**

---

## 4.8 输出策略

默认：

```text
输入：
knowledge/pdf/*.pdf

输出：
knowledge/knowledge.txt
```

建议采用**一次性重新生成**策略，而不是不断 append。

即每次运行：

```text
删除/覆盖旧 knowledge.txt
    ↓
重新扫描全部 PDF
    ↓
重新生成 knowledge.txt
```

这样可以避免：

- 重复运行导致重复数据；
- PDF 删除后旧内容仍残留；
- PDF 内容发生变化后 TXT 不同步。

---

## 4.9 PDF Parser 验收标准

### 正常场景

- [ ] 能扫描 `knowledge/pdf` 下全部 PDF。
- [ ] 中文 PDF 能正常解析。
- [ ] 日语 PDF 能正常解析。
- [ ] 英文 PDF 能正常解析。
- [ ] 正文文字能够写入 TXT。
- [ ] 表格能够被识别并保留基本行列关系。
- [ ] 多个 PDF 能合并到一个 `knowledge.txt`。
- [ ] 不同 PDF 之间有来源标识。
- [ ] UTF-8 输出无乱码。
- [ ] 重复执行不会产生重复内容。

### 异常场景

- [ ] PDF 目录不存在时给出明确错误。
- [ ] PDF 目录为空时给出明确提示。
- [ ] 单个 PDF 损坏时不会阻止其他文件处理。
- [ ] 解析失败的 PDF 会被记录。
- [ ] 最终输出能够明确知道哪些文件成功、哪些文件失败。

---

# 5. 任务二：Excel 转 MySQL 结构化数据

## 5.1 目标

将：

```text
knowledge/excel/
```

目录下多个 Excel 文件中的日本铁路相关历史数据进行：

```text
Excel
 ↓
读取
 ↓
Sheet 发现
 ↓
原始数据标准化
 ↓
字段清洗
 ↓
类型转换
 ↓
缺失值处理
 ↓
重复数据处理
 ↓
数据质量校验
 ↓
结构化数据
 ↓
MySQL
```

最终形成能够被后续应用稳定查询的数据表。

---

# 6. Excel 解析技术方案

## 6.1 推荐技术栈

推荐：

```text
Python
├── pandas
├── SQLAlchemy
└── PyMySQL
```

Excel：

```python
pandas.read_excel()
```

数据库：

```text
SQLAlchemy + PyMySQL
```

Pandas 的 `read_excel` 支持读取多个 Excel 格式，并且可以通过 `sheet_name=None` 一次获取工作簿中的全部 Sheet。citeturn0search0turn0search1

SQLAlchemy 可使用类似：

```text
mysql+pymysql://username:password@host/database
```

的连接 URL 连接 MySQL。citeturn0search12turn0search15

---

# 7. Excel 文件发现

`excel_parser.py` 启动后：

1. 扫描 `knowledge/excel`。
2. 支持：
   - `.xlsx`
   - `.xls`
   - `.xlsm`
3. 文件名排序后依次处理。
4. 每个 Excel 文件读取所有 Sheet。
5. 忽略明显为空的 Sheet。
6. 对每个 Sheet 单独执行数据分析和清洗。

不要默认所有 Excel 都只有一个 Sheet。

---

# 8. Excel 数据解析原则

铁路数据来源可能具有不同的表格格式，因此：

> **禁止假设所有 Excel 文件拥有完全相同的列名和表头结构。**

处理流程应分为：

```text
文件级识别
    ↓
Sheet 级识别
    ↓
表头识别
    ↓
字段标准化
    ↓
数据类型标准化
    ↓
业务字段映射
    ↓
数据质量校验
    ↓
数据库写入
```

---

# 9. Excel 表头处理

Excel 常见问题：

```text
第一行为空
第二行才是真正表头

或者：

第一行是标题
第二行是单位
第三行才是字段
```

因此不能无条件：

```python
pd.read_excel(file)
```

然后认为第一行就是数据库字段。

Parser 应具备以下能力：

1. 读取 Sheet 原始内容；
2. 判断有效表头所在行；
3. 清除纯标题行；
4. 清除纯单位行；
5. 处理多级表头；
6. 最终生成稳定的单层字段名。

如果无法可靠判断表头，应：

- 输出警告；
- 保留原始 Sheet；
- 不应静默写入错误字段。

---

# 10. 字段标准化

数据库字段不能直接完全依赖 Excel 原始列名。

例如：

```text
駅名
駅 名
駅名（名称）
Station Name
station_name
```

应经过字段映射后统一成明确的数据库字段。

建议采用：

```text
英文 snake_case
```

例如：

```text
station_name
railway_company
line_name
prefecture
city
year
month
passenger_count
distance_km
fare
```

但：

> **以上字段仅作为命名示例，不代表当前 Excel 一定存在这些字段。**

IDE 在实现时必须首先根据实际 Excel 文件识别字段，再建立字段映射配置。

---

# 11. 原始字段与标准字段映射

推荐不要将字段映射逻辑全部硬编码在主流程中。

建议设计：

```python
COLUMN_MAPPING = {
    "Excel原始列名": "standard_column_name"
}
```

对于日语字段，也允许：

```python
COLUMN_MAPPING = {
    "駅名": "station_name",
    "鉄道会社": "railway_company",
}
```

字段映射必须集中管理。

这样未来新增 Excel 文件时，只需要增加映射，而不需要重写整个 ETL 流程。

---

# 12. 数据清洗规则

## 12.1 字符串

对字符串字段：

- 去除首尾空格；
- 统一明显的全角/半角问题；
- 不随意改变日语、中文、英文内容；
- 空字符串转换为 NULL；
- 不应将 `"0"` 转换成 NULL；
- 不应将 `"N/A"`、`"-"` 等业务值直接当作 0。

---

## 12.2 数字

数字字段需要：

- 去除千分位；
- 处理字符串形式数字；
- 转换为合理数值类型；
- 非法数字转换失败时记录异常；
- 不允许静默将非法数据转换成 0。

例如：

```text
10,000 → 10000
1,234.5 → 1234.5
```

---

## 12.3 日期

日期字段统一成数据库可识别的标准格式。

例如：

```text
YYYY-MM-DD
```

如果原始数据只有年份：

```text
2019
```

不要伪造：

```text
2019-01-01
```

除非业务定义明确规定如此。

年份数据应保留为：

```text
year = 2019
```

---

## 12.4 缺失值

缺失值统一使用：

```text
NULL
```

而不是：

```text
""
"N/A"
"null"
"-"
0
```

但如果原始值本身就是业务意义上的 0，则必须保留：

```text
0 ≠ NULL
```

---

# 13. 重复数据处理

重复数据必须区分：

### 完全重复

例如：

```text
station | year | passengers
Tokyo   | 2020 | 1000
Tokyo   | 2020 | 1000
```

可以去重。

### 业务键重复但数值不同

例如：

```text
station | year | passengers
Tokyo   | 2020 | 1000
Tokyo   | 2020 | 1200
```

不能直接删除其中一条。

此时必须：

- 记录冲突；
- 尝试判断来源；
- 无法判断时保留冲突记录或阻止该 Sheet 自动入库；
- 不允许静默覆盖。

---

# 14. 推荐的数据表设计

由于当前未读取实际 Excel 文件，因此不应凭空确定最终业务表字段。

推荐采用：

## 14.1 数据来源表

```text
data_source
```

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT | 主键 |
| source_file | VARCHAR(512) | Excel 文件名 |
| source_sheet | VARCHAR(255) | Sheet 名称 |
| source_row | INT | 原始行号 |
| imported_at | DATETIME | 导入时间 |
| row_hash | CHAR(64) | 原始数据行哈希 |

用途：

- 数据溯源；
- 问题排查；
- 重复导入控制；
- 追踪原始 Excel 数据来源。

---

## 14.2 业务数据表

业务表根据实际 Excel 内容确定。

例如，如果实际数据属于车站年度统计，可以形成：

```text
railway_station_statistics
```

示意：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT | 主键 |
| station_name | VARCHAR(255) | 车站名称 |
| railway_company | VARCHAR(255) | 铁路公司 |
| line_name | VARCHAR(255) | 线路 |
| year | INT | 年份 |
| passenger_count | BIGINT | 客流量 |
| source_id | BIGINT | 数据来源 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

**注意：这只是候选结构，不得直接当成最终 Schema。**

最终 Schema 必须基于实际 Excel 文件内容确定。

---

# 15. 为什么不能直接把所有 Excel 原样塞进 MySQL

不推荐：

```text
一个 Excel → 一个 MySQL 表
```

也不推荐：

```text
所有 Excel → 一个巨大表
```

原因：

- Excel Sheet 结构可能不同；
- 同一业务字段可能存在不同名称；
- 数据类型可能不一致；
- 后续查询难以维护；
- 重复数据无法控制；
- 数据来源难以追踪。

推荐：

```text
原始来源信息
+
标准化业务数据
+
必要的数据质量信息
```

---

# 16. 数据库连接配置

数据库配置禁止硬编码账号密码。

建议通过环境变量：

```text
MYSQL_HOST
MYSQL_PORT
MYSQL_USER
MYSQL_PASSWORD
MYSQL_DATABASE
```

或者：

```text
MYSQL_URL
```

示例：

```text
mysql+pymysql://user:password@localhost:3306/railway
```

代码中不得出现：

```python
password = "123456"
```

等明文密码。

---

# 17. MySQL 字符集

由于铁路数据包含：

- 中文
- 日文
- 英文

数据库和连接应使用：

```text
utf8mb4
```

确保 Unicode 数据不会因为字符集限制出现乱码。

---

# 18. 数据写入策略

`excel_parser.py` 应支持安全重复执行。

推荐：

```text
读取
 ↓
清洗
 ↓
生成业务唯一键 / row_hash
 ↓
写入
 ↓
重复数据检测
```

对于具有明确业务唯一键的数据，可以使用：

```text
INSERT ... ON DUPLICATE KEY UPDATE
```

实现幂等导入。MySQL 官方文档定义了该语句在 PRIMARY KEY 或 UNIQUE KEY 冲突时执行更新的行为。citeturn0search3

不要默认使用：

```text
REPLACE
```

因为 MySQL 的 `REPLACE` 在重复键场景下具有删除旧行后重新插入的语义，可能影响关联关系或审计字段。citeturn0search10

---

# 19. 事务策略

建议：

```text
一个 Excel 文件
    ↓
多个 Sheet
    ↓
数据清洗
    ↓
数据质量检查
    ↓
事务写入
```

对于关键业务数据：

- 数据质量检查失败时不要提交错误数据；
- 数据库写入失败时应回滚当前事务；
- 不应出现“半个 Sheet 成功、半个 Sheet 失败但程序报告成功”的情况。

对于超大数据集，可以采用分批提交，但必须明确批次边界并记录处理进度。

---

# 20. 数据质量报告

`excel_parser.py` 每次执行后应输出处理摘要。

建议：

```text
==================================================
Excel Import Summary
==================================================

Files discovered: 5
Files processed: 5
Files failed: 0

Sheets discovered: 18
Sheets processed: 17
Empty sheets: 1

Rows read: 125430
Rows inserted: 120000
Rows updated: 5000
Rows skipped: 430

Validation errors: 12
Duplicate rows: 430

Failed files:
  none

==================================================
```

如果出现异常，应明确指出：

```text
文件
Sheet
Excel 行号
字段
原始值
错误原因
```

---

# 21. 日志要求

推荐日志至少包含：

```text
INFO
WARNING
ERROR
```

例如：

```text
INFO  Reading Excel: xxx.xlsx
INFO  Sheet detected: Sheet1
INFO  Rows loaded: 12000
WARNING Empty value in column: station_name
WARNING Invalid number: passenger_count=abc
ERROR Database insert failed
```

禁止仅输出：

```text
something went wrong
```

必须提供定位信息。

---

# 22. 异常处理

## 文件异常

包括：

- 文件不存在；
- 文件损坏；
- Excel 格式无法识别；
- 文件被其他程序占用。

处理：

```text
记录错误
跳过当前文件
继续处理其他文件
```

---

## Sheet 异常

包括：

- Sheet 为空；
- 表头无法识别；
- 字段重复；
- 数据格式异常。

处理：

```text
记录 Sheet
记录原因
根据严重程度决定跳过 Sheet 或终止整个文件
```

---

## 数据异常

包括：

- 非法数字；
- 非法日期；
- 关键字段为空；
- 重复业务键；
- 类型不一致。

原则：

> 数据异常不能通过“默默转换成默认值”来隐藏。

---

## 数据库异常

包括：

- 无法连接；
- 数据库不存在；
- 表不存在；
- 字段不匹配；
- 唯一键冲突；
- 权限不足。

必须：

- 输出明确错误；
- 回滚当前事务；
- 保留失败文件 / Sheet 信息；
- 不报告为成功。

---

# 23. 幂等性

多次执行：

```bash
python excel_parser.py
```

不应产生：

```text
数据 × 2
数据 × 3
数据 × 4
```

因此必须设计稳定的数据唯一标识。

优先：

```text
业务唯一键
```

没有业务唯一键时：

```text
source_file
+
source_sheet
+
source_row
+
row_hash
```

可以作为数据来源层的幂等依据。

---

# 24. Excel Parser 推荐内部模块划分

虽然最终只要求：

```text
excel_parser.py
```

但代码结构应避免所有逻辑集中在一个巨大函数中。

推荐逻辑层次：

```text
excel_parser.py
│
├── discover_excel_files()
│
├── load_workbook()
│
├── detect_header()
│
├── normalize_columns()
│
├── clean_dataframe()
│
├── validate_dataframe()
│
├── generate_row_hash()
│
├── prepare_database_records()
│
├── write_to_mysql()
│
└── generate_import_report()
```

函数名只是建议，IDE 可以根据项目实际结构调整。

---

# 25. PDF Parser 推荐内部模块划分

推荐：

```text
pdf_parser.py
│
├── discover_pdf_files()
│
├── parse_pdf()
│
├── normalize_element()
│
├── format_table()
│
├── format_text()
│
├── write_knowledge_txt()
│
└── generate_parse_report()
```

---

# 26. 配置原则

路径、数据库连接等外部配置不要散落在代码中。

建议集中：

```python
PDF_INPUT_DIR
PDF_OUTPUT_FILE
EXCEL_INPUT_DIR

MYSQL_HOST
MYSQL_PORT
MYSQL_USER
MYSQL_PASSWORD
MYSQL_DATABASE
```

路径应使用：

```python
pathlib.Path
```

而不是大量手工拼接：

```python
"C:\\Users\\..."
```

这样可以降低 Windows 路径转义问题，并提高后续跨平台能力。

---

# 27. 依赖建议

项目至少需要：

```text
unstructured
pandas
SQLAlchemy
PyMySQL
openpyxl
```

如果实际 PDF 解析策略需要 OCR / 高分辨率版面分析，还需要按照当前 `unstructured` 版本补充其对应的 PDF/OCR 依赖。

不要在 SDD 中锁死未经验证的第三方依赖版本；IDE 实现时应先检查当前项目已有 `requirements.txt` / `pyproject.toml`，尽量复用现有依赖。

---

# 28. 执行方式

建议支持：

```bash
python pdf_parser.py
```

执行：

```text
knowledge/pdf/*.pdf
        ↓
knowledge/knowledge.txt
```

以及：

```bash
python excel_parser.py
```

执行：

```text
knowledge/excel/*.xlsx
        ↓
数据清洗
        ↓
MySQL
```

---

# 29. 数据处理原则

整个数据预处理模块必须遵循：

### 原则一：不修改原始语义

Parser 可以：

```text
清洗格式
统一类型
标准化字段
```

不能：

```text
猜测业务含义
修改原始数据
自动纠错业务数据
翻译原文
```

### 原则二：可追溯

数据库中的业务数据必须尽可能能够追溯到：

```text
原始 Excel
→ Sheet
→ 行号
```

PDF 输出也必须能够追溯到：

```text
PDF 文件
→ 页面
→ 内容
```

### 原则三：可重复执行

重复执行不会造成：

```text
重复 TXT
重复数据库记录
```

### 原则四：失败可定位

所有失败都应该能够定位到：

```text
文件
→ Sheet / Page
→ Row / Element
→ Field
```

---

# 30. 实现顺序

IDE/Coding Agent 应按以下顺序实现，不建议两个 Parser 同时复杂化。

## Phase 1：PDF Parser

1. 扫描 PDF；
2. 使用 `partition_pdf`；
3. 处理 Element；
4. 处理 Table；
5. 生成 `knowledge.txt`；
6. 增加错误日志；
7. 增加解析统计。

## Phase 2：Excel 探查

在真正设计最终 MySQL Schema 前：

1. 扫描所有 Excel；
2. 输出文件列表；
3. 输出 Sheet 列表；
4. 输出每个 Sheet 的行列数；
5. 输出前 N 行；
6. 输出候选字段；
7. 识别不同 Excel 的结构差异。

**这一步非常重要。**

不能在没有查看真实 Excel 数据结构的情况下直接创建最终业务表。

## Phase 3：Excel 清洗

1. 确定表头；
2. 字段标准化；
3. 类型转换；
4. 缺失值处理；
5. 重复数据处理；
6. 数据质量检查。

## Phase 4：MySQL Schema

根据 Phase 2 的真实数据结果确定：

```text
表
字段
类型
主键
唯一键
索引
外键
```

## Phase 5：数据库导入

实现：

```text
事务
批量写入
幂等
错误记录
导入报告
```

## Phase 6：验证

执行：

```text
Excel 总行数
        ↓
清洗后有效行数
        ↓
MySQL 写入行数
        ↓
数据库查询验证
```

确保数量和关键字段一致。

---

# 31. 最终验收标准

## PDF

满足：

```text
knowledge/pdf/*.pdf
        ↓
pdf_parser.py
        ↓
knowledge/knowledge.txt
```

并满足：

- 中文无乱码；
- 日语无乱码；
- 英文无乱码；
- 表格基本结构保留；
- 文件来源可识别；
- 重复运行结果稳定；
- 单个损坏 PDF 不影响其他 PDF；
- 失败内容有日志。

## Excel

满足：

```text
knowledge/excel/*.xlsx/*.xls
        ↓
excel_parser.py
        ↓
标准化数据
        ↓
MySQL
```

并满足：

- 所有目标 Excel 文件都能被扫描；
- 所有有效 Sheet 都被识别；
- 不同表头格式可以被处理；
- 中文 / 日文数据无乱码；
- 数字类型正确；
- 日期 / 年份类型正确；
- NULL 与 0 正确区分；
- 重复执行不会产生重复数据；
- 数据来源可追踪；
- 数据库写入失败可回滚；
- 导入结束有统计报告；
- 异常数据可定位到源文件和行。

---

# 32. 明确禁止的实现方式

IDE 实现时禁止以下做法：

### 1. 禁止硬编码数据库密码

```python
MYSQL_PASSWORD = "xxxx"
```

### 2. 禁止假设 Excel 第一行永远是表头

```python
pd.read_excel(file, header=0)
```

不能成为唯一策略。

### 3. 禁止所有 Excel 强制使用同一个 Schema

除非实际数据经过验证后确认结构一致。

### 4. 禁止异常数据静默转换

例如：

```text
"abc" → 0
```

### 5. 禁止重复导入产生重复业务数据

### 6. 禁止为了“清洗数据”修改业务含义

### 7. 禁止忽略来源信息

数据必须尽可能可以追溯到原始文件。

### 8. 禁止在未检查实际 Excel 数据结构之前确定最终业务表

---

# 33. IDE 实现要求

请根据本 SDD 直接实现：

```text
pdf_parser.py
excel_parser.py
```

实现过程中：

1. 优先检查当前项目已有代码和依赖；
2. 检查实际 `knowledge/pdf` 文件；
3. 检查实际 `knowledge/excel` 文件；
4. **先探查 Excel 实际结构，再确定最终 MySQL Schema**；
5. 不要凭空创造不存在的业务字段；
6. 不要修改原始文件；
7. 数据库配置使用环境变量；
8. 对异常提供明确日志；
9. 保证重复运行的幂等性；
10. 实现完成后执行实际数据验证；
11. 最终报告：
   - 处理了多少 PDF；
   - 生成多少文本内容；
   - 处理了多少 Excel；
   - 多少 Sheet；
   - 多少数据行；
   - 成功写入多少；
   - 跳过多少；
   - 多少异常；
   - 异常分别来自哪里。

---

# 34. 关键设计决策总结

| 项目 | 决策 |
|---|---|
| PDF 解析 | `unstructured.partition_pdf` |
| PDF 表格 | 优先启用高分辨率 / 表格结构识别 |
| PDF 输出 | `knowledge/knowledge.txt` |
| PDF 编码 | UTF-8 |
| PDF 多语言 | 中文 + 日语 + 英语原文保留 |
| Excel 读取 | pandas |
| Excel Sheet | 默认处理全部有效 Sheet |
| Excel 清洗 | DataFrame 标准化 |
| MySQL 连接 | SQLAlchemy + PyMySQL |
| MySQL 字符集 | utf8mb4 |
| 数据来源 | 保存 source_file / source_sheet / source_row 等信息 |
| 数据重复 | 业务唯一键优先，无唯一键时使用来源 + hash |
| 重复导入 | 必须幂等 |
| 数据库写入 | 事务 + 批量处理 |
| 密码 | 环境变量 |
| 最终 Schema | 必须根据真实 Excel 数据确定 |
| Parser 职责 | 解析、清洗、标准化、导入，不改变业务语义 |

---

# 35. 重要实现前提

**在开始编写 `excel_parser.py` 的数据库 Schema 部分之前，必须先实际检查 `knowledge/excel` 中的所有 Excel 文件。**

原因是当前需求只说明：

> “日本铁路十年间的铁路相关数据”

但没有提供：

- Excel 文件名称；
- Sheet 名称；
- 实际列名；
- 表头位置；
- 数据类型；
- 数据之间的关联关系；
- 哪些字段构成业务唯一键。

因此，不能仅凭需求文字臆造数据库 Schema。

正确流程必须是：

```text
实际 Excel
    ↓
结构探查
    ↓
字段分类
    ↓
业务实体识别
    ↓
确定表关系
    ↓
确定主键 / 唯一键
    ↓
确定字段类型
    ↓
确定索引
    ↓
实现 excel_parser.py
```

这一步是本任务中最重要的设计约束之一。

---

# 36. 参考资料

- Pandas `read_excel` 支持读取单个或多个 Sheet，也支持 `sheet_name=None` 读取整个工作簿。citeturn0search0turn0search1
- SQLAlchemy MySQL 方言支持 `mysql+pymysql://...` 形式的连接 URL。citeturn0search12turn0search15
- MySQL `INSERT ... ON DUPLICATE KEY UPDATE` 可用于基于 PRIMARY KEY / UNIQUE KEY 的幂等写入。citeturn0search3
- MySQL 对主键和唯一键冲突有明确的约束行为，事务型存储引擎应利用事务保证数据一致性。citeturn0search4
