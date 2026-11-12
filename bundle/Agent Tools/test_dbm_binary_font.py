#!/usr/bin/env python3
"""Font rasterization, error detection, and real OCR pipeline regressions."""

from itertools import combinations
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

import dbm_binary_font as binary
import ocr_logs

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "DBM-Retail/DBM-Core/fonts/DBMBinary.ttf"
ASCII = "".join(chr(value) for value in range(32, 127))
EVENT = ("23.45: ENCOUNTER_TIMELINE_EVENT_ADDED: fired for eventID: 12 with spellID: "
         "1234567 with spellName: A Test and duration: 31.965 (Rounded: 32) and state: 0")


def render(path: Path, lines: list[str], size: int = 24, color="white") -> Image.Image:
    font = ImageFont.truetype(str(FONT), size)
    framed = [binary.START + line + binary.END
              for line in [f"{binary.PAGE_HEADER}{len(lines)}", *lines]]
    width = max(int(font.getlength(line)) for line in framed) + 60
    image = Image.new("RGB", (width, (size + 8) * len(framed) + 40), "black")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(framed):
        draw.text((20, 20 + index * (size + 8)), line, font=font, fill=color)
    image.save(path)
    return image


class CodeTests(unittest.TestCase):
    def test_all_ascii_and_single_double_bit_errors(self):
        for value in range(32, 127):
            bits = binary.codeword(value)
            self.assertEqual(binary.decode_word(bits), chr(value))
            for count in (1, 2):
                for indices in combinations(range(12), count):
                    damaged = list(bits)
                    for index in indices:
                        damaged[index] ^= 1
                    with self.assertRaises(binary.BinaryDecodeError):
                        binary.decode_word(tuple(damaged))

    def test_unsupported_values(self):
        for value in (0, 10, 31, 127):
            with self.assertRaises(binary.BinaryDecodeError):
                binary.decode_word(binary.codeword(value))

    def test_font_rebuild_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "font.ttf"
            binary.build_font(output)
            self.assertEqual(output.read_bytes(), FONT.read_bytes())


class RasterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "capture.png"

    def test_printable_ascii_spaces_case_and_multiline(self):
        for size in (12, 24, 36, 48):
            with self.subTest(size=size):
                lines = [ASCII, "  leading and trailing spaces  ", EVENT]
                render(self.path, lines, size)
                self.assertEqual(binary.decode_image(self.path, required=True), lines)

    def test_native_color_spans_and_lossy_capture(self):
        for color in ("yellow", "#69CCF0", "#D8B4FE", "white"):
            with self.subTest(color=color):
                image = render(self.path, [EVENT], color=color)
                jpeg = self.path.with_suffix(".jpg")
                image.save(jpeg, quality=90)
                self.assertEqual(binary.decode_image(jpeg, required=True), [EVENT])

    def test_wrong_font_and_empty_capture(self):
        image = Image.new("RGB", (800, 100), "black")
        ImageDraw.Draw(image).text((20, 20), EVENT, fill="white")
        image.save(self.path)
        self.assertIsNone(binary.decode_image(self.path))
        with self.assertRaises(binary.BinaryDecodeError):
            binary.decode_image(self.path, required=True)

    def test_clipping_and_missing_prefix_are_errors(self):
        image = render(self.path, [EVENT])
        for cropped in (image.crop((0, 0, image.width - 80, image.height)),
                        image.crop((150, 0, image.width, image.height))):
            cropped.save(self.path)
            with self.assertRaises(binary.BinaryDecodeError):
                binary.decode_image(self.path, required=True)

    def test_bad_cell_is_not_repaired(self):
        image = render(self.path, ["A"])
        # Size 24: 2 pixels/cell, 16 pixels/glyph, ink starts at y=24.
        index = len(binary.START)
        x = 20 + index * 16 + 4
        y = 24 + 24 + 8
        ImageDraw.Draw(image).rectangle((x, y, x + 1, y + 1), fill="white")
        image.save(self.path)
        with self.assertRaises(binary.BinaryDecodeError):
            binary.decode_image(self.path, required=True)

    def test_pipeline_preserves_exact_binary_and_three_pass_files(self):
        render(self.path, [EVENT])
        with patch.object(ocr_logs.subprocess, "run", side_effect=AssertionError("Tesseract on binary")):
            lines = ocr_logs.ocr_folder(str(self.path.parent), capture_format="binary")
        self.assertIn(EVENT, lines)
        self.assertIn(ocr_logs.BINARY_CACHE_MARKER, lines)
        for filename in ocr_logs.LOG_VARIANT_FILENAMES:
            self.assertIn(EVENT, (self.path.parent / filename).read_text())

    def test_mixed_pages_use_real_english_tesseract_and_keep_binary_exact(self):
        render(self.path, [EVENT])
        text_image = Image.new("RGB", (1800, 120), "black")
        font = ImageFont.load_default(size=24)
        line = "24.00: ENCOUNTER_WARNING: spellID: 1234567 severity: 1"
        ImageDraw.Draw(text_image).text((20, 25), line, font=font, fill="white")
        text_image.save(self.path.parent / "page2.png")
        output = ocr_logs.ocr_folder(str(self.path.parent))
        self.assertIn(EVENT, output)
        self.assertTrue(any("ENCOUNTER_WARNING" in item and "1234567" in item for item in output))

    def test_failed_force_keeps_reviewed_canonical_cache(self):
        image = render(self.path, [EVENT])
        image.crop((0, 0, image.width - 50, image.height)).save(self.path)
        cache = self.path.parent / ocr_logs.LOG_FILENAME
        cache.write_text("reviewed canonical evidence\n")
        with self.assertRaises(binary.BinaryDecodeError):
            ocr_logs.process_folders([str(self.path.parent)], force=True, capture_format="binary")
        self.assertEqual(cache.read_text(), "reviewed canonical evidence\n")
        self.assertFalse((self.path.parent / "log1.txt").exists())

    def test_binary_sections_bypass_english_repairs(self):
        exact = "24.00: ENCOUNTER_WARNING: spellID: 1234567 spellName: Rouncied"
        lines = ["=== a.png ===", ocr_logs.BINARY_CACHE_MARKER, exact,
                 "=== b.png ===", "25.00: ENCOUNTER_WARNING: spellID: 1234567"]
        processed, _ = ocr_logs._postprocess_capture_lines(lines)
        self.assertEqual(processed, lines)

    def test_normalize_preserves_binary_but_still_repairs_english(self):
        exact = "24.00: ENCOUNTER_WARNING: spellID: 1234567 spellName: Rouncied"
        cache = self.path.parent / ocr_logs.LOG_FILENAME
        cache.write_text("\n".join([
            "=== binary.png ===", ocr_logs.BINARY_CACHE_MARKER, exact,
            "=== english.png ===",
            "25.00: ENCOUNTER_WARNING: spellID: 1234567 (Rouncied: 32)",
        ]) + "\n")
        with patch.object(ocr_logs, "LOG_ROOTS", (str(self.path.parent),)):
            ocr_logs.normalize_cached_logs()
        output = cache.read_text()
        self.assertIn(exact, output)
        self.assertIn("(Rounded: 32)", output)

    def test_text_mode_works_without_python_site_packages(self):
        image = Image.new("RGB", (1600, 100), "black")
        ImageDraw.Draw(image).text(
            (20, 20), "24.00: ENCOUNTER_WARNING: spellID: 1234567 severity: 1",
            font=ImageFont.load_default(size=24), fill="white",
        )
        image.save(self.path)
        result = subprocess.run([
            sys.executable, "-S", str(ROOT / "Agent Tools/ocr_logs.py"),
            "--folder", str(self.path.parent), "--capture-format", "text",
        ], capture_output=True, text=True, check=True)
        self.assertIn("done", result.stdout)
        self.assertIn("ENCOUNTER_WARNING", (self.path.parent / "log.txt").read_text())

    def test_partial_or_missing_body_row_cannot_pass_as_complete_page(self):
        image = render(self.path, ["first row", "last row"])
        # Retain the header and first row but remove all/most of the last row.
        for bottom in (80, 90):
            image.crop((0, 0, image.width, bottom)).save(self.path)
            with self.assertRaises(binary.BinaryDecodeError):
                binary.decode_image(self.path, required=True)


if __name__ == "__main__":
    unittest.main()
