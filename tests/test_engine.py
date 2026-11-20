"""Rodada completa do engine com Bling/ML falsos (sem rede)."""
import pytest

from erp import engine
from erp.state import State


class FakeBling:
    saldo = {1: 5, 2: 0}
    lancamentos = []

    def __init__(self, *a):
        pass

    def produtos(self):
        yield from [{"id": 1, "codigo": "p1"}, {"id": 2, "codigo": "P2"}]

    def saldos(self, ids, dep, campo):
        return {i: self.saldo[i] for i in ids if i in self.saldo}

    def lancar(self, pid, dep, op, q, obs):
        FakeBling.lancamentos.append((pid, dep, op, q, obs))


class FakeMeli:
    puts = []
    pedidos = {}
    envios = {}

    def __init__(self, canal, *a):
        self.canal = canal

    def seller_id(self):
        return 100

    def itens_sincronizaveis(self, seller):
        n = self.canal.nome
        yield {"id": f"MLB{n}1", "status": "active", "available_quantity": 2, "seller_custom_field": "P1"}
        yield {"id": f"MLB{n}2", "status": "active", "available_quantity": 3, "seller_custom_field": "P2"}
        yield {"id": f"MLB{n}3", "status": "active", "available_quantity": 7, "seller_custom_field": "NAO-EXISTE"}
        yield {"id": f"MLB{n}4", "status": "active", "variations": [
            {"id": 11, "available_quantity": 0, "seller_custom_field": "P1"},
            {"id": 12, "available_quantity": 9}]}

    def pedidos_desde(self, seller, desde):
        return iter(self.pedidos.get(self.canal.nome, []))

    def envio(self, shipping_id):
        return FakeMeli.envios.get(shipping_id, {})

    def atualizar_item(self, item_id, payload):
        FakeMeli.puts.append((item_id, payload))


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dry_run: false\nmodo_baixo_estoque: espelho\nsku_map_csv: none.csv\nsku_prefixo_obrigatorio: ''\n"
        "canais:\n  - {nome: CAPITAL, prioridade: 1}\n  - {nome: VERO, prioridade: 2}\n", encoding="utf-8")
    for k in ("BLING_CLIENT_ID", "BLING_CLIENT_SECRET", "MELI_CLIENT_ID", "MELI_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("STATE_KEY", State.gerar_chave())
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(engine, "Bling", FakeBling)
    monkeypatch.setattr(engine, "Meli", FakeMeli)
    FakeMeli.puts, FakeMeli.pedidos, FakeMeli.envios = [], {}, {}
    FakeBling.saldo, FakeBling.lancamentos = {1: 5, 2: 0}, []
    return str(cfg), str(tmp_path / "st")


def test_espelha_e_reserva_venda(ambiente):
    cfg, sd = ambiente
    rel = engine.sync(cfg, sd)
    assert rel.ok, rel.erros
    puts = dict(FakeMeli.puts)
    assert puts["MLBCAPITAL1"] == {"available_quantity": 5}
    assert puts["MLBVERO2"] == {"available_quantity": 0}
    assert "MLBVERO3" not in puts  # sem vínculo: nunca mexe
    # variação: envia todas, só altera a vinculada
    assert puts["MLBCAPITAL4"] == {"variations": [{"id": 11, "available_quantity": 5},
                                                  {"id": 12, "available_quantity": 9}]}

    # 2ª rodada: venda paga de 2 un na VERO -> todas as contas caem para 3
    FakeMeli.puts = []
    FakeMeli.pedidos = {"VERO": [{"id": 9, "status": "paid", "order_items": [
        {"item": {"id": "MLBVERO1"}, "quantity": 2}]}]}
    rel = engine.sync(cfg, sd)
    assert rel.ok, rel.erros
    puts = dict(FakeMeli.puts)
    assert puts["MLBCAPITAL1"] == {"available_quantity": 3}
    assert puts["MLBVERO1"] == {"available_quantity": 3}

    # 3ª rodada: Rennar baixou no Bling (5 -> 3): reserva some, estoque continua 3
    FakeBling.saldo = {1: 3, 2: 0}
    FakeMeli.puts = []
    rel = engine.sync(cfg, sd)
    assert rel.ok, rel.erros
    assert rel.metricas["Reservas ativas"] == 0
    assert dict(FakeMeli.puts)["MLBCAPITAL1"] == {"available_quantity": 3}  # sem desconto duplo


def test_trava_zeragem_bloqueia(ambiente, monkeypatch):
    cfg, sd = ambiente
    monkeypatch.setattr(engine.al, "trava_zeragem", lambda *a: True)
    rel = engine.sync(cfg, sd)
    assert not rel.ok and FakeMeli.puts == []


def test_cancelamento_pre_envio_vs_devolucao_pos_envio(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dry_run: false\nmodo_baixo_estoque: espelho\nsku_map_csv: none.csv\nsku_prefixo_obrigatorio: ''\n"
        "bling_write_mode: estoque\nbling_deposito_id: 99\ndevolucao_auto_estorno: false\n"
        "canais:\n  - {nome: CAPITAL, prioridade: 1}\n", encoding="utf-8")
    for k in ("BLING_CLIENT_ID", "BLING_CLIENT_SECRET", "MELI_CLIENT_ID", "MELI_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("STATE_KEY", State.gerar_chave())
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(engine, "Bling", FakeBling)
    monkeypatch.setattr(engine, "Meli", FakeMeli)
    FakeMeli.puts, FakeMeli.pedidos, FakeMeli.envios = [], {}, {}
    FakeBling.saldo, FakeBling.lancamentos = {1: 5, 2: 0}, []
    sd = str(tmp_path / "st")

    # Rodada 1: inicializa checkpoint
    assert engine.sync(str(cfg), sd).ok

    # Rodada 2: 2 pedidos pagos -> ambos dão saída ("S") no Bling
    FakeMeli.pedidos = {"CAPITAL": [
        {"id": 101, "status": "paid", "shipping": {"id": 501},
         "order_items": [{"item": {"id": "MLBCAPITAL1"}, "quantity": 1}]},
        {"id": 102, "status": "paid", "shipping": {"id": 502},
         "order_items": [{"item": {"id": "MLBCAPITAL1"}, "quantity": 1}]},
    ]}
    assert engine.sync(str(cfg), sd).ok
    assert [x[2] for x in FakeBling.lancamentos] == ["S", "S"]

    # Rodada 3: 101 cancelado pré-envio (ready_to_ship) -> estorna "E";
    #           102 devolvido pós-envio (delivered) -> NÃO estorna automaticamente, gera alerta.
    FakeBling.lancamentos = []
    FakeMeli.envios = {501: {"status": "ready_to_ship"}, 502: {"status": "delivered"}}
    FakeMeli.pedidos = {"CAPITAL": [
        {"id": 101, "status": "cancelled", "shipping": {"id": 501}, "order_items": []},
        {"id": 102, "status": "cancelled", "shipping": {"id": 502}, "order_items": []},
    ]}
    rel = engine.sync(str(cfg), sd)
    assert rel.ok
    assert len(FakeBling.lancamentos) == 1
    assert FakeBling.lancamentos[0][2] == "E" and "101" in FakeBling.lancamentos[0][4]
    assert any("102" in msg and "Devolução pós-envio" in msg for msg in rel.infos)


def test_isolamento_prefixo_ren_ignora_anuncios_proprios(tmp_path, monkeypatch):
    class MeliMisto(FakeMeli):
        def itens_sincronizaveis(self, seller):
            # P1 sem prefixo = anúncio próprio/Donegá (NÃO pode sincronizar mesmo existindo P1 no Bling!)
            yield {"id": "MLBPROPRIO1", "status": "active", "available_quantity": 99, "seller_custom_field": "P1"}
            yield {"id": "MLBPROPRIO2", "status": "active", "available_quantity": 10, "seller_custom_field": None}
            yield {"id": "MLBRENNAR1", "status": "active", "available_quantity": 1, "seller_custom_field": "REN-P1"}

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dry_run: false\nmodo_baixo_estoque: espelho\nsku_map_csv: none.csv\nsku_prefixo_obrigatorio: 'REN-'\n"
        "canais:\n  - {nome: CAPITAL, prioridade: 1}\n", encoding="utf-8")
    for k in ("BLING_CLIENT_ID", "BLING_CLIENT_SECRET", "MELI_CLIENT_ID", "MELI_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("STATE_KEY", State.gerar_chave())
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(engine, "Bling", FakeBling)
    monkeypatch.setattr(engine, "Meli", MeliMisto)
    FakeMeli.puts, FakeMeli.pedidos, FakeMeli.envios = [], {}, {}
    FakeBling.saldo = {1: 5, 2: 0}

    rel = engine.sync(str(cfg), str(tmp_path / "st"))
    assert rel.ok, rel.erros
    assert rel.metricas["Anúncios próprios ignorados (não-Rennar)"] == 2
    assert rel.metricas["Anúncios vinculados"] == 1
    assert dict(FakeMeli.puts) == {"MLBRENNAR1": {"available_quantity": 5}}
    assert rel.sem_vinculo == []


def test_zero_anuncios_rennar_nao_falha(tmp_path, monkeypatch):
    class MeliSoProprios(FakeMeli):
        def itens_sincronizaveis(self, seller):
            yield {"id": "MLBDONEGA1", "status": "active", "available_quantity": 40, "seller_custom_field": "P1"}
            yield {"id": "MLBSEM1", "status": "active", "available_quantity": 12, "seller_custom_field": "PROPRIO-77"}

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dry_run: false\nmodo_baixo_estoque: espelho\nsku_map_csv: none.csv\nsku_prefixo_obrigatorio: 'REN-'\n"
        "canais:\n  - {nome: CAPITAL, prioridade: 1}\n  - {nome: VERO, prioridade: 2}\n", encoding="utf-8")
    for k in ("BLING_CLIENT_ID", "BLING_CLIENT_SECRET", "MELI_CLIENT_ID", "MELI_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("STATE_KEY", State.gerar_chave())
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(engine, "Bling", FakeBling)
    monkeypatch.setattr(engine, "Meli", MeliSoProprios)
    FakeMeli.puts, FakeMeli.pedidos, FakeMeli.envios = [], {}, {}

    rel = engine.sync(str(cfg), str(tmp_path / "st"))
    assert rel.ok, rel.erros
    assert FakeMeli.puts == []
    assert rel.metricas["Anúncios próprios ignorados (não-Rennar)"] == 4
    assert rel.metricas["SKUs Rennar vinculados"] == 0


def test_alerta_cancelamento_smtp_e_arquivo(tmp_path, monkeypatch):
    import smtplib
    from erp import notify

    enviados = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=20, context=None):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, pwd):
            pass

        def send_message(self, msg):
            enviados.append((msg["Subject"], msg["To"], msg.get_content()))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for k, v in {
        "BLING_CLIENT_ID": "x", "BLING_CLIENT_SECRET": "x",
        "MELI_CLIENT_ID": "x", "MELI_CLIENT_SECRET": "x",
        "SMTP_HOST": "smtp.exemplo.com", "SMTP_PORT": "587",
        "SMTP_USER": "bot@exemplo.com", "SMTP_PASS": "segredo",
        "ALERT_EMAIL_TO": "dono@exemplo.com",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STATE_KEY", State.gerar_chave())
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(engine, "Bling", FakeBling)
    monkeypatch.setattr(engine, "Meli", FakeMeli)
    FakeMeli.puts, FakeMeli.pedidos, FakeMeli.envios = [], {}, {}
    FakeBling.saldo, FakeBling.lancamentos = {1: 5, 2: 0}, []

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dry_run: false\nmodo_baixo_estoque: espelho\nsku_map_csv: none.csv\nsku_prefixo_obrigatorio: ''\n"
        "bling_write_mode: estoque\nbling_deposito_id: 99\ndevolucao_auto_estorno: false\n"
        "canais:\n  - {nome: CAPITAL, prioridade: 1}\n", encoding="utf-8")
    sd = tmp_path / "st"

    assert engine.sync(str(cfg), str(sd)).ok
    FakeMeli.pedidos = {"CAPITAL": [
        {"id": 201, "status": "paid", "shipping": {"id": 601},
         "order_items": [{"item": {"id": "MLBCAPITAL1"}, "quantity": 1}]},
        {"id": 202, "status": "paid", "shipping": {"id": 602},
         "order_items": [{"item": {"id": "MLBCAPITAL1"}, "quantity": 1}]},
    ]}
    assert engine.sync(str(cfg), str(sd)).ok

    FakeMeli.envios = {601: {"status": "ready_to_ship"}, 602: {"status": "delivered"}}
    FakeMeli.pedidos = {"CAPITAL": [
        {"id": 201, "status": "cancelled", "shipping": {"id": 601}, "order_items": []},
        {"id": 202, "status": "cancelled", "shipping": {"id": 602}, "order_items": []},
    ]}
    rel = engine.sync(str(cfg), str(sd))
    assert rel.ok
    alerta_md = (sd / "cancelamentos_alerta.md").read_text(encoding="utf-8")
    assert "201" in alerta_md and "CANCELAMENTO PRÉ-ENVIO" in alerta_md
    assert "202" in alerta_md and "DEVOLUÇÃO PÓS-ENVIO" in alerta_md
    assert len(enviados) == 1
    assert "2 pedido(s)" in enviados[0][0]
    assert "201" in enviados[0][2] and "202" in enviados[0][2]

    # Próxima rodada calma remove arquivo antigo para não duplicar Issue
    FakeMeli.pedidos = {}
    assert engine.sync(str(cfg), str(sd)).ok
    assert not (sd / "cancelamentos_alerta.md").exists()
    assert notify.smtp_configurado() is True


def test_credenciais_meli_por_canal(monkeypatch):
    from erp.settings import Canal
    monkeypatch.setenv("MELI_CLIENT_ID", "global_id")
    monkeypatch.setenv("MELI_CLIENT_SECRET", "global_sec")
    monkeypatch.setenv("MELI_CLIENT_ID_VERO", "vero_id")
    monkeypatch.setenv("MELI_CLIENT_SECRET_VERO", "vero_sec")
    assert engine._meli_creds(Canal(nome="CAPITAL")) == ("global_id", "global_sec")
    assert engine._meli_creds(Canal(nome="VERO")) == ("vero_id", "vero_sec")
