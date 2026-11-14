import json
import re
from homeassistant.helpers.template import Template
from custom_components.bbox_4g import dashboard as D
from .test_parental import FakeClient, setup, tick, ent, settle


def walk(o):
    if isinstance(o, dict):
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
    elif isinstance(o, str):
        yield o


async def test_dashboards(hass, freezer):
    """The three dashboards (native, auto, Mushroom) contain the parental-control section, only reference entities that really exist, and every template renders in Home Assistant's real template engine.
    FR: Les trois tableaux de bord (natif, auto, Mushroom) contiennent la section de contrôle parental, ne citent que des entités qui existent, et chaque modèle s'affiche dans le vrai moteur de modèles de Home Assistant.
    """
    c = FakeClient()
    hass.config.language = "fr"
    entry = await setup(hass, freezer, c, options={"dashboards": []})
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await tick(hass, freezer, 6)
    reg_ids = set(hass.states.async_entity_ids())
    for kind in ("native", "mushroom", "auto"):
        cfg = D._config_for(hass, kind)
        text = json.dumps(cfg, ensure_ascii=False)
        assert "Contrôle parental" in text and "Léa" in text, kind
        # every entity id referenced exists
        import re
        for m in set(re.findall(r"\b(?:switch|sensor|binary_sensor|button|time|number)\.[a-z0-9_]+", text)):
            assert m == "button.press" or m in reg_ids, (kind, m)
        n = 0
        for s in walk(cfg):
            if "{{" in s or "{%" in s:
                out = Template(s, hass).async_render(parse_result=False)
                n += 1
                if "mushroom" == kind and "Léa" in s or "min" in s:
                    print("RENDER", kind, repr(out))
        print(kind, "templates ok:", n)
    cfg = D._config_for(hass, "mushroom")
    sec = cfg["views"][0]["sections"]
    assert [s["cards"][0].get("heading") or s["cards"][0].get("title") for s in sec][-1] == "Contrôle parental"
    # saved through the real storage path
    assert await D.async_create_storage_dashboard(hass, "native") == "new"


async def test_sections_are_exclusive_and_texts_render(hass, freezer):
    """A device that is cut off appears only under "blocked devices", never also under "connected devices"; the texts and counters of the cards render correctly.
    FR: Un appareil coupé n'apparaît que dans « Appareils bloqués », jamais aussi dans « Appareils connectés » ; les textes et compteurs des cartes s'affichent correctement.
    """
    hass.config.language = "fr"
    c = FakeClient()
    entry = await setup(hass, freezer, c)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    cfg = D._config_for(hass, "mushroom")
    secs = cfg["views"][0]["sections"]

    def check(cond):
        """Frontend semantics of conditional-card conditions."""
        kind = cond.get("condition", "state")
        if kind == "or":
            return any(check(x) for x in cond["conditions"])
        if kind == "and":
            return all(check(x) for x in cond["conditions"])
        state = hass.states.get(cond["entity"]).state
        if "state_not" in cond:
            return state != cond["state_not"]
        return state == cond["state"]

    async def shown(card):
        return all(check(c_) for c_ in card["conditions"])

    def device_cards(sec):
        return [x for x in sec["cards"][1:] if x["type"] == "conditional" and x["card"]["type"] == "vertical-stack"]

    async def titles():
        out = {}
        for i, name in ((1, "connected"), (2, "blocked")):
            out[name] = [x["card"]["cards"][0]["entity"] for x in device_cards(secs[i]) if await shown(x)]
        return out

    # daytime: everybody connected and allowed
    t = await titles()
    assert len(t["connected"]) == 2 and not set(t["connected"]) & set(t["blocked"]), t
    # night: parental control cuts Léa's devices -> they move to "blocked"
    await tick(hass, freezer, 6)
    # the engine asks for a refresh; the test clock is frozen, so do it by hand
    await hass.data["bbox_4g"][entry.entry_id]["coordinator"].async_refresh()
    await settle(hass)
    t = await titles()
    assert not set(t["connected"]) & set(t["blocked"]), t
    assert len(t["blocked"]) >= 2, t
    # the counters and the header summary render
    from homeassistant.helpers.template import Template
    title = secs[1]["cards"][0]["title"]
    n = Template(title, hass).async_render(parse_result=False)
    assert n.startswith("Appareils connectés (") and n.endswith(")"), n
    status_card = D._config_for(hass, "mushroom")["views"][0]["sections"][3]["cards"][1]["cards"][0]
    sec = Template(status_card["secondary"], hass).async_render(parse_result=False)
    print("SECONDARY", sec)
    assert "Semaine 21:00–07:00" in sec and "Week-end 22:00–08:00" in sec, sec


async def test_left_table_renders(hass, freezer):
    """The "time left today" table of each profile renders with one line per device ("Illimité" = unlimited, when there is no quota).
    FR: Le tableau « Temps restant aujourd'hui » de chaque profil s'affiche avec une ligne par appareil (« Illimité » quand il n'y a pas de quota).
    """
    hass.config.language = "fr"
    from homeassistant.helpers.template import Template
    await setup(hass, freezer, FakeClient())
    for kind in ("native", "mushroom", "auto"):
        cfg = D._config_for(hass, kind)
        tables = [s for s in walk(cfg) if s.startswith("| Temps restant aujourd'hui")]
        assert len(tables) == 1, kind
        out = Template(tables[0], hass).async_render(parse_result=False)
        assert "Tel Léa | Illimité |" in out and "PC Léa | Illimité |" in out, out
