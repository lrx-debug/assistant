"""任务二：Excel 转 MySQL 结构化数据。

扫描 knowledge/excel 下全部 Excel（.xlsx/.xls/.xlsm），逐 Sheet：
表头识别（多级表头）-> 字段标准化（日语表头路径 -> snake_case）
-> 数值/缺失值/重复数据清洗 -> 数据质量校验
-> 事务 + ON DUPLICATE KEY UPDATE 幂等写入 MySQL（utf8mb4），
并输出导入质量报告。

最终业务表 Schema 基于 2012-2021 真实文件探查确定（跨年份字段超集）。
"""
import hashlib
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------- 配置
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
EXCEL_INPUT_DIR = BASE_DIR / "knowledge" / "excel"

MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "railway")

EXCEL_SUFFIXES = (".xlsx", ".xls", ".xlsm")

# 单位类表头段，不参与字段语义
UNIT_TOKENS = {
    "千円", "円", "千人", "千個", "トン", "人", "人月",
    "キロメートル", "千キロメートル", "キロワット時", "キロリットル", "％", "%",
}
# 缺失值标记（业务 0 不在其中，0 必须保留）
NULL_TOKENS = {"", "-", "－", "—", "–", "―", "N/A", "n/a", "null", "NULL", "・", "…"}
# 非业务列（制表人核对辅助列）
NONBUSINESS_SEGMENTS = {"check", "NG個数", "正味"}
# 裸局名抬头行（2020/2021 文件中局区段以此开头，而非局小计收尾）
BUREAU_HEADER_RE = re.compile(
    r"(?:北海道|東北|関東|北陸信越|中部|近畿|中国|四国|九州)運輸局|沖縄総合事務局"
)
# 官方订正标记，如 R3.8.25修正（出现在数据格内，表示该值已订正、原指标不可用）
REVISION_MARK_RE = re.compile(r"^[RＲ]?\s*\d+(?:\.\d+){1,2}\s*修正$")
# 官方不予公开标注（依协定不能刊载），语义为"主动留空"而非数据错误
WITHHELD_RE = re.compile(r".*(?:掲載不可|非公表|公表していない).*")
REMARK_SEGMENTS = {"備考", "備考・疑義", "疑義・備考", "疑義"}

BIGINT = "BIGINT"
PCT = "DECIMAL(8,3)"
DECIMAL3 = "DECIMAL(14,3)"
KM = "DECIMAL(10,2)"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("excel_parser")


# ---------------------------------------------------------------- 字段映射
@dataclass(frozen=True)
class MetricSpec:
    file_keyword: str          # 文件名中的指标关键词
    table: str                 # MySQL 业务表
    columns: dict[str, str]     # 标准化表头路径 -> (列名:类型 用 : 连接)
    aliases: dict[str, str]    # 表头路径变体 -> 标准路径


def _cols(table_pairs: list[tuple[str, str, str]]) -> dict[str, str]:
    return {path: f"{name}:{typ}" for path, name, typ in table_pairs}


# ---- 旅客收入
REVENUE_SPEC = MetricSpec(
    "旅客收入", "railway_passenger_revenue",
    _cols([
        ("旅客運輸収入/定期/通勤", "season_commute_revenue", BIGINT),
        ("旅客運輸収入/定期/通学", "season_school_revenue", BIGINT),
        ("旅客運輸収入/定期/小計", "season_revenue_subtotal", BIGINT),
        ("旅客運輸収入/定期/構成比", "season_share_pct", PCT),
        ("旅客運輸収入/定期外", "nonseason_revenue", BIGINT),
        ("旅客運輸収入/定期外/構成比", "nonseason_share_pct", PCT),
        ("旅客運輸収入/計", "passenger_revenue_total", BIGINT),
        ("旅客運輸収入/手小荷物", "parcel_revenue", BIGINT),
        ("旅客運輸収入/合計", "passenger_revenue_grand", BIGINT),
        ("貨物運輸収入/コンテナ", "freight_container_revenue", BIGINT),
        ("貨物運輸収入/車扱", "freight_car_revenue", BIGINT),
        ("貨物運輸収入/合計", "freight_revenue_total", BIGINT),
        ("鉄道線路/使用料収入", "track_usage_fee", BIGINT),
        ("鉄道線路/譲渡収入", "track_transfer_revenue", BIGINT),
        ("運輸雑収", "misc_transport_revenue", BIGINT),
        ("総合計", "grand_total_revenue", BIGINT),
        ("総合計/前年度比", "grand_total_yoy_pct", PCT),
    ]),
    {},
)

# ---- 旅客数量
VOLUME_SPEC = MetricSpec(
    "旅客数量", "railway_passenger_volume",
    _cols([
        ("輸送人員/定期/通勤", "season_commute_persons", BIGINT),
        ("輸送人員/定期/通学", "season_school_persons", BIGINT),
        ("輸送人員/定期/計", "season_persons_subtotal", BIGINT),
        ("輸送人員/定期/構成比", "season_share_pct", PCT),
        ("輸送人員/定期/前年度比", "season_yoy_pct", PCT),
        ("輸送人員/定期外", "nonseason_persons", BIGINT),
        ("輸送人員/定期外/構成比", "nonseason_share_pct", PCT),
        ("輸送人員/定期外/前年度比", "nonseason_yoy_pct", PCT),
        ("輸送人員/合計", "total_persons", BIGINT),
        ("輸送人員/合計/前年度比", "total_yoy_pct", PCT),
        ("手小荷物個数", "parcel_count", BIGINT),
        ("輸送トン数/コンテナ", "freight_container_tons", BIGINT),
        ("輸送トン数/車扱", "freight_car_tons", BIGINT),
        ("輸送トン数/合計", "freight_tons_total", BIGINT),
        ("輸送トン数/合計/前年度比", "freight_tons_yoy_pct", PCT),
    ]),
    {},
)

# ---- 资源支出费
ENERGY_SPEC = MetricSpec(
    "资源支出费", "railway_energy_expense",
    _cols([
        ("電力/数量", "power_quantity_kwh", BIGINT),
        ("電力/代価", "power_cost", BIGINT),
        ("軽油/数量", "diesel_quantity_kl", DECIMAL3),
        ("軽油/代価", "diesel_cost", BIGINT),
        ("その他の燃料/代価", "other_fuel_cost", BIGINT),
    ]),
    {},
)

# ---- 走行千米数
def _distance_columns() -> dict[str, str]:
    pairs: list[tuple[str, str, str]] = [
        ("年度末営業キロ/旅客", "operating_km_passenger", KM),
        ("年度末営業キロ/貨物", "operating_km_freight", KM),
    ]
    groups = [
        ("自己車両自線走行キロ/車両", "olv"),
        ("他鉄道車両自線走行キロ/車両", "oov"),
        ("合計/車両", "tv"),
        ("自己車両他線走行キロ/車両", "oll"),
    ]
    vehicle_parts = [
        ("機関車/電気", "loco_electric"),
        ("機関車/内燃", "loco_diesel"),
        ("機関車/蒸気", "loco_steam"),
        ("機関車/その他", "loco_other"),
        ("旅客車/電車", "pass_emus"),
        ("旅客車/内燃動車", "pass_dmus"),
        ("旅客車/客車", "pass_coaches"),
        ("旅客車/その他", "pass_other"),
        ("貨物車", "freight"),
        ("特殊車", "special"),
        ("合計", "total"),
    ]
    for prefix, short in groups:
        for tail, name in vehicle_parts:
            pairs.append((f"{prefix}/{tail}", f"{short}_{name}", BIGINT))
    # 总车辆列在"合計/車両"组下没有第三层"合計"叶节点
    pairs = [(p, n, t) if (p, n) != ("合計/車両/合計", "tv_total")
             else ("合計/車両", "tv_total", BIGINT) for (p, n, t) in pairs]
    pairs += [
        ("合計/列車/旅客", "tt_passenger", BIGINT),
        ("合計/列車/貨物", "tt_freight", BIGINT),
        ("合計/列車/混合", "tt_mixed", BIGINT),
        ("合計/列車", "tt_all", BIGINT),
    ]
    return _cols(pairs)


DISTANCE_SPEC = MetricSpec(
    "走行千米数", "railway_running_distance",
    _distance_columns(),
    {"自己車両自線走行キロ/年度末営業キロ/旅客": "年度末営業キロ/旅客",
     "自己車両自線走行キロ/年度末営業キロ/貨物": "年度末営業キロ/貨物"},
)

# ---- 车辆数量
def _vehicles_columns() -> dict[str, str]:
    pairs: list[tuple[str, str, str]] = [
        ("機関車/電気/直流", "loco_electric_dc", BIGINT),
        ("機関車/電気/交流", "loco_electric_ac", BIGINT),
        ("機関車/電気/交直流", "loco_electric_acdc", BIGINT),
        ("機関車/内燃", "loco_diesel", BIGINT),
        ("機関車/蒸気", "loco_steam", BIGINT),
        ("機関車/その他", "loco_other", BIGINT),
        ("機関車/計", "loco_total", BIGINT),
    ]
    for current, short in [("直流", "dc"), ("交流", "ac"), ("交直流", "acdc")]:
        for kind, name in [("制御電動", "control_motor"), ("電動", "motor"),
                           ("制御", "control"), ("付随", "trailer"), ("計", "total")]:
            pairs.append((f"旅客車/電車/{current}/{kind}", f"emu_{short}_{name}", BIGINT))
    pairs += [
        ("旅客車/電車/計", "emu_total", BIGINT),
        ("旅客車/内燃動車", "dmu_count", BIGINT),
        ("旅客車/客車/座席車", "coach_seat", BIGINT),
        ("旅客車/客車/寝台車", "coach_sleeper", BIGINT),
        ("旅客車/客車/その他", "coach_other", BIGINT),
        ("旅客車/その他", "passenger_other", BIGINT),
        ("旅客車/計", "passenger_total", BIGINT),
        ("貨物車/貨物電車", "freight_emus", BIGINT),
        ("貨物車/貨車/有がい", "freight_covered", BIGINT),
        ("貨物車/貨車/無がい", "freight_open", BIGINT),
        ("貨物車/貨車/コンテナ", "freight_container", BIGINT),
        ("貨物車/貨車/タンク", "freight_tank", BIGINT),
        ("貨物車/貨車/ホッパ", "freight_hopper", BIGINT),
        ("貨物車/荷物車", "baggage_cars", BIGINT),
        ("貨物車/その他", "freight_other", BIGINT),
        ("貨物車/計", "freight_total", BIGINT),
        ("特殊車", "special_cars", BIGINT),
        ("合計", "total_vehicles", BIGINT),
    ]
    return _cols(pairs)


VEHICLES_SPEC = MetricSpec(
    "车辆数量", "railway_rolling_stock",
    _vehicles_columns(),
    {},
)

# ---- 雇员数量及工资
STAFF_SPEC = MetricSpec(
    "雇员数量及工资", "railway_staff_wages",
    _cols([
        ("役員", "officers_count", BIGINT),
        ("職員/本社部門/総務", "hq_admin", BIGINT),
        ("職員/本社部門/運輸", "hq_transport", BIGINT),
        ("職員/本社部門/工務", "hq_engineering", BIGINT),
        ("職員/本社部門/電気", "hq_electrical", BIGINT),
        ("職員/本社部門/車両", "hq_rolling", BIGINT),
        ("職員/本社部門/建設", "hq_construction", BIGINT),
        ("職員/本社部門/計", "hq_total", BIGINT),
        ("職員/現業部門/運輸/駅職員", "field_station_staff", BIGINT),
        ("職員/現業部門/運輸/運転手", "field_drivers", BIGINT),
        ("職員/現業部門/運輸/車掌", "field_conductors", BIGINT),
        ("職員/現業部門/運輸/その他", "field_transport_other", BIGINT),
        ("職員/現業部門/運輸/小計", "field_transport_subtotal", BIGINT),
        ("職員/現業部門/工務", "field_engineering", BIGINT),
        ("職員/現業部門/電気", "field_electrical", BIGINT),
        ("職員/現業部門/車両", "field_rolling", BIGINT),
        ("職員/現業部門/建設", "field_construction", BIGINT),
        ("職員/現業部門/計", "field_total", BIGINT),
        ("職員/合計", "staff_total", BIGINT),
        ("年間総額/通常給与/基準賃金", "regular_base_salary", BIGINT),
        ("年間総額/通常給与/基準外賃金", "regular_other_salary", BIGINT),
        ("年間総額/通常給与/計", "regular_salary_total", BIGINT),
        ("年間総額/臨時給与", "temporary_salary", BIGINT),
        ("年間総額/合計", "annual_salary_total", BIGINT),
        ("延べ人員", "person_months", BIGINT),
        ("1人1ヶ月平均給与/基準賃金", "avg_monthly_base_salary", BIGINT),
        ("1人1ヶ月平均給与/基準外賃金", "avg_monthly_other_salary", BIGINT),
        ("1人1ヶ月平均給与/臨時給与", "avg_monthly_temporary_salary", BIGINT),
    ]),
    {},
)

METRIC_SPECS: list[MetricSpec] = [
    REVENUE_SPEC, VOLUME_SPEC, ENERGY_SPEC,
    DISTANCE_SPEC, VEHICLES_SPEC, STAFF_SPEC,
]


def select_metric_spec(file_name: str) -> MetricSpec | None:
    for spec in METRIC_SPECS:
        if spec.file_keyword in file_name:
            return spec
    return None


# ---------------------------------------------------------------- 文件发现
def discover_excel_files() -> list[Path]:
    return sorted(
        p for p in EXCEL_INPUT_DIR.glob("*")
        if p.suffix.lower() in EXCEL_SUFFIXES and not p.name.startswith(("~", "."))
    )


# ---------------------------------------------------------------- 表头识别
def _norm_token(value) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", "", text).strip()


def find_data_start(df: pd.DataFrame) -> int | None:
    """数据起始行：首行 col0 非空且右侧至少存在一个数字。"""
    for i in range(df.shape[0]):
        if pd.notna(df.iat[i, 0]) and pd.to_numeric(df.iloc[i, 2:], errors="coerce").notna().any():
            return i
    return None


def build_column_paths(df: pd.DataFrame, data_start: int) -> dict[int, str]:
    """多级表头（行 2..data_start-1，纵向 ffill）-> 每列语义路径。"""
    header = df.iloc[2:data_start].ffill()
    paths: dict[int, str] = {}
    for c in range(2, df.shape[1]):
        parts = []
        for r in range(header.shape[0]):
            token = _norm_token(header.iat[r, c]) if pd.notna(header.iat[r, c]) else ""
            if token and token not in UNIT_TOKENS and token not in parts:
                parts.append(token)
        paths[c] = "/".join(parts)
    return paths


# ---------------------------------------------------------------- 行清洗
def classify_record(raw_name: str, line_name: str | None) -> str:
    if line_name:
        return "line"
    if re.search(r"運輸局計$", raw_name) or raw_name == "沖縄総合事務局計":
        return "bureau_subtotal"
    if raw_name.endswith("合計"):
        return "national_subtotal"
    if raw_name.startswith("総合計"):
        return "grand_total"
    if raw_name.endswith("計"):
        return "subtotal"
    return "operator"


def clean_number(raw, column_name: str, errors: list[str], location: str):
    """单元格 -> 数值；非法数字记录异常，不静默转 0。"""
    if pd.isna(raw):
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if text in NULL_TOKENS:
        return None
    number = pd.to_numeric(text.replace(",", ""), errors="coerce")
    if pd.isna(number):
        errors.append(f"{location} 字段={column_name} 原始值={raw!r} 原因=非法数字")
        return None
    return float(number)


def generate_row_hash(values: list) -> str:
    joined = "|".join("" if v is None else str(v) for v in values)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- DDL
SOURCE_DDL = """
CREATE TABLE IF NOT EXISTS data_source (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_file VARCHAR(512) NOT NULL,
    source_sheet VARCHAR(255) NOT NULL,
    source_row INT NOT NULL,
    metric_type VARCHAR(32) NOT NULL,
    row_hash CHAR(64) NOT NULL,
    imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_source (source_file, source_sheet, source_row)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def build_business_ddl(spec: MetricSpec) -> str:
    cols = ["id BIGINT AUTO_INCREMENT PRIMARY KEY",
            "year SMALLINT NOT NULL",
            "record_type VARCHAR(20) NOT NULL",
            "operator_name VARCHAR(255) NOT NULL",
            "line_name VARCHAR(255) NOT NULL DEFAULT ''",
            "region_bureau VARCHAR(64) NOT NULL DEFAULT ''",
            "remark TEXT"]
    update_cols = ["remark"]
    for path, decl in spec.columns.items():
        name, typ = decl.split(":")
        cols.append(f"{name} {typ}")
        update_cols.append(name)
    cols += ["source_id BIGINT",
             "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
             "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
             "ON UPDATE CURRENT_TIMESTAMP",
             "UNIQUE KEY uq_business (year, record_type, operator_name, line_name, region_bureau)"]
    return (f"CREATE TABLE IF NOT EXISTS {spec.table} (\n  "
            + ",\n  ".join(cols) + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"), update_cols


def prepare_database(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(SOURCE_DDL))
        for spec in METRIC_SPECS:
            ddl, _ = build_business_ddl(spec)
            conn.execute(text(ddl))


# ---------------------------------------------------------------- 单 Sheet 处理
def process_sheet(
    df_raw: pd.DataFrame,
    year: int,
    spec: MetricSpec,
    source_file: str,
    sheet_name: str,
    counters: dict,
    validation_errors: list[str],
):
    df = df_raw.dropna(axis=1, how="all")
    data_start = find_data_start(df)
    if data_start is None:
        counters["empty_sheets"] += 1
        logger.warning("空 Sheet 或无法识别表头：%s -> %s", source_file, sheet_name)
        return []

    paths = build_column_paths(df, data_start)
    # 列位置 -> (业务列名, SQL类型)
    column_targets: dict[int, tuple[str, str]] = {}
    remark_col = None
    for c, path in paths.items():
        segments = path.split("/")
        if not path or any(seg in NONBUSINESS_SEGMENTS for seg in segments):
            continue
        if segments[-1] in REMARK_SEGMENTS or path in REMARK_SEGMENTS:
            remark_col = c
            continue
        canonical = spec.aliases.get(path, path)
        decl = spec.columns.get(canonical)
        if decl is None:
            logger.warning("未映射列 %s [%s] %s", source_file, sheet_name, path)
            continue
        name, typ = decl.split(":")
        column_targets[c] = (name, typ)

    records = []
    last_operator = None
    current_bureau = None
    for idx in range(data_start, df.shape[0]):
        raw_name = df.iat[idx, 0]
        raw_line = df.iat[idx, 1] if df.shape[1] > 1 else None
        line_name = _norm_token(raw_line) if pd.notna(raw_line) else None
        name = _norm_token(raw_name) if pd.notna(raw_name) else ""

        # 裸局名抬头：后续记录前向归属该局
        if name and line_name is None and BUREAU_HEADER_RE.fullmatch(name):
            current_bureau = name
            continue

        value_cells = [df.iat[idx, c] for c in column_targets]
        if line_name is None and (not name or all(pd.isna(v) for v in value_cells)):
            continue  # 局/类别等纯结构行或空行

        if line_name:
            operator_name = last_operator
        else:
            operator_name = name
            last_operator = name

        record_type = classify_record(name, line_name)
        # 全国合计位于局区段之外
        record_bureau = current_bureau
        if record_type in ("national_subtotal", "grand_total"):
            current_bureau = None
            record_bureau = None
        location = f"{source_file}/{sheet_name}/行{idx + 1}"

        metrics = {}
        revision_notes: list[str] = []
        for c, (col_name, typ) in column_targets.items():
            raw_cell = df.iat[idx, c]
            cell_text = raw_cell.strip() if isinstance(raw_cell, str) else raw_cell
            if isinstance(cell_text, str) and REVISION_MARK_RE.fullmatch(cell_text):
                metrics[col_name] = None  # 原指标已订正，不可用
                revision_notes.append(cell_text)
            elif isinstance(cell_text, str) and WITHHELD_RE.match(cell_text):
                metrics[col_name] = None  # 官方不予公开
                revision_notes.append(cell_text)
            else:
                metrics[col_name] = clean_number(raw_cell, col_name, validation_errors, location)

        if not line_name and not operator_name:
            counters["rows_skipped"] += 1
            continue

        remark = None
        if remark_col is not None and pd.notna(df.iat[idx, remark_col]):
            remark = str(df.iat[idx, remark_col]).strip() or None
        if revision_notes:
            note = "; ".join(revision_notes)
            remark = f"{remark}; {note}" if remark else note

        row_hash = generate_row_hash(
            [year, record_type, operator_name, line_name]
            + [df.iat[idx, c] for c in column_targets]
        )
        counters["rows_read"] += 1
        records.append({
            "year": year, "record_type": record_type,
            "operator_name": operator_name,
            "line_name": line_name or "",  # 唯一键列，NULL 会使 MySQL 唯一约束失效
            "region_bureau": record_bureau or "",
            "metrics": metrics, "remark": remark,
            "source_row": idx + 1, "row_hash": row_hash,
        })
    counters["sheets_processed"] += 1

    # 局区段回填：遇到局小计行时，其之前缓冲的记录归属该局。
    # 中小計/公営計 等小计在每个局各出现一次，必须靠 region_bureau 区分。
    flushed: list[dict] = []
    buffer_: list[dict] = []
    for rec in records:
        if rec["record_type"] == "bureau_subtotal":
            bureau = rec["operator_name"][:-1]  # 去掉末尾「計」
            rec["region_bureau"] = bureau
            for pending in buffer_:
                if not pending["region_bureau"]:
                    pending["region_bureau"] = bureau
            flushed.extend(buffer_)
            flushed.append(rec)
            buffer_ = []
        else:
            buffer_.append(rec)
    # 缓冲尾（全国合计等局区外记录）region_bureau 保持 ""
    flushed.extend(buffer_)
    return flushed


# ---------------------------------------------------------------- 写入
def write_file_records(conn, spec: MetricSpec, source_file: str, records: list[dict],
                       counters: dict) -> None:
    for rec in records:
        # 写入前快照：旧溯源 hash + 业务行是否已存在。
        # 不能依赖 rowcount：SQLAlchemy/pymysql 链路下"插入"与"匹配未改"都可能返回 1。
        old_hash = conn.execute(text(
            "SELECT row_hash FROM data_source WHERE source_file=:f "
            "AND source_sheet=:s AND source_row=:r"
        ), {"f": source_file, "s": rec["sheet"],
            "r": rec["source_row"]}).scalar_one_or_none()
        business_exists = conn.execute(text(
            f"SELECT 1 FROM {spec.table} WHERE year=:y AND record_type=:rt "
            "AND operator_name=:o AND line_name=:l AND region_bureau=:rb"
        ), {"y": rec["year"], "rt": rec["record_type"],
            "o": rec["operator_name"], "l": rec["line_name"],
            "rb": rec["region_bureau"]}).first() is not None

        conn.execute(text(
            "INSERT INTO data_source (source_file, source_sheet, source_row, metric_type, row_hash) "
            "VALUES (:f, :s, :r, :m, :h) "
            "ON DUPLICATE KEY UPDATE row_hash=VALUES(row_hash), imported_at=NOW()"
        ), {"f": source_file, "s": rec["sheet"], "r": rec["source_row"],
            "m": spec.file_keyword, "h": rec["row_hash"]})
        source_id = conn.execute(text("SELECT id FROM data_source WHERE source_file=:f "
                                      "AND source_sheet=:s AND source_row=:r"),
                                 {"f": source_file, "s": rec["sheet"],
                                  "r": rec["source_row"]}).scalar_one()

        names = ["year", "record_type", "operator_name", "line_name",
                 "region_bureau", "remark", "source_id"]
        params = {"year": rec["year"], "record_type": rec["record_type"],
                  "operator_name": rec["operator_name"], "line_name": rec["line_name"],
                  "region_bureau": rec["region_bureau"],
                  "remark": rec["remark"], "source_id": source_id}
        for col_name, value in rec["metrics"].items():
            names.append(col_name)
            params[col_name] = value

        placeholders = ", ".join(f":{n}" for n in names)
        updates = ", ".join(f"{n}=VALUES({n})"
                            for n in names if n not in ("year", "record_type",
                                                        "operator_name", "line_name",
                                                        "region_bureau"))
        conn.execute(text(
            f"INSERT INTO {spec.table} ({', '.join(names)}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        ), params)

        if not business_exists:
            counters["rows_inserted"] += 1
        elif old_hash != rec["row_hash"]:
            counters["rows_updated"] += 1
        else:
            counters["rows_unchanged"] += 1


# ---------------------------------------------------------------- 报告
def generate_import_report(counters: dict, failed_files: dict[str, str],
                           validation_errors: list[str]) -> None:
    sep = "=" * 50
    logger.info(sep)
    logger.info("Excel Import Summary")
    logger.info(sep)
    logger.info("Files discovered: %d", counters["files_discovered"])
    logger.info("Files processed : %d", counters["files_processed"])
    logger.info("Files failed    : %d", len(failed_files))
    logger.info("Sheets processed: %d", counters["sheets_processed"])
    logger.info("Empty sheets    : %d", counters["empty_sheets"])
    logger.info("Rows read       : %d", counters["rows_read"])
    logger.info("Rows inserted   : %d", counters["rows_inserted"])
    logger.info("Rows updated    : %d", counters["rows_updated"])
    logger.info("Rows unchanged  : %d", counters["rows_unchanged"])
    logger.info("Rows skipped    : %d", counters["rows_skipped"])
    logger.info("Validation errors: %d", len(validation_errors))
    for detail in validation_errors[:50]:
        logger.warning("  %s", detail)
    if len(validation_errors) > 50:
        logger.warning("  ...另有 %d 条未显示", len(validation_errors) - 50)
    if failed_files:
        logger.error("Failed files:")
        for name, reason in failed_files.items():
            logger.error("  %s -> %s", name, reason)
    logger.info(sep)


# ---------------------------------------------------------------- 主流程
def make_server_engine():
    url = (f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
           f"@{MYSQL_HOST}:{MYSQL_PORT}/?charset=utf8mb4")
    return create_engine(url)


def main() -> int:
    if not EXCEL_INPUT_DIR.is_dir():
        logger.error("Excel 目录不存在：%s", EXCEL_INPUT_DIR)
        return 1

    files = discover_excel_files()
    if not files:
        logger.error("Excel 目录为空：%s", EXCEL_INPUT_DIR)
        return 1

    # 确保数据库存在（utf8mb4）
    server_engine = make_server_engine()
    with server_engine.begin() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS {MYSQL_DATABASE} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        ))
    server_engine.dispose()

    engine = create_engine(
        f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    )
    prepare_database(engine)

    counters = {"files_discovered": len(files), "files_processed": 0,
                "sheets_processed": 0, "empty_sheets": 0, "rows_read": 0,
                "rows_inserted": 0, "rows_updated": 0, "rows_unchanged": 0,
                "rows_skipped": 0}
    validation_errors: list[str] = []
    failed_files: dict[str, str] = {}

    for file in files:
        spec = select_metric_spec(file.name)
        if spec is None:
            failed_files[file.name] = "无法识别指标类型"
            continue
        year_match = re.search(r"(20\d{2})", file.name)
        year = int(year_match.group(1))
        logger.info("读取：%s", file.name)
        try:
            workbook = pd.read_excel(file, sheet_name=None, header=None)
            all_records = []
            for sheet_name, df_raw in workbook.items():
                records = process_sheet(df_raw, year, spec, file.name,
                                        sheet_name, counters, validation_errors)
                for rec in records:
                    rec["sheet"] = sheet_name
                all_records.extend(records)

            seen_keys: set[tuple] = set()
            for rec in all_records:
                key = (rec["year"], rec["record_type"], rec["operator_name"],
                       rec["line_name"], rec["region_bureau"])
                if key in seen_keys:
                    raise ValueError(f"业务唯一键在文件内重复：{key}（局区段切分可能异常）")
                seen_keys.add(key)

            with engine.begin() as conn:  # 一个文件一个事务
                write_file_records(conn, spec, file.name, all_records, counters)
            counters["files_processed"] += 1
        except Exception as exc:
            logger.error("处理失败 %s：%s", file.name, exc)
            failed_files[file.name] = f"{type(exc).__name__}: {exc}"

    engine.dispose()
    generate_import_report(counters, failed_files, validation_errors)
    return 1 if failed_files else 0


if __name__ == "__main__":
    sys.exit(main())
