from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docling_poc_api import convert_pdf
from routing_pipeline import RoutedPdfOptions
from settings import AppSettings


class DoclingPocApiTests(unittest.TestCase):
    def test_convert_pdf_accepts_input_pdf_path_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "input.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            with patch(
                "docling_poc_api.run_routed_pdf",
                return_value={"ok": True},
            ) as run_mock:
                result = convert_pdf(
                    input_pdf=pdf_path,
                    settings=AppSettings(),
                    run_id="api-test",
                )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(run_mock.call_args.args[0], pdf_path)
        self.assertEqual(run_mock.call_args.kwargs["run_id"], "api-test")
        self.assertIsInstance(run_mock.call_args.kwargs["options"], RoutedPdfOptions)
        self.assertEqual(
            run_mock.call_args.kwargs["options"].invocation,
            "python_api",
        )

    def test_convert_pdf_accepts_input_pdf_bytes_argument(self) -> None:
        observed: dict[str, object] = {}

        def fake_run(pdf_path: Path, **kwargs: object) -> dict[str, object]:
            observed["name"] = pdf_path.name
            observed["content"] = pdf_path.read_bytes()
            observed["options"] = kwargs["options"]
            return {"ok": True}

        with patch("docling_poc_api.run_routed_pdf", side_effect=fake_run):
            result = convert_pdf(
                input_pdf=b"%PDF-1.4\n",
                filename="sample.pdf",
                settings=AppSettings(),
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(observed["name"], "uploaded.pdf")
        self.assertEqual(observed["content"], b"%PDF-1.4\n")
        self.assertIsInstance(observed["options"], RoutedPdfOptions)


if __name__ == "__main__":
    unittest.main()
