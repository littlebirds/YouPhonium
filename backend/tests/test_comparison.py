"""Comparison orchestration regressions; real engines are not needed."""
import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, UploadFile

import main
from services import omr


SCORE = b'''<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note></measure></part></score-partwise>'''


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.job_id = "test-comparison"
        main._upload_jobs[self.job_id] = {"status": "processing", "message": "Starting"}
        self.addCleanup(lambda: main._upload_jobs.pop(self.job_id, None))

    def run_job(self, failed=(), is_image=True):
        source = self.root / ("upload.png" if is_image else "upload.pdf")
        source.write_bytes(b"same input")
        calls = []

        def recognize(path, filename, stored_filename, engine, image, progress):
            self.assertEqual(path.read_bytes(), b"same input")
            self.assertEqual(image, is_image)
            calls.append((engine, stored_filename))
            progress("Recognizing")
            status = main.upload_status(self.job_id)
            self.assertEqual(status["engines"][engine]["message"], "Recognizing")
            self.assertNotIn("result", status["engines"]["audiveris"])
            if engine in failed:
                raise RuntimeError(f"{engine} unavailable")
            return {"success": True, "engine": engine, "musicxml_base64": "abc"}

        with patch.object(main, "_recognize_upload", side_effect=recognize), patch.object(main.log, "exception"):
            main._run_upload_job(self.job_id, source, source.name, "compare", is_image)
        self.assertFalse(source.exists(), "Delete temporary upload only after both engines")
        self.assertEqual([call[0] for call in calls], ["audiveris", "homr"])
        self.assertNotEqual(calls[0][1], calls[1][1])
        for name, stored in calls:
            self.assertIn(f"__{self.job_id}__{name}", stored)
        result = main.upload_status(self.job_id)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["result"]["mode"], "comparison")
        return result["result"]

    def test_both_engines_receive_same_image_and_unique_output_names(self):
        result = self.run_job()
        self.assertTrue(result["success"])
        self.assertTrue(all(entry["status"] == "complete" for entry in result["engines"].values()))

    def test_pdf_comparison_uses_same_orchestration(self):
        self.run_job(is_image=False)

    def test_one_engine_failure_does_not_discard_other(self):
        for failed in ("audiveris", "homr"):
            with self.subTest(failed=failed):
                result = self.run_job(failed=(failed,))
                self.assertTrue(result["success"])
                self.assertEqual(result["engines"][failed]["status"], "error")
                self.assertIn("unavailable", result["engines"][failed]["error"])

    def test_both_failures_are_returned_for_display(self):
        result = self.run_job(failed=("audiveris", "homr"))
        self.assertFalse(result["success"])
        self.assertTrue(all(entry["status"] == "error" for entry in result["engines"].values()))

    def test_upload_accepts_compare_and_schedules_one_job(self):
        upload = UploadFile(filename="score.png", file=io.BytesIO(b"image"))
        upload.read = AsyncMock(return_value=b"image")
        tasks = BackgroundTasks()
        response = asyncio.run(main.upload_pdf(upload, "compare", tasks))
        job_id = json.loads(response.body)["job_id"]
        self.addCleanup(lambda: main._upload_jobs.pop(job_id, None))
        self.assertEqual(len(tasks.tasks), 1)
        task = tasks.tasks[0]
        self.addCleanup(lambda: task.args[1].unlink(missing_ok=True))
        self.assertEqual(task.args[-2:], ("compare", True))

    def test_missing_or_empty_engine_schedules_homr_for_images_and_pdfs(self):
        for engine in (None, "", "homr"):
            for extension in ("png", "pdf"):
                with self.subTest(engine=engine, extension=extension):
                    upload = UploadFile(filename=f"score.{extension}", file=io.BytesIO(b"input"))
                    upload.read = AsyncMock(return_value=b"input")
                    tasks = BackgroundTasks()
                    response = asyncio.run(main.upload_pdf(upload, engine, tasks))
                    job_id = json.loads(response.body)["job_id"]
                    self.addCleanup(lambda job_id=job_id: main._upload_jobs.pop(job_id, None))
                    task = tasks.tasks[0]
                    self.addCleanup(lambda path=task.args[1]: path.unlink(missing_ok=True))
                    self.assertEqual(task.args[-2:], ("homr", extension == "png"))

    def test_upload_schema_defaults_to_homr(self):
        schema = main.app.openapi()
        ref = schema["paths"]["/upload"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        body = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        self.assertEqual(body["properties"]["engine"]["default"], "homr")

    def test_default_job_runs_homr_without_automatic_fallback(self):
        source = self.root / "default.png"
        source.write_bytes(b"input")
        result = {"success": True, "engine": "homr", "musicxml_path": "score.musicxml"}
        with patch.object(main, "_recognize_upload", return_value=result) as recognize:
            main._run_upload_job(self.job_id, source, source.name)
        self.assertEqual(recognize.call_args.args[3], "homr")
        self.assertEqual(main.upload_status(self.job_id)["result"]["engine"], "homr")

    def test_notation_survives_playback_failure_and_selects_correct_adapter(self):
        score = self.root / "unique-homr.musicxml"
        score.write_bytes(SCORE)
        for is_image in (True, False):
            with self.subTest(is_image=is_image), \
                 patch.object(main, "image_to_musicxml", return_value=(score, [], [])) as image, \
                 patch.object(main, "pdf_to_musicxml", return_value=(score, [], [])) as pdf, \
                 patch.object(main, "musicxml_to_midi", side_effect=ValueError("Bad rhythm")):
                result = main._recognize_upload(self.root / "input", "source.png", "unique-homr.png", "homr", is_image, lambda _: None)
                self.assertTrue(result["success"])
                self.assertEqual(base64.b64decode(result["musicxml_base64"]), SCORE)
                self.assertEqual(result["midi_base64"], "")
                self.assertEqual(result["playback_error"], "Bad rhythm")
                self.assertEqual(result["musicxml_path"], str(score))
                self.assertEqual(image.call_count, int(is_image))
                self.assertEqual(pdf.call_count, int(not is_image))

    def test_homr_uses_private_image_copy(self):
        from PIL import Image
        source = self.root / "user-score.png"
        Image.new("RGB", (20, 20), "white").save(source)
        original = source.read_bytes()
        output = self.root / "job"

        def recognize(path):
            self.assertNotEqual(path, source)
            self.assertTrue(path.is_relative_to(output))
            path.with_suffix(".musicxml").write_bytes(SCORE)
            return path.with_suffix(".musicxml")

        with patch.object(omr, "_run_homr_on_image", side_effect=recognize):
            result = omr.run_omr_homr_from_images([source], output)
        self.assertEqual(result.read_bytes(), SCORE)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(source.with_suffix(".musicxml").exists())


if __name__ == "__main__":
    unittest.main()
