"""数据库连接层：读取 .env 配置，创建 SQLAlchemy Engine，并从 information_schema
获取真实数据库 Schema（表、字段、类型、主键、唯一键）。

不在业务代码中硬编码密码；不记录连接 URL / 密码。
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def load_config() -> DBConfig:
    """从环境变量读取数据库配置（由上层保证 .env 已加载）。"""
    return DBConfig(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        # 项目实际数据由 excel_parser 写入 railway 库；SDD 中的 tetsudou 名称
        # 可通过 MYSQL_DATABASE 环境变量覆盖。
        database=os.environ.get("MYSQL_DATABASE", "railway"),
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """创建（并缓存）SQLAlchemy Engine。创建时不建立实际连接。"""
    cfg = load_config()
    url = URL.create(
        drivername="mysql+pymysql",
        username=cfg.user,
        password=cfg.password,
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
        query={"charset": "utf8mb4"},
    )
    return create_engine(
        url, pool_pre_ping=True, pool_size=3, max_overflow=5,
        # 客户端侧语句超时（秒），与 MySQL MAX_EXECUTION_TIME hint 互为兜底
        connect_args={
            "read_timeout": int(os.environ.get("QUERY_TIMEOUT_SECONDS", "30")),
            "write_timeout": int(os.environ.get("QUERY_TIMEOUT_SECONDS", "30")),
        },
    )


# ---- Schema 语义补充说明（结构本身全部来自 information_schema，不允许猜测） ----

TABLE_HINTS: Dict[str, str] = {
    "railway_passenger_revenue": "铁路旅客收入表（金额单位：千日元；占比/同比单位：%）",
    "railway_passenger_volume": "铁路旅客数量（输送人员）表（单位：千人；占比单位：%）",
    "railway_energy_expense": "铁路资源支出费表（费用单位：千日元；占比单位：%）",
    "railway_running_distance": "列车走行千米数表（单位：千米；占比单位：%）",
    "railway_rolling_stock": "车辆数量表（单位：辆）",
    "railway_staff_wages": "雇员数量及工资表（人数单位：人；工资单位：千日元）",
    "data_source": "数据溯源表（记录数据来自哪个源文件/sheet/行，仅供 JOIN 溯源）",
}

GLOBAL_SCHEMA_NOTES = """全局字段含义（适用于所有业务表）：
- year：统计年份，公历，范围 2012-2021。
- record_type：行类型，枚举值：
  operator=铁路事业者（公司）汇总行；line=单条线路行；
  bureau_subtotal=运输局小计行；national_subtotal=全国分类小计行；
  grand_total=全国总计行；subtotal=其他小计行。
- operator_name：事业者（公司）日文名称，如「東日本旅客鉄道」「東海旅客鉄道」。
- line_name：线路日文名称；非线路行该字段为空字符串 ''（不是 NULL）。
- region_bureau：所属运输局日文名称，如「関東運輸局」；总计等局外行为空字符串 ''。
- 各业务表 source_id 关联 data_source.id，可 JOIN 查询源文件 source_file。
- 指标为 NULL 表示官方未公布/订正/不予公开；0 是真实业务值，二者不可混淆。
- bureau_subtotal（局小计行）、national_subtotal（全国分类小计行）仅部分年份存在
  （近年官方报表已删除局小计行）。统计任意年份各运输局指标时，应使用
  record_type='operator' 的行按 region_bureau 做 GROUP BY 聚合；region_bureau=''
  的是跨局/全国性公司（如六家 JR、日本貨物鉄道），不计入任何单一运输局。
"""


def _query_columns(engine: Engine, database: str) -> Dict[str, List[dict]]:
    sql = text("""
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :db
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)
    result: Dict[str, List[dict]] = {}
    with engine.connect() as conn:
        for row in conn.execute(sql, {"db": database}).mappings():
            result.setdefault(row["TABLE_NAME"], []).append(dict(row))
    return result


def _query_unique_keys(engine: Engine, database: str) -> Dict[str, Dict[str, List[str]]]:
    """返回 {表: {约束名: [列...]}}，含主键与唯一键。"""
    sql = text("""
        SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = :db
        ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
    """)
    keys: Dict[str, Dict[str, List[str]]] = {}
    with engine.connect() as conn:
        for row in conn.execute(sql, {"db": database}).mappings():
            (keys.setdefault(row["TABLE_NAME"], {})
                 .setdefault(row["CONSTRAINT_NAME"], []).append(row["COLUMN_NAME"]))
    return keys


@lru_cache(maxsize=1)
def get_database_schema() -> str:
    """读取当前 MySQL 的真实 Schema 并格式化为 LLM 可读文本。

    结构信息（表/列/类型/键）全部来自 information_schema，仅附加人工语义注释。
    """
    cfg = load_config()
    engine = get_engine()
    columns = _query_columns(engine, cfg.database)
    keys = _query_unique_keys(engine, cfg.database)

    lines: List[str] = [f"数据库：{cfg.database}", ""]
    for table in sorted(columns):
        hint = TABLE_HINTS.get(table, "")
        lines.append(f"表 {table} — {hint}")
        for col in columns[table]:
            attrs = []
            if col["IS_NULLABLE"] == "NO":
                attrs.append("NOT NULL")
            if col["COLUMN_KEY"] == "PRI":
                attrs.append("主键")
            if col["COLUMN_KEY"] == "UNI":
                attrs.append("唯一")
            if col["EXTRA"]:
                attrs.append(col["EXTRA"])
            attr_text = f" [{', '.join(attrs)}]" if attrs else ""
            lines.append(f"    {col['COLUMN_NAME']} {col['COLUMN_TYPE']}{attr_text}")
        for constraint, cols in keys.get(table, {}).items():
            label = "主键" if constraint == "PRIMARY" else f"唯一键 {constraint}"
            lines.append(f"    {label}: ({', '.join(cols)})")
        lines.append("")
    lines.append(GLOBAL_SCHEMA_NOTES)
    return "\n".join(lines)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    print(get_database_schema())
