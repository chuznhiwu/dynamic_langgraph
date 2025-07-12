"""
将 txt / docx / pdf 转 Markdown，并对文档中的图片做 OCR，
把识别出的文字一并写入 .md 文件。

环境依赖：
    - pandoc
    - tesseract-ocr
    - pip 包：pypandoc python-docx pdfminer.six pytesseract pillow pdf2image
"""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from typing import List

import pytesseract
from PIL import Image

from pdf2image import convert_from_path  # PDF → image

from dynamic_langgraph import config

# ---------- 内部工具 ----------


def _pandoc_convert(src: Path, dst: Path) -> None:
    try:
        subprocess.run(
            ["pandoc", "-f", src.suffix.lstrip("."), "-t", "markdown",
             str(src), "-o", str(dst)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        # ---- 如果是 PDF，再试 pdfminer ----
        if src.suffix.lower() == ".pdf":
            from pdfminer.high_level import extract_text
            try:
                text = extract_text(str(src))
                dst.write_text(text or "[空 PDF]", encoding="utf-8")
                return                       # 成功兜底
            except Exception:
                pass
        # ---- 最终失败：抛更友好的信息 ----
        raise RuntimeError(
            f"pandoc 转换失败（exit {e.returncode}）:\n{e.stderr.strip()}"
        ) from None


def _ocr_image(img: Image.Image) -> str:
    """对单张 PIL.Image 执行 OCR，返回纯文本"""
    text = pytesseract.image_to_string(img, lang="chi_sim+eng")
    return text.strip()


def _extract_images_from_docx(src: Path, workdir: Path) -> List[Path]:
    """
    从 docx 中解包 media 目录里的图片，返回图片路径数组
    """
    import zipfile

    output_images = []
    with zipfile.ZipFile(src) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/") and name.lower().endswith((".png", ".jpg", ".jpeg")):
                img_data = zf.read(name)
                out_path = workdir / f"{uuid.uuid4().hex}{Path(name).suffix}"
                with out_path.open("wb") as f:
                    f.write(img_data)
                output_images.append(out_path)
    return output_images


def _extract_images_from_pdf(src: Path, workdir: Path) -> List[Path]:
    """
    把每页 PDF 渲染成一张 PNG（300 DPI），返回图片路径数组
    """
    pages = convert_from_path(str(src), dpi=300)
    out_paths = []
    for i, page in enumerate(pages):
        out_path = workdir / f"{uuid.uuid4().hex}_p{i}.png"
        page.save(out_path, "PNG")
        out_paths.append(out_path)
    return out_paths


# ---------- 对外主函数 ----------


def convert_to_markdown_with_ocr(src_path: str) -> tuple[str, List[str]]:
    """
    转换文件为 Markdown，并对图片 OCR。
    返回：
        md_path: 生成的 .md 文件绝对路径
        snippets: OCR 得到的文字块列表（可插入或附录到 md）
    """
    src = Path(src_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)

    out_dir = Path(config.ROOT) / "markdown"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_md = out_dir / (src.stem + ".md")

    # 1️⃣ 先用 pandoc 或复制生成初始 Markdown -------------------
    ext = src.suffix.lower()
    if ext in {".md", ".markdown", ".txt"}:
        shutil.copy2(src, dst_md)
    elif ext in {".doc", ".docx", ".pdf"}:
        _pandoc_convert(src, dst_md)
    else:
        raise ValueError(f"暂不支持 {ext} 文件")

    # 2️⃣ 提取图片并 OCR -----------------------------------------
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        img_paths: List[Path] = []

        if ext in {".doc", ".docx"}:
            img_paths = _extract_images_from_docx(src, workdir)
        elif ext == ".pdf":
            img_paths = _extract_images_from_pdf(src, workdir)
        # txt / md 不含嵌入图片，不处理

        snippets: List[str] = []
        for p in img_paths:
            try:
                with Image.open(p) as im:
                    txt = _ocr_image(im)
                    if txt:
                        snippets.append(txt)
            except Exception as e:
                snippets.append(f"[OCR 失败: {p.name} - {e}]")

    # 3️⃣ 把 OCR 文字追加到 Markdown 末尾 ------------------------
    if snippets:
        with dst_md.open("a", encoding="utf-8") as f:
            f.write("\n\n---\n### 🖼 OCR 提取文字\n")
            for i, snip in enumerate(snippets, 1):
                f.write(f"\n**图 {i}**\n\n> {snip}\n")

    return str(dst_md), snippets
