"""Petits utilitaires partagés par les plates-formes."""

from __future__ import annotations

import re
import unicodedata

_SLUG_STRIP = re.compile(r"[^a-z0-9_]+")


def slugify(text: str) -> str:
    """Transforme un nom d'appareil en identifiant d'entité stable.

    Les accents sont translittérés comme le fait Home Assistant pour ses
    ``entity_id`` (« Téléphone » devient « telephone »).
    """
    nettoye = (
        unicodedata.normalize("NFKD", text or "")
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    slug = _SLUG_STRIP.sub("_", nettoye).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "appareil"
