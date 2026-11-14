"""순수 로직 테스트 (Minecraft, 네트워크 없이 실행). 실행: python -m pytest tests"""
import random
from pathlib import Path

from mcbuildai.builder.order import compress_runs, order_blocks
from mcbuildai.editor.dedupe import distance
from mcbuildai.generator.building import generate
from mcbuildai.generator.classify import auto_label, normalize
from mcbuildai.generator.schematic import read_mcfunction, read_schem, write_mcfunction, write_schem
from mcbuildai.planner.dna import BUILDING_TYPES, BuildingDNA, mutate, random_dna


def test_every_type_generates():
    rng = random.Random(1)
    for bt in BUILDING_TYPES:
        dna = random_dna(rng, forced={"btype": bt})
        m = generate(dna)
        assert len(m) > 100, bt


def test_dna_roundtrip_and_hash():
    d = random_dna(random.Random(2))
    d2 = BuildingDNA.from_json(d.to_json())
    assert d.dna_hash() == d2.dna_hash()
    v = mutate(d, random.Random(3), strength=1.0)
    assert v.seed != d.seed


def test_schem_roundtrip(tmp_path: Path):
    m = generate(random_dna(random.Random(4), forced={"btype": "house", "scale": "small"}))
    for ver in ("1.21.1", "1.20.4"):
        p = write_schem(m, tmp_path / f"b_{ver}.schem", mc_version=ver)
        m2 = read_schem(p)
        assert {k: v.block for k, v in m.items()} == {k: v.block for k, v in m2.items()}


def test_mcfunction_roundtrip(tmp_path: Path):
    m = generate(random_dna(random.Random(5), forced={"btype": "tower", "scale": "small"}), with_terrain=False)
    p = write_mcfunction(m, tmp_path / "b.mcfunction")
    m2, skipped = read_mcfunction(p.read_text(encoding="utf-8"))
    assert not skipped
    assert {k: v.block for k, v in m.items()} == {k: v.block for k, v in m2.items()}


def test_mcfunction_fill_modes():
    m, skipped = read_mcfunction("fill ~0 ~0 ~0 ~4 ~4 ~4 minecraft:stone hollow\n"
                                 "execute positioned ~ ~ ~ run setblock ~2 ~2 ~2 minecraft:glowstone\nsay hi")
    assert len(m) == 5 ** 3 - 3 ** 3 + 1
    assert skipped == ["say hi"]


def test_orders_cover_all_blocks():
    m = generate(random_dna(random.Random(6), forced={"btype": "castle", "scale": "large"}))
    lab = auto_label(normalize(m))
    for strat in ("natural", "layer", "wave", "multi_front", "bottom_up", "top_down", "section", "sequential", "material"):
        items, _ = order_blocks(lab, strat, 1)
        assert len(items) == len(lab), strat
        assert len({p for p, _ in items}) == len(lab)


def test_compress_runs_counts():
    m = generate(random_dna(random.Random(7), forced={"btype": "house"}), with_terrain=False)
    items, _ = order_blocks(m, "layer", 1)
    ops = compress_runs(items)
    assert sum(op[1][-1] for op in ops) == len(items)
    assert any(op[0] == "fill" for op in ops)


def test_hash_distance():
    assert distance("0" * 32, "0" * 32) == 0
    assert distance("f" * 16, "0" * 16) == 64


def test_license_filter():
    from mcbuildai.audio.fetcher import normalize_license
    assert normalize_license("cc0") == "CC0"
    assert normalize_license("", "http://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert normalize_license("", "http://creativecommons.org/licenses/by/3.0/") == "CC-BY"
    assert normalize_license("by") == "CC-BY"
    for bad in ("http://creativecommons.org/licenses/by-nc-sa/3.0/", "http://creativecommons.org/licenses/by-sa/3.0/",
                "http://creativecommons.org/licenses/by-nd/4.0/"):
        assert normalize_license("", bad) is None
    assert normalize_license("by-nc") is None


def test_i18n_english_default():
    from mcbuildai import i18n
    from mcbuildai.config import load_config
    cfg = load_config("__missing__.yaml")
    assert i18n.lang(cfg) == "en"
    assert i18n.t(cfg, "hook", style="Gothic", type="Cathedral") == "Building a Gothic Cathedral"
