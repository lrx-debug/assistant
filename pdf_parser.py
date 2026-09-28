"""任务一：PDF 转 TXT。

扫描 knowledge/pdf 下全部 PDF，使用 unstructured.partition_pdf 解析
（优先 hi_res 版面分析 + 表格结构识别），按来源文件/页/表格统一格式，
一次性重新生成 knowledge/knowledge.txt（UTF-8）。
"""
import inspect
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from bs4 import BeautifulSoup

# ---------------------------------------------------------------- 配置
BASE_DIR = Path(__file__).resolve().parent
PDF_INPUT_DIR = BASE_DIR / "knowledge" / "pdf"
PDF_OUTPUT_FILE = BASE_DIR / "knowledge" / "knowledge.txt"

PREFERRED_STRATEGY = "hi_res"  # 版面分析 + 表格识别；环境不支持时自动降级 fast
OCR_LANGUAGES = ["chi_sim", "jpn", "eng"]  # 中/日/英
SEPARATOR = "=" * 50

# Windows 常见安装位置：winget 安装后当前 shell 的 PATH 可能尚未刷新
_TESSERACT_FALLBACK_DIRS = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
]
if shutil.which("tesseract") is None:
    for _dir in _TESSERACT_FALLBACK_DIRS:
        if (Path(_dir) / "tesseract.exe").exists():
            os.environ["PATH"] = _dir + os.pathsep + os.environ.get("PATH", "")
            break
# Program Files 下的 tessdata 普通用户不可写，语言包改放用户缓存目录
_USER_TESSDATA = Path.home() / ".cache" / "tessdata"
if _USER_TESSDATA.is_dir() and not os.environ.get("TESSDATA_PREFIX"):
    os.environ["TESSDATA_PREFIX"] = str(_USER_TESSDATA)
# matplotlib 默认配置目录在沙箱中不可写
if not os.environ.get("MPLCONFIGDIR"):
    os.environ["MPLCONFIGDIR"] = str(Path.home() / ".cache" / "matplotlib")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
logger = logging.getLogger("pdf_parser")


# ---------------------------------------------------------------- 文件发现
def discover_pdf_files(pdf_dir: Path) -> list[Path]:
    """按文件名排序返回 .pdf；忽略隐藏文件与 .tmp/.lock 临时文件。"""
    return sorted(
        p
        for p in pdf_dir.glob("*.pdf")
        if not p.name.startswith((".", "~"))
        and p.suffix.lower() == ".pdf"
        and p.suffix not in (".tmp", ".lock")
    )


# ---------------------------------------------------------------- 解析参数
def build_partition_kwargs() -> dict:
    """根据当前安装的 unstructured 版本适配 partition_pdf 参数。"""
    from unstructured.partition.pdf import partition_pdf

    params = inspect.signature(partition_pdf).parameters
    kwargs: dict = {"strategy": PREFERRED_STRATEGY}
    if "infer_table_structure" in params:
        kwargs["infer_table_structure"] = True
    if "languages" in params:
        kwargs["languages"] = OCR_LANGUAGES
    return kwargs


def select_strategy(partition_kwargs: dict) -> tuple[str, dict]:
    """hi_res 需要额外推理依赖；探测一次，不支持则降级 fast 并记录。"""
    from unstructured.partition.pdf import partition_pdf

    probe = next(iter(discover_pdf_files(PDF_INPUT_DIR)), None)
    if probe is None:
        return PREFERRED_STRATEGY, partition_kwargs
    try:
        partition_pdf(filename=str(probe), **partition_kwargs)
        return PREFERRED_STRATEGY, partition_kwargs
    except Exception as exc:  # 依赖缺失/模型不可用等
        logger.warning(
            "hi_res 不可用（%s：%s），降级为 fast 策略，表格结构识别能力受限",
            type(exc).__name__,
            exc,
        )
        fallback = dict(partition_kwargs)
        fallback["strategy"] = "fast"
        fallback.pop("infer_table_structure", None)
        return "fast", fallback


# ---------------------------------------------------------------- 表格格式化
def _clean_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def format_table(element) -> str:
    """Table 元素转为 `列 | 列` 结构；识别失败时保留原始文本。"""
    html = getattr(element.metadata, "text_as_html", None)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        lines = []
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if cells:
                lines.append(" | ".join(_clean_cell(c.get_text(" ")) for c in cells))
        if lines:
            return "[TABLE]\n" + "\n".join(lines)
        logger.warning("表格 HTML 未解析出行列，保留原始文本")
    return "[TABLE]\n" + _clean_cell(str(element))


# ---------------------------------------------------------------- 单文件解析
def parse_pdf(pdf_file: Path, partition_kwargs: dict) -> tuple[list[str], int]:
    """返回该文件的输出行列表与元素个数。"""
    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(filename=str(pdf_file), **partition_kwargs)

    blocks: list[str] = []
    current_page = None
    for element in elements:
        page = getattr(element.metadata, "page_number", None)
        if page != current_page:
            blocks.append(f"[PAGE {page if page is not None else '?'}]")
            current_page = page

        type_name = type(element).__name__
        if type_name == "Table":
            blocks.append(format_table(element))
        else:
            text = _clean_cell(str(element))
            if text:
                blocks.append(("• " if type_name == "ListItem" else "") + text)
    return blocks, len(elements)


# ---------------------------------------------------------------- 输出
def write_knowledge_txt(sections: dict[str, list[str]]) -> None:
    """一次性覆盖生成 knowledge.txt，不同 PDF 之间带来源标识。"""
    parts: list[str] = []
    for source_name, lines in sections.items():
        parts.append(SEPARATOR)
        parts.append(f"SOURCE_FILE: {source_name}")
        parts.append(SEPARATOR)
        parts.append("")
        parts.extend(lines)
        parts.append("")
    PDF_OUTPUT_FILE.write_text("\n".join(parts), encoding="utf-8")


def generate_parse_report(
    discovered: list[Path],
    succeeded: dict[str, int],
    failed: dict[str, str],
) -> None:
    logger.info("%s", SEPARATOR)
    logger.info("PDF Parse Summary")
    logger.info("%s", SEPARATOR)
    logger.info("Files discovered: %d", len(discovered))
    logger.info("Files succeeded : %d", len(succeeded))
    logger.info("Files failed    : %d", len(failed))
    total_elements = sum(succeeded.values())
    logger.info("Elements parsed : %d", total_elements)
    if PDF_OUTPUT_FILE.exists():
        logger.info(
            "Output: %s (%d chars)",
            PDF_OUTPUT_FILE,
            len(PDF_OUTPUT_FILE.read_text(encoding="utf-8")),
        )
    if failed:
        logger.error("Failed files:")
        for name, reason in failed.items():
            logger.error("  %s -> %s", name, reason)


# ---------------------------------------------------------------- 主流程
def main() -> int:
    if not PDF_INPUT_DIR.is_dir():
        logger.error("PDF 目录不存在：%s", PDF_INPUT_DIR)
        return 1

    pdf_files = discover_pdf_files(PDF_INPUT_DIR)
    if not pdf_files:
        logger.error("PDF 目录为空，未找到任何 PDF：%s", PDF_INPUT_DIR)
        return 1

    partition_kwargs = build_partition_kwargs()
    strategy, partition_kwargs = select_strategy(partition_kwargs)
    logger.info("使用解析策略：%s，待处理 PDF：%d 个", strategy, len(pdf_files))

    sections: dict[str, list[str]] = {}
    succeeded: dict[str, int] = {}
    failed: dict[str, str] = {}

    for pdf_file in pdf_files:
        logger.info("解析：%s", pdf_file.name)
        try:
            blocks, element_count = parse_pdf(pdf_file, partition_kwargs)
            sections[pdf_file.name] = blocks
            succeeded[pdf_file.name] = element_count
        except Exception as exc:  # 单文件失败不阻断其他文件
            logger.error("解析失败 %s：%s", pdf_file.name, exc)
            failed[pdf_file.name] = f"{type(exc).__name__}: {exc}"

    write_knowledge_txt(sections)
    generate_parse_report(pdf_files, succeeded, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
