import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from knowledge_workflow.build import build
from knowledge_workflow.config import KnowledgeConfig, SourceRoot
from knowledge_workflow.service import KnowledgeService
from knowledge_workflow.storage import Store
from knowledge_workflow.util import atomic_json
from support import Fixture


def text_pdf(path, pages):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(400, 300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 20 260 Td (" + text + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


class PDF(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.documents = self.f.root / "pdf-sources"
        self.documents.mkdir()
        original = self.f.config
        self.config = KnowledgeConfig(original.config_path, original.kb_id, original.root, original.cache,
            original.model_dir, original.sources + (SourceRoot("pdf", self.documents),))
        atomic_json(self.config.config_path, self.config.json())

    def test_original_pdf_text_and_page_are_readable(self):
        text_pdf(self.documents / "manual.pdf", ["The fan stops before the access panel opens.", "The calibration cycle lasts seven minutes."])
        build(self.config, lexical_only=True)
        service = KnowledgeService(self.config)
        self.addCleanup(service.close)
        answer = service.search("calibration seven minutes", mode="lexical")
        evidence = service.read_evidence(answer["hits"][0]["evidence_id"])
        self.assertIn("seven minutes", evidence["body"])
        self.assertEqual(str(evidence["page"]), "2")
        self.assertTrue(evidence["source_version"]["extractor"].startswith("pypdf-"))

    def test_scan_only_and_damaged_pdf_cannot_replace_good_index(self):
        self.f.capture()
        build(self.config, lexical_only=True)
        before = Store(self.config).current.read_bytes()
        scan = PdfWriter()
        scan.add_blank_page(300, 200)
        target = self.documents / "scan.pdf"
        with target.open("wb") as output:
            scan.write(output)
        with self.assertRaisesRegex(ValueError, "requires_ocr"):
            build(self.config, lexical_only=True)
        self.assertEqual(Store(self.config).current.read_bytes(), before)
        target.write_bytes(b"invalid PDF payload")
        with self.assertRaises(Exception):
            build(self.config, lexical_only=True)
        self.assertEqual(Store(self.config).current.read_bytes(), before)
