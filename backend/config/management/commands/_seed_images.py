"""
Real image bytes for the seed.

**Not 1x1 test pixels.** Everything in this project that touches an image --
the variant job, the EXIF strip, the content-type sniff, the size cap -- has
only ever met a synthetic one-pixel PNG, which is a file that exercises the
code path and none of its costs. A 4 MB photo from a phone is a different
input: it takes real time to decode, it carries GPS coordinates, and it is the
size at which "resize in a background job" stops being a formality.

The shapes here are the ones landlords actually upload, plus two the code has
to survive:

- a **4 MB phone photo** at 4032x3024, EXIF and GPS attached;
- a **200 kB** one, already sensible;
- a **portrait**, because a gallery laid out for landscape crops faces;
- a **very wide panorama**, which breaks any layout assuming a bounded ratio;
- an **over-compressed** one, to see what the variant job does with a file
  already ruined;
- a **file whose extension lies** -- a PDF named .jpg;
- and a **deliberately bad** one: truncated bytes with a valid header, which
  is what a dropped upload leaves behind.

Generated rather than committed. Binary fixtures in a repository are files
nobody reads in review, and these need to be reproducible from a seed anyway.
"""

from __future__ import annotations

import io
import random

#: What each generated photo is for, so a failure names the case.
PHOTO_SHAPES = (
    "phone_4mb",
    "modest_200kb",
    "portrait",
    "panorama",
    "over_compressed",
    "extension_lies",
    "truncated",
)


def _noise_image(width: int, height: int, seed: int):
    """An image that does not compress away to nothing.

    Per-pixel noise, straight from a seeded RNG into `frombytes`. The first
    attempt used 16-pixel blocks, which encode beautifully -- a "4 MB phone
    photo" at 4032x3024 came out at 392 kB, a fixture lying about the one
    property it exists to have. Noise is not a photograph either, but it is
    incompressible in the same direction a photograph is, which is what makes
    the byte counts real.
    """
    from PIL import Image

    rng = random.Random(seed)  # noqa: S311 - reproducible fixtures, not secrets
    return Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))


def _with_exif(image, *, seed: int) -> bytes:
    """A JPEG carrying EXIF, including GPS.

    The GPS tags are the point. `strip_image_metadata` claims to drop them, and
    that claim has never been tested against a file that has any -- the phone
    photo of a student ID carries the coordinates of wherever it was taken,
    which is usually somebody's home.
    """
    import piexif

    exif = {
        "0th": {
            piexif.ImageIFD.Make: b"SeedPhone",
            piexif.ImageIFD.Model: b"SP-1",
            piexif.ImageIFD.Software: b"seed_platform",
        },
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:03:14 08:15:00"},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"S",
            # Nairobi, roughly. Real coordinates so a leak would be visible as
            # one rather than as an odd number.
            piexif.GPSIFD.GPSLatitude: ((1, 1), (17, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((36, 1), (49, 1), (0, 1)),
        },
        "1st": {},
        "thumbnail": None,
    }

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=42, exif=piexif.dump(exif))
    return buffer.getvalue()


def generate(shape: str, *, seed: int = 0) -> tuple[bytes, str, str]:
    """Bytes, content type and a filename for one shape.

    The filename is part of the fixture: `extension_lies` returns a PDF called
    `.jpg`, because an extension is whatever the client typed and the sniff
    has to be what decides.
    """
    if shape == "phone_4mb":
        # ~4 MB at 4032x3024. The quality figure is lower than a camera would
        # use because pure noise is harder to compress than a photograph; the
        # byte count is what this fixture is for, not the aesthetics.
        return _with_exif(_noise_image(4032, 3024, seed), seed=seed), "image/jpeg", "IMG_2831.jpg"

    if shape == "modest_200kb":
        buffer = io.BytesIO()
        _noise_image(1280, 960, seed + 1).save(buffer, format="JPEG", quality=18)
        return buffer.getvalue(), "image/jpeg", "room.jpg"

    if shape == "portrait":
        buffer = io.BytesIO()
        _noise_image(1080, 1920, seed + 2).save(buffer, format="JPEG", quality=80)
        return buffer.getvalue(), "image/jpeg", "doorway.jpg"

    if shape == "panorama":
        buffer = io.BytesIO()
        _noise_image(5000, 900, seed + 3).save(buffer, format="JPEG", quality=75)
        return buffer.getvalue(), "image/jpeg", "compound.jpg"

    if shape == "over_compressed":
        buffer = io.BytesIO()
        # Quality 4: already ruined before we touch it. What the variant job
        # does with this is worth knowing, because re-encoding it can only
        # make it worse and the original may be the better artefact.
        _noise_image(1600, 1200, seed + 4).save(buffer, format="JPEG", quality=4)
        return buffer.getvalue(), "image/jpeg", "blurry.jpg"

    if shape == "extension_lies":
        # A real PDF header. Named .jpg, which is what the client says.
        return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 2048, "application/pdf", "room.jpg"

    if shape == "truncated":
        # A valid PNG header and then nothing. What a dropped upload leaves.
        buffer = io.BytesIO()
        _noise_image(800, 600, seed + 5).save(buffer, format="PNG")
        whole = buffer.getvalue()
        return whole[: len(whole) // 3], "image/png", "cut-short.png"

    raise ValueError(f"unknown photo shape: {shape}")
