"""Read OOXML relationships, never infer presentation order from slide filenames."""
import posixpath
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from defusedxml import ElementTree as ET

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass
class Slide:
    page: int
    title: str
    text: str
    notes: str
    images: int

    @property
    def content(self):
        return "\n".join(x for x in [self.text, self.notes] if x).strip()


def relationships(z, part):
    folder, name = posixpath.split(part)
    relpath = posixpath.join(folder, "_rels", name + ".rels")
    if relpath not in z.namelist():
        return {}
    return {
        x.attrib["Id"]: (x.attrib["Type"], posixpath.normpath(
            posixpath.join(folder, x.attrib["Target"]).lstrip("/")
        ))
        for x in ET.fromstring(z.read(relpath))
        if x.attrib.get("TargetMode") != "External"
    }


def paragraphs(root):
    return "\n".join(
        text for p in root.findall(".//a:p", NS)
        if (text := "".join(t.text or "" for t in p.findall(".//a:t", NS)).strip())
    )


def parse_pptx(path: Path) -> list[Slide]:
    with ZipFile(path) as z:
        if sum(x.file_size for x in z.infolist()) > 1024**3:
            raise ValueError("PPTX uncompressed size exceeds limit")
        rels = relationships(z, "ppt/presentation.xml")
        root = ET.fromstring(z.read("ppt/presentation.xml"))
        pages = []
        for i, ref in enumerate(root.findall("p:sldIdLst/p:sldId", NS), 1):
            part = rels[ref.attrib[f"{{{NS['r']}}}id"]][1]
            slide = ET.fromstring(z.read(part))
            text = paragraphs(slide)
            title = ""
            for shape in slide.findall(".//p:sp", NS):
                ph = shape.find(".//p:ph", NS)
                if ph is not None and ph.attrib.get("type") in ("title", "ctrTitle"):
                    title = paragraphs(shape)
                    break
            notes = ""
            for kind, target in relationships(z, part).values():
                if kind.endswith("/notesSlide"):
                    nr = ET.fromstring(z.read(target))
                    notes = "\n".join(
                        paragraphs(sp) for sp in nr.findall(".//p:sp", NS)
                        if (ph := sp.find(".//p:ph", NS)) is None
                        or ph.attrib.get("type") not in {"sldNum", "hdr", "ftr", "dt", "sldImg"}
                    ).strip()
            pages.append(Slide(i, title or text.split("\n")[0][:120], text, notes,
                               len(slide.findall(".//p:pic", NS))))
        return pages


def chunks(text: str, size=1200, overlap=150):
    if not text.strip():
        return []
    result, start = [], 0
    while start < len(text):
        result.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return result
