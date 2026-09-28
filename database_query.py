"""Database Query Tool（只读）：

自然语言问题 -> 真实 Schema -> qwen3.8-flash 生成 SELECT -> sqlglot AST 安全校验
-> 包裹行数限制/超时 hint -> MySQL 执行 -> 结构化结果。

任何一步失败均返回带 error_type 的结构化错误，不向用户暴露密码/连接串/堆栈。
"""
import datetime as dt
import logging
import os
import time
from decimal import Decimal
from typing import Any, Dict, List

from langchain.tools import tool
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from database import get_database_schema, get_engine, load_config
from sql_generator import AmbiguousQuestion, SQLGenerationError, generate_sql
from sql_validator import validate_sql

logger = logging.getLogger("db_query")

# 默认只允许查询 6 张业务表；可用 ALLOWED_TABLES=a,b 覆盖
DEFAULT_ALLOWED_TABLES = [
    "railway_passenger_revenue",
    "railway_passenger_volume",
    "railway_energy_expense",
    "railway_running_distance",
    "railway_rolling_stock",
    "railway_staff_wages",
]

# MySQL 错误码 -> 查询超时
TIMEOUT_ERROR_CODES = {3024, 2013, 1886}


def _load_allowed_tables() -> List[str]:
    configured = os.environ.get("ALLOWED_TABLES", "").strip()
    if configured:
        return [t.strip() for t in configured.split(",") if t.strip()]
    return list(DEFAULT_ALLOWED_TABLES)


def _json_safe(value: Any) -> Any:
    """将数据库返回值转换为可 JSON 序列化的类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _error(error_type: str, message: str, sql: str = None) -> Dict[str, Any]:
    return {
        "success": False, "sql": sql, "columns": [], "rows": [],
        "row_count": 0, "error_type": error_type, "message": message,
    }


def _audit(question: str, sql: str, elapsed_ms: int, row_count: int,
           success: bool, error_type: str = "") -> None:
    # 审计日志禁止包含密码与完整连接 URL
    logger.info(
        "audit question=%r sql=%r elapsed_ms=%d row_count=%d success=%s error_type=%s",
        question, sql, elapsed_ms, row_count, success, error_type,
    )


@tool
def database_query(question: str) -> dict:
    """根据自然语言问题只读查询铁路 MySQL 数据库（2012-2021 年日本铁路公司/线路的
    旅客收入、旅客数量、资源支出费、走行千米数、车辆数量、雇员及工资等真实统计数据）。
    仅当回答需要读取本地数据库时调用；查不到数据时返回空 rows，由上层另行处理。
    """
    started = time.time()
    sql: str = None

    # 1) 真实 Schema
    try:
        schema = get_database_schema()
    except Exception:
        logger.exception("获取数据库 Schema 失败")
        return _error("DATABASE_CONNECTION_ERROR", "无法读取数据库结构")

    # 2) 生成 SQL
    allowed_tables = _load_allowed_tables()
    try:
        sql = generate_sql(question, schema, allowed_tables)
    except AmbiguousQuestion as exc:
        return _error("NEED_CLARIFICATION", exc.reason)
    except SQLGenerationError as exc:
        return _error("SQL_GENERATION_ERROR", str(exc))
    except Exception:
        logger.exception("SQL 生成异常")
        return _error("SQL_GENERATION_ERROR", "SQL 生成失败，请稍后重试")

    # 3) AST 安全校验
    current_db = load_config().database
    validation = validate_sql(sql, allowed_tables, current_db)
    if not validation.ok:
        _audit(question, sql, int((time.time() - started) * 1000), 0, False, "SQL_VALIDATION_ERROR")
        return _error("SQL_VALIDATION_ERROR", f"SQL 未通过安全校验: {validation.error}", sql)

    # 4) 包裹统一行数限制与服务端超时 hint。
    #    外层包一层 LIMIT 不改变聚合语义，只截断返回行数。
    sql = sql.strip().rstrip(";").strip()  # 尾部分号在子查询内非法
    max_rows = int(os.environ.get("MAX_QUERY_ROWS", "100"))
    timeout_ms = int(os.environ.get("QUERY_TIMEOUT_SECONDS", "30")) * 1000
    wrapped_sql = (
        f"SELECT /*+ MAX_EXECUTION_TIME({timeout_ms}) */ * "
        f"FROM ({sql}) AS _q LIMIT {max_rows}"
    )

    # 5) 执行（连接与执行分开捕获，区分连接错误/SQL 错误/超时）
    engine = get_engine()
    try:
        connection = engine.connect()
    except OperationalError:
        logger.exception("数据库连接失败")
        return _error("DATABASE_CONNECTION_ERROR", "无法连接数据库")

    try:
        try:
            result = connection.execute(text(wrapped_sql))
            raw_rows = result.fetchall()
            column_names = list(result.keys())
        except OperationalError as exc:
            code = exc.orig.args[0] if getattr(exc, "orig", None) and exc.orig.args else None
            if code in TIMEOUT_ERROR_CODES:
                _audit(question, wrapped_sql, int((time.time() - started) * 1000),
                       0, False, "QUERY_TIMEOUT")
                return _error("QUERY_TIMEOUT", "查询超时，请缩小查询范围后重试", wrapped_sql)
            logger.exception("SQL 执行失败")
            return _error("SQL_EXECUTION_ERROR", "数据库查询执行失败", wrapped_sql)
        except SQLAlchemyError:
            logger.exception("SQL 执行异常")
            return _error("SQL_EXECUTION_ERROR", "数据库查询执行失败", wrapped_sql)
    finally:
        connection.close()

    # 6) 格式化结果
    elapsed_ms = int((time.time() - started) * 1000)
    if not raw_rows:
        _audit(question, wrapped_sql, elapsed_ms, 0, True)
        return {"success": True, "sql": wrapped_sql, "columns": [],
                "rows": [], "row_count": 0}

    rows: List[Dict[str, Any]] = [
        {name: _json_safe(value) for name, value in zip(column_names, row)}
        for row in raw_rows
    ]
    _audit(question, wrapped_sql, elapsed_ms, len(rows), True)
    return {
        "success": True,
        "sql": wrapped_sql,
        "columns": column_names,
        "rows": rows,
        "row_count": len(rows),
    }
