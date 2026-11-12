#!/usr/bin/env python3
"""DBM Binary v1: native ASCII glyphs, extended Hamming parity, and image decoding."""

from dataclasses import dataclass
from pathlib import Path
import argparse
from itertools import product

START = "~DBM1~"
END = "~END~"
PAGE_HEADER = "#DBMP1:"
DATA_POSITIONS = (3, 5, 6, 7, 9, 10, 11)
ADVANCE = 8
EM = 12


class BinaryDecodeError(ValueError):
    """A binary capture is damaged, clipped, or unsupported."""


def codeword(value: int) -> tuple[int, ...]:
    if not 0 <= value < 128:
        raise ValueError("Binary v1 encodes seven-bit ASCII")
    bits = [0] * 13
    for bit, position in enumerate(DATA_POSITIONS):
        bits[position] = (value >> bit) & 1
    for parity in (1, 2, 4, 8):
        bits[parity] = sum(bits[p] for p in range(1, 12) if p & parity) % 2
    bits[12] = sum(bits[1:12]) % 2
    return tuple(bits[1:])


def decode_word(bits: tuple[int, ...]) -> str:
    if len(bits) != 12 or any(bit not in (0, 1) for bit in bits):
        raise BinaryDecodeError("Invalid binary cell count/value")
    for parity in (1, 2, 4, 8):
        if sum(bits[p - 1] for p in range(1, 12) if p & parity) % 2:
            raise BinaryDecodeError("Hamming parity failure; recapture this row")
    if sum(bits) % 2:
        raise BinaryDecodeError("Overall parity failure; recapture this row")
    value = sum(bits[position - 1] << bit for bit, position in enumerate(DATA_POSITIONS))
    if not 32 <= value <= 126:
        raise BinaryDecodeError("Unsupported/control glyph; Binary v1 requires printable ASCII")
    return chr(value)


def build_font(destination: Path) -> None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    builder = FontBuilder(EM * 100, isTTF=True)
    names = [".notdef"] + [f"ascii{value}" for value in range(32, 127)]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({value: f"ascii{value}" for value in range(32, 127)})
    glyphs = {}
    for name in names:
        value = 127 if name == ".notdef" else int(name[5:])
        pen = TTGlyphPen(None)

        def rectangle(x: int, y: int, width: int, height: int) -> None:
            pen.moveTo((x * 100, y * 100))
            pen.lineTo((x * 100, (y + height) * 100))
            pen.lineTo(((x + width) * 100, (y + height) * 100))
            pen.lineTo(((x + width) * 100, y * 100))
            pen.closePath()

        rectangle(0, 0, 1, 7)
        for index, bit in enumerate(codeword(value)):
            if bit:
                rectangle(2 + 2 * (index % 3), 6 - 2 * (index // 3), 1, 1)
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (ADVANCE * 100, 0) for name in names})
    builder.setupHorizontalHeader(ascent=900, descent=-300, lineGap=0)
    builder.setupNameTable({
        "familyName": "DBM Binary",
        "styleName": "Regular",
        "uniqueFontIdentifier": "DBM Binary v1",
        "fullName": "DBM Binary Regular",
        "psName": "DBMBinary-Regular",
        "version": "Version 1.000",
        "copyright": "Original geometric font. Distributed under the DBM project license.",
    })
    builder.setupOS2(sTypoAscender=900, sTypoDescender=-300, sTypoLineGap=0,
                     usWinAscent=900, usWinDescent=300, fsSelection=0x40,
                     fsType=0, ulCodePageRange1=1, ulCodePageRange2=0)
    builder.setupPost()
    builder.setupMaxp()
    # Stable OpenType timestamps make regeneration reproducible.
    builder.font["head"].created = 3800000000
    builder.font["head"].modified = 3800000000
    builder.font.recalcTimestamp = False
    destination.parent.mkdir(parents=True, exist_ok=True)
    builder.save(str(destination))


@dataclass(frozen=True)
class Rail:
    x: float
    top: int
    height: int


def _rails(image, threshold: int) -> list[Rail]:
    """Find tall locator bars without interpreting ordinary text as bit cells."""
    # The brightest channel allows colored DBM spans; low-luminance blue is valid.
    from PIL import ImageChops
    red, green, blue = image.convert("RGB").split()
    mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(
        lambda value: 255 if value >= threshold else 0
    )
    pixels = mask.load()
    width, height = mask.size
    runs: list[tuple[int, int, int]] = []
    for x in range(width):
        y = 0
        while y < height:
            if not pixels[x, y]:
                y += 1
                continue
            start = y
            while y < height and pixels[x, y]:
                y += 1
            if y - start >= 5:
                runs.append((x, start, y - start))
    # Merge neighboring columns of the same bar (including scaled screenshots).
    bars: list[list[tuple[int, int, int]]] = []
    active: dict[tuple[int, int], int] = {}
    for run in runs:
        x, y, h = run
        key = (y, h)
        previous = active.get(key)
        if previous is not None and bars[previous][-1][0] == x - 1:
            bars[previous].append(run)
        else:
            active[key] = len(bars)
            bars.append([run])
    return [
        Rail((bar[0][0] + bar[-1][0]) / 2, bar[0][1], bar[0][2])
        for bar in bars
        if 4.5 <= bar[0][2] / len(bar) <= 10
    ]


def _calibrate(pixels, run: list[Rail], top: int, height: int, threshold: int):
    """Fit the public prefix, allowing FreeType's fractional-pixel rounding."""
    expected = [codeword(ord(char)) for char in START]
    best = None
    best_score = -1.0
    for delta, dx, dy in product(range(-4, 9), (-0.35, 0.0, 0.35), (-0.35, 0.0, 0.35)):
        unit = (height + delta / 8) / 7
        score = 0.0
        valid = True
        for rail, bits in zip(run, expected):
            for index, bit in enumerate(bits):
                x = round(rail.x + (2 + 2 * (index % 3)) * unit + dx)
                y = round(top + (0.5 + 2 * (index // 3)) * unit - 0.5 + dy)
                try:
                    value = pixels[x, y]
                except IndexError as error:
                    raise BinaryDecodeError("Cropped binary prefix") from error
                if int(value >= threshold) != bit:
                    valid = False
                    break
                score += value if bit else 255 - value
            if not valid:
                break
        if valid and score > best_score:
            best_score = score
            best = (unit, dx, dy)
    if best is None:
        raise BinaryDecodeError(f"Image row y={top}: damaged/missing Binary v1 prefix")
    return best


def _decode_threshold(image, threshold: int, required: bool) -> list[str] | None:
    from PIL import ImageChops
    red, green, blue = image.convert("RGB").split()
    pixels = ImageChops.lighter(ImageChops.lighter(red, green), blue).load()
    width, height = image.size
    groups: dict[tuple[int, int], list[Rail]] = {}
    for rail in sorted(_rails(image, threshold), key=lambda rail: (rail.top, -rail.height, rail.x)):
        key = next(
            (key for key in groups
             if abs(rail.top - key[0]) <= 1
             and abs(rail.top + rail.height - key[0] - key[1]) <= 1),
            (rail.top, rail.height),
        )
        # Colored antialiased rails can lose one edge pixel at a higher threshold.
        # Keep the row's shared geometry; do not shift data-cell sampling per glyph.
        groups.setdefault(key, []).append(rail)
    lines: list[tuple[int, str]] = []
    suspicious = False
    for (top, bar_height), rails in sorted(groups.items()):
        rails.sort(key=lambda rail: rail.x)
        unit = bar_height / 7
        pitch = ADVANCE * unit
        # A regular run of at least the two framing markers is not English text.
        runs: list[list[Rail]] = []
        for rail in rails:
            if not runs or abs(rail.x - runs[-1][-1].x - pitch) > max(1.1, unit * 0.6):
                runs.append([])
            runs[-1].append(rail)
        for run in runs:
            if len(run) < len(START):
                continue
            suspicious = True
            unit, dx, dy = _calibrate(pixels, run, top, bar_height, threshold)
            decoded: list[str] = []
            try:
                for rail in run:
                    bits: list[int] = []
                    for index in range(12):
                        x = round(rail.x + (2 + 2 * (index % 3)) * unit + dx)
                        y = round(top + (0.5 + 2 * (index // 3)) * unit - 0.5 + dy)
                        if not (0 <= x < width and 0 <= y < height):
                            raise BinaryDecodeError("Cropped binary glyph")
                        bits.append(int(pixels[x, y] >= threshold))
                    decoded.append(decode_word(tuple(bits)))
            except BinaryDecodeError as error:
                raise BinaryDecodeError(f"Image row y={top}: {error}") from error
            text = "".join(decoded)
            if not text.startswith(START) or not text.endswith(END):
                raise BinaryDecodeError(
                    f"Image row y={top}: missing Binary v1 framing; "
                    "capture the entire line, including both ends"
                )
            lines.append((top, text[len(START):-len(END)]))
    if not lines:
        if required or suspicious:
            raise BinaryDecodeError("No complete Binary v1 rows; check font, scale, and crop")
        return None
    return [text for _, text in sorted(lines)]


def decode_image(path: str | Path, required: bool = False) -> list[str] | None:
    """Recognize Binary v1, or return None for an ordinary text screenshot.

    No spelling, duration, or timestamp repairs are applied. Three independent
    thresholds must agree; parity failures never become success-shaped text.
    """
    from PIL import Image
    with Image.open(path) as source:
        image = source.convert("RGB")
    results = [_decode_threshold(image, threshold, required) for threshold in (96, 128, 160)]
    if all(result is None for result in results):
        return None
    if results[0] is None or any(result != results[0] for result in results[1:]):
        raise BinaryDecodeError("Binary threshold passes disagree; recapture at a larger font/scale")
    lines = results[0]
    if not lines or not lines[0].startswith(PAGE_HEADER):
        raise BinaryDecodeError("Missing binary page header; capture the full debug panel")
    count = lines[0][len(PAGE_HEADER):]
    if not count.isascii() or not count.isdigit():
        raise BinaryDecodeError("Invalid binary page row count")
    if int(count) != len(lines) - 1:
        raise BinaryDecodeError(
            f"Binary page declares {count} rows but {len(lines) - 1} decoded; "
            "capture the full debug panel"
        )
    return lines[1:]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-font", type=Path, required=True)
    build_font(parser.parse_args().build_font)
