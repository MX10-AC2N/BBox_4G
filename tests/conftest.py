"""Test configuration, plus an optional human-readable test report.

Set ``BBOX_TEST_REPORT=1`` to write ``docs/TEST_REPORT.md`` (English) and
``docs/TEST_REPORT_french.md`` after the run. The text of each line comes from
the docstring of the test: first line(s) in English, then a line starting with
``FR:`` in French.
"""

import datetime as dt
import json
import os
import platform
import time
from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

REPO = "MX10-AC2N/BBox_4G"

AREAS = {
    "test_parental.py": (
        "Parental control, services and options",
        "Contrôle parental, services et options",
    ),
    "test_dashboards.py": ("Dashboards", "Tableaux de bord"),
}


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations):
    yield


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_INFO: dict[str, dict] = {}
_RESULTS: dict[str, dict] = {}
_STARTED = time.monotonic()


def _split(doc: str | None) -> tuple[str, str]:
    lines = [ln.strip() for ln in (doc or "").strip().splitlines() if ln.strip()]
    fr = " ".join(ln[3:].strip() for ln in lines if ln.startswith("FR:"))
    en = " ".join(ln for ln in lines if not ln.startswith("FR:"))
    return en, fr


def pytest_collection_modifyitems(items):
    for item in items:
        en, fr = _split(getattr(item.obj, "__doc__", ""))
        _INFO[item.nodeid] = {
            "file": Path(item.fspath).name,
            "name": item.name,
            "en": en or item.name,
            "fr": fr or en or item.name,
        }


def pytest_runtest_logreport(report):
    if report.nodeid not in _INFO:
        return
    entry = _RESULTS.setdefault(
        report.nodeid, {"outcome": "passed", "duration": 0.0, "message": ""}
    )
    entry["duration"] += report.duration
    failed = report.failed or (report.when == "call" and report.outcome == "failed")
    if failed:
        entry["outcome"] = "failed"
        crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
        entry["message"] = (getattr(crash, "message", "") or str(report.longrepr))[:300]
    elif report.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"


def _versions() -> dict[str, str]:
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads(
        (root / "custom_components" / "bbox_4g" / "manifest.json").read_text()
    )
    try:
        from homeassistant.const import __version__ as ha_version
    except Exception:  # pragma: no cover
        ha_version = "?"
    return {
        "integration": manifest.get("version", "?"),
        "ha": ha_version,
        "python": platform.python_version(),
    }


TEXT = {
    "en": {
        "title": "Test report",
        "other": "Test report in French: [TEST_REPORT_french.md](TEST_REPORT_french.md)",
        "ok": "✅ **All {n} tests passed**",
        "ko": "❌ **{bad} of {n} tests failed**",
        "date": "Date (UTC)",
        "integ": "Integration version",
        "ha": "Home Assistant used for the tests",
        "py": "Python",
        "dur": "Duration",
        "intro": (
            "These tests run automatically on every push "
            f"([GitHub Actions](https://github.com/{REPO}/actions/workflows/validate.yml)). "
            "Each one starts a real Home Assistant in memory, loads the integration and "
            "talks to a **simulated router**, with a simulated clock so that nights, "
            "quotas and timers can be checked in seconds."
        ),
        "areas": "Summary",
        "area": "Area",
        "passed": "Passed",
        "details": "What is checked",
        "result": "Result",
        "what": "What is checked",
        "test": "Test",
        "time": "Time",
        "fail": "Failures",
        "limits": "What these tests do not cover",
        "limit_items": [
            "The behaviour of a **real router**: login, MAC filtering and Wi-Fi commands are simulated. Only the author's Huawei B528s has been used for real; Sagemcom Bbox routers are not validated.",
            "The **display in a browser**: dashboards are generated and their templates are rendered, but the cards (including Mushroom and auto-entities) are not drawn on screen.",
            "**Real time**: the clock is simulated, so very long periods (several days in a row, restarts at a particular moment) are only partly covered.",
        ],
        "ok_mark": "✅",
        "ko_mark": "❌",
        "skip_mark": "⏭️",
    },
    "fr": {
        "title": "Rapport de tests",
        "other": "Rapport de tests en anglais : [TEST_REPORT.md](TEST_REPORT.md)",
        "ok": "✅ **Les {n} tests ont réussi**",
        "ko": "❌ **{bad} test(s) sur {n} en échec**",
        "date": "Date (UTC)",
        "integ": "Version de l'intégration",
        "ha": "Home Assistant utilisé pour les tests",
        "py": "Python",
        "dur": "Durée",
        "intro": (
            "Ces tests s'exécutent automatiquement à chaque envoi "
            f"([GitHub Actions](https://github.com/{REPO}/actions/workflows/validate.yml)). "
            "Chacun démarre un vrai Home Assistant en mémoire, charge l'intégration et "
            "dialogue avec un **routeur simulé**, avec une horloge simulée pour vérifier "
            "en quelques secondes les nuits, les quotas et les minuteurs."
        ),
        "areas": "Résumé",
        "area": "Domaine",
        "passed": "Réussis",
        "details": "Ce qui est vérifié",
        "result": "Résultat",
        "what": "Ce qui est vérifié",
        "test": "Test",
        "time": "Durée",
        "fail": "Échecs",
        "limits": "Ce que ces tests ne couvrent pas",
        "limit_items": [
            "Le comportement d'un **vrai routeur** : connexion, filtrage MAC et commandes Wi-Fi sont simulés. Seul le Huawei B528s de l'auteur a été utilisé en réel ; les Bbox Sagemcom ne sont pas validées.",
            "L'**affichage dans un navigateur** : les tableaux de bord sont générés et leurs modèles sont calculés, mais les cartes (dont Mushroom et auto-entities) ne sont pas dessinées à l'écran.",
            "Le **temps réel** : l'horloge est simulée, donc les très longues durées (plusieurs jours de suite, redémarrages à un moment précis) ne sont couvertes qu'en partie.",
        ],
        "ok_mark": "✅",
        "ko_mark": "❌",
        "skip_mark": "⏭️",
    },
}


def _render(lang: str, rows: list[dict], total_s: float) -> str:
    t = TEXT[lang]
    v = _versions()
    total = len(rows)
    bad = sum(1 for r in rows if r["outcome"] == "failed")
    head = t["ok"].format(n=total) if bad == 0 else t["ko"].format(bad=bad, n=total)
    out = [
        f"# {t['title']}",
        "",
        t["other"],
        "",
        head,
        "",
        f"| | |\n|---|---|\n"
        f"| {t['date']} | {dt.datetime.now(dt.timezone.utc):%Y-%m-%d} |\n"
        f"| {t['integ']} | {v['integration']} |\n"
        f"| {t['ha']} | {v['ha']} |\n"
        f"| {t['py']} | {v['python']} |\n"
        f"| {t['dur']} | {total_s:.1f} s |",
        "",
        t["intro"],
        "",
        f"## {t['areas']}",
        "",
        f"| {t['area']} | {t['passed']} |",
        "|---|---|",
    ]
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    idx = 0 if lang == "en" else 1
    for file, items in by_file.items():
        label = AREAS.get(file, (file, file))[idx]
        ok = sum(1 for r in items if r["outcome"] == "passed")
        out.append(f"| {label} | {ok} / {len(items)} |")
    out += ["", f"## {t['details']}", ""]
    for file, items in by_file.items():
        label = AREAS.get(file, (file, file))[idx]
        out += [
            f"### {label}",
            "",
            f"| {t['result']} | {t['what']} | {t['test']} | {t['time']} |",
            "|:-:|---|---|--:|",
        ]
        for r in items:
            mark = {"passed": t["ok_mark"], "failed": t["ko_mark"]}.get(
                r["outcome"], t["skip_mark"]
            )
            text = (r["en"] if lang == "en" else r["fr"]).replace("|", "\\|")
            out.append(f"| {mark} | {text} | `{r['name']}` | {r['duration']:.2f} s |")
        out.append("")
    failures = [r for r in rows if r["outcome"] == "failed"]
    if failures:
        out += [f"## {t['fail']}", ""]
        for r in failures:
            out.append(f"- `{r['name']}` — {r['message'].splitlines()[0] if r['message'] else ''}")
        out.append("")
    out += [f"## {t['limits']}", ""]
    out += [f"- {item}" for item in t["limit_items"]]
    out.append("")
    return "\n".join(out)


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("BBOX_TEST_REPORT") != "1" or not _RESULTS:
        return
    rows = []
    for nodeid, info in _INFO.items():
        res = _RESULTS.get(nodeid)
        if res:
            rows.append({**info, **res})
    docs = Path(session.config.rootpath) / "docs"
    docs.mkdir(exist_ok=True)
    total_s = time.monotonic() - _STARTED
    (docs / "TEST_REPORT.md").write_text(_render("en", rows, total_s), encoding="utf-8")
    (docs / "TEST_REPORT_french.md").write_text(_render("fr", rows, total_s), encoding="utf-8")
