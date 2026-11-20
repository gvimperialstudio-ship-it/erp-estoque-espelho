from datetime import datetime, timedelta, timezone

from erp import allocation as al
from erp.mapping import SkuMap, normaliza, parse_componentes, sku_of
from erp.state import State, StateError

T0 = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def _res(criada, itens, ref):
    return {"criada": criada.isoformat(), "itens": dict(itens), "saldo_ref": dict(ref)}


def test_kit_usa_minimo_entre_componentes():
    assert al.qtd_por_componentes([("A", 2), ("B", 1)], {"A": 9, "B": 3}) == 3
    assert al.qtd_por_componentes([("A", 4)], {"A": 3}) == 0
    assert al.qtd_por_componentes([("X", 1)], {"A": 3}) is None


def test_limites_buffer_e_teto():
    assert al.aplicar_limites(10, 2, 5) == 5
    assert al.aplicar_limites(1, 2, 99) == 0


def test_prioridade_baixo_estoque():
    ls = [{"canal": c, "componentes": [("A", 1)]} for c in ("VERO", "CAPITAL", "ELUXIA")]
    donos = al.donos_baixo_estoque(ls, {"A": 1}, 1, {"CAPITAL": 1, "VERO": 2, "ELUXIA": 3})
    assert donos == {"A": "CAPITAL"}
    assert al.donos_baixo_estoque(ls, {"A": 5}, 1, {}) == {}


def test_reconciliar_fifo_sem_baixa_dupla():
    r = {"a": _res(T0, {"A": 1}, {"A": 5}), "b": _res(T0 + timedelta(seconds=1), {"A": 1}, {"A": 5})}
    al.reconciliar(r, {"A": 4}, 24, T0 + timedelta(hours=1))
    assert list(r) == ["b"] and r["b"]["saldo_ref"]["A"] == 4
    al.reconciliar(r, {"A": 4}, 24, T0 + timedelta(hours=2))
    assert list(r) == ["b"]  # 2ª venda ainda não chegou ao Bling
    al.reconciliar(r, {"A": 3}, 24, T0 + timedelta(hours=3))
    assert r == {}


def test_reconciliar_reposicao_segura_ate_ttl():
    r = {"a": _res(T0, {"A": 1}, {"A": 5})}
    al.reconciliar(r, {"A": 15}, 24, T0 + timedelta(hours=1))
    assert "a" in r
    al.reconciliar(r, {"A": 15}, 24, T0 + timedelta(hours=25))
    assert r == {}


def test_reservas_por_codigo():
    r = {"a": _res(T0, {"A": 2, "B": 1}, {}), "b": _res(T0, {"A": 1}, {})}
    assert al.reservas_por_codigo(r) == {"A": 3, "B": 1}


def test_trava_zeragem():
    assert al.trava_zeragem(100, 50, 40, 5) is True
    assert al.trava_zeragem(4, 3, 40, 5) is False   # catálogo pequeno não trava
    assert al.trava_zeragem(100, 10, 40, 5) is False


def test_mapping(tmp_path):
    assert sku_of({"seller_custom_field": " 123 "}) == "123"
    assert sku_of({"attributes": [{"id": "SELLER_SKU", "value_name": "X9"}]}) == "X9"
    assert normaliza("vr-123", ["VR-"]) == "123"
    assert parse_componentes("a:2|b") == [("A", 2), ("B", 1)]
    csv = tmp_path / "m.csv"
    csv.write_text("canal,item_id,variation_id,sku_anuncio,componentes\n"
                   "*,,,KIT4,P1:4\nVERO,MLB1,,,P1:2|P2:1\n", encoding="utf-8")
    m = SkuMap(str(csv), ["VR-"])
    assert m.componentes("VERO", "MLB1", None, "qualquer") == [("P1", 2), ("P2", 1)]
    assert m.componentes("CAPITAL", "MLB9", None, "kit4") == [("P1", 4)]
    assert m.componentes("VERO", "MLB9", None, "VR-77") == [("77", 1)]
    assert m.componentes("VERO", "MLB9", None, None) is None


def test_mapping_prefixo_obrigatorio_ren(tmp_path):
    csv = tmp_path / "m.csv"
    csv.write_text("canal,item_id,variation_id,sku_anuncio,componentes\n"
                   "VERO,MLB-KIT,,,P1:2\n", encoding="utf-8")
    m = SkuMap(str(csv), ["VR-"], prefixo_obrigatorio="REN-")
    assert m.classificar("CAPITAL", "MLB1", None, "P1") == ("proprio", None)
    assert m.classificar("CAPITAL", "MLB2", None, None) == ("proprio", None)
    assert m.classificar("CAPITAL", "MLB3", None, "ren-p1") == ("rennar", [("P1", 1)])
    assert m.classificar("VERO", "MLB4", None, "REN-VR-99") == ("rennar", [("99", 1)])
    assert m.classificar("VERO", "MLB-KIT", None, "SEM-PREFIXO") == ("mapeado", [("P1", 2)])


def test_state_criptografado(tmp_path):
    k = State.gerar_chave()
    s = State(str(tmp_path), k).load()
    s.section("tokens")["bling"] = {"refresh_token": "abc"}
    s.save()
    assert b"abc" not in (tmp_path / "state.enc").read_bytes()
    assert State(str(tmp_path), k).load().data["tokens"]["bling"]["refresh_token"] == "abc"
    try:
        State(str(tmp_path), State.gerar_chave()).load()
        assert False
    except StateError:
        pass


def test_seed_so_reimporta_quando_muda():
    from erp.http import importar_seed
    t = {}
    importar_seed(t, "seed1")
    t["refresh_token"] = "girado"
    importar_seed(t, "seed1")
    assert t["refresh_token"] == "girado"
    importar_seed(t, "seed2")
    assert t["refresh_token"] == "seed2"
