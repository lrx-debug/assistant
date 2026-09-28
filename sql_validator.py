"""SQL 安全校验：基于 sqlglot AST，而不是 startswith("select") 字符串匹配。

校验项：
1. 有且只有一个 SQL 语句（禁止多语句）；
2. 根节点为只读查询（SELECT / UNION 等）；
3. 全树不存在任何写操作 / DDL / 管理命令；
4. SQL 中实际访问的表全部在允许名单内；
5. 不允许跨库 / 跨 catalog 访问；
6. 禁止 SELECT ... INTO OUTFILE/DUMPFILE 及危险函数。
"""
from dataclasses import dataclass, field
from typing import List, Set

import sqlglot
from sqlglot import exp

# 根节点允许的只读类型
READONLY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# 全树禁止出现的节点类型（写操作 / DDL / 管理命令）
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Replace,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Command,        # SET、CALL、LOAD DATA 等命令
    exp.Grant, exp.Revoke,
)

# 危险函数（读服务端文件 / 拖延时间 / 压测）
FORBIDDEN_FUNCTIONS = {"LOAD_FILE", "SLEEP", "BENCHMARK"}


@dataclass
class ValidationResult:
    ok: bool
    error: str = ""
    tables: Set[str] = field(default_factory=set)


def validate_sql(sql: str, allowed_tables: List[str], current_database: str) -> ValidationResult:
    """校验 SQL，返回 ValidationResult。任何一项不通过均拒绝执行。"""
    if not sql or not sql.strip():
        return ValidationResult(False, "空 SQL")

    # 1) 解析全部语句（MySQL 方言）。trailing ';' 产生的空节点需剔除。
    try:
        parsed = [stmt for stmt in sqlglot.parse(sql, read="mysql") if stmt is not None]
    except sqlglot.errors.ParseError as exc:
        return ValidationResult(False, f"SQL 解析失败: {str(exc)[:120]}")

    if len(parsed) != 1:
        return ValidationResult(False, "只允许执行一条 SQL 语句，检测到多条语句")
    statement = parsed[0]

    # 2) 根节点必须是只读查询
    if not isinstance(statement, READONLY_ROOTS):
        return ValidationResult(False, f"只允许 SELECT 查询，检测到 {type(statement).__name__}")

    # 3) 全树扫描禁止节点
    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return ValidationResult(False, f"检测到禁止的操作: {type(node).__name__}")

    # 4) SELECT ... INTO OUTFILE/DUMPFILE（可向服务器写文件）
    for into in statement.find_all(exp.Into):
        text = into.sql().upper()
        if "OUTFILE" in text or "DUMPFILE" in text:
            return ValidationResult(False, "禁止 SELECT INTO OUTFILE/DUMPFILE")

    # 5) 危险函数（匿名函数在 sqlglot 中名称在 .name 上）
    for func in statement.find_all(exp.Func, exp.Anonymous):
        if func.name.upper() in FORBIDDEN_FUNCTIONS:
            return ValidationResult(False, f"禁止使用函数 {func.name.upper()}")

    # 6) 实际访问的表：必须在允许名单，且不得跨库 / 跨 catalog
    allowed = set(allowed_tables)
    referenced: Set[str] = set()
    for table in statement.find_all(exp.Table):
        name = table.name
        referenced.add(name)
        if table.catalog:
            return ValidationResult(False, f"禁止跨 catalog 访问: {table.catalog}")
        if table.db and table.db != current_database:
            return ValidationResult(False, f"禁止跨数据库访问: {table.db}.{name}")
        if name not in allowed:
            return ValidationResult(False, f"访问了未授权的表: {name}")

    if not referenced:
        return ValidationResult(False, "SQL 中未检测到任何表")

    return ValidationResult(True, tables=referenced)
