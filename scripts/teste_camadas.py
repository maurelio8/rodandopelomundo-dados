"""
scripts/teste_camadas.py

Testes dos dois scripts, sem internet (o Overpass é trocado por respostas
falsas). Usa o unittest, que já vem com o Python.

Rodar a partir da raiz do repositório:

    python3 -m unittest discover -s scripts -p "teste_*.py" -v
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import atualizar_indice as indice
import gerar_camadas as g


def no(id_, lat, lon, **tags):
    """Um ponto (node) falso do Overpass, com as etiquetas dadas."""
    return {"type": "node", "id": id_, "lat": lat, "lon": lon, "tags": tags}


def area(id_, lat, lon, **tags):
    """Uma área (way) falsa do Overpass: a posição vem no "center"."""
    return {"type": "way", "id": id_, "center": {"lat": lat, "lon": lon},
            "tags": tags}


class TesteClassificar(unittest.TestCase):
    """As mesmas regras do app (test/camadas_osm_test.dart), mais as
    borracharias."""

    def test_camadas_basicas(self):
        self.assertEqual(g.classificar({"barrier": "toll_booth"}), "pedagios")
        self.assertEqual(g.classificar({"amenity": "fuel"}), "postos")
        self.assertEqual(g.classificar({"shop": "motorcycle"}), "oficinasMoto")
        self.assertEqual(g.classificar({"shop": "motorcycle_repair"}), "oficinasMoto")
        self.assertEqual(g.classificar({"amenity": "motorcycle_parking"}),
                         "estacionamentoMoto")
        self.assertEqual(
            g.classificar({"amenity": "parking", "motorcycle": "designated"}),
            "estacionamentoMoto")
        self.assertEqual(g.classificar({"tourism": "viewpoint"}), "mirantes")
        self.assertEqual(g.classificar({"traffic_calming": "bump"}), "lombadas")

    def test_pedagio_ganha_do_posto(self):
        self.assertEqual(
            g.classificar({"amenity": "fuel", "barrier": "toll_booth"}), "pedagios")

    def test_fora_das_camadas(self):
        self.assertIsNone(g.classificar({"traffic_calming": "rumble_strip"}))
        self.assertIsNone(g.classificar({"amenity": "parking"}))
        self.assertIsNone(g.classificar({"shop": "car_repair", "name": "Auto Center"}))

    def test_borracharias(self):
        self.assertEqual(g.classificar({"shop": "tyres"}), "borracharias")
        self.assertEqual(
            g.classificar({"shop": "car_repair", "name": "BORRACHARIA DO ZÉ"}),
            "borracharias")
        # Oficina de moto com "borracharia" no nome continua oficina de moto.
        self.assertEqual(
            g.classificar({"shop": "motorcycle_repair", "name": "Borracharia Moto"}),
            "oficinasMoto")


class TesteExtras(unittest.TestCase):

    def test_posto_24h_pelo_horario_e_pelo_nome(self):
        self.assertEqual(
            g.extras("postos", {"brand": "Shell", "opening_hours": "24/7"}),
            {"marca": "Shell", "h24": True})
        self.assertEqual(g.extras("postos", {"name": "Posto 24 Horas"}), {"h24": True})
        self.assertEqual(g.extras("postos", {"opening_hours": "Mo-Sa 06:00-22:00"}), {})

    def test_borracharia_24h(self):
        self.assertEqual(g.extras("borracharias", {"name": "Borracharia 24h"}),
                         {"h24": True})
        self.assertEqual(g.extras("borracharias", {"name": "Borracharia 2400"}), {})

    def test_estacionamento(self):
        self.assertEqual(
            g.extras("estacionamentoMoto", {"capacity": "20", "fee": "yes"}),
            {"vagas": 20, "pago": True})
        self.assertEqual(g.extras("estacionamentoMoto", {"fee": "no"}), {"pago": False})
        self.assertEqual(g.extras("estacionamentoMoto", {"capacity": "muitas"}), {})

    def test_mirante_altitude_solta(self):
        self.assertEqual(g.extras("mirantes", {"ele": "850 m"}), {"altitude": 850})
        self.assertEqual(g.extras("mirantes", {"ele": "1012,6"}), {"altitude": 1013})
        self.assertEqual(g.extras("mirantes", {"ele": "alto"}), {})

    def test_lombada_so_tipos_especiais(self):
        self.assertEqual(g.extras("lombadas", {"traffic_calming": "table"}),
                         {"tipo": "table"})
        self.assertEqual(g.extras("lombadas", {"traffic_calming": "bump"}), {})

    def test_oficina_loja(self):
        self.assertEqual(g.extras("oficinasMoto", {"shop": "motorcycle"}), {"loja": True})
        self.assertEqual(g.extras("oficinasMoto", {"shop": "motorcycle_repair"}), {})


class TesteResposta(unittest.TestCase):

    def test_le_ponto_e_area(self):
        lugares = g.ler_resposta({"elements": [
            no(1, -8.051234567, -34.9, amenity="fuel", name=" Posto A "),
            area(2, -8.2, -35.0, amenity="motorcycle_parking"),
            {"type": "node", "id": 3, "tags": {"amenity": "fuel"}},  # sem posição
            no(4, -8.3, -35.1, amenity="bench"),                       # nenhuma camada
        ]})
        self.assertEqual(len(lugares), 2)
        self.assertEqual(lugares[0], {"_camada": "postos", "id": "n1",
                                      "lat": -8.05123, "lon": -34.9,
                                      "nome": "Posto A"})
        self.assertEqual(lugares[1]["id"], "w2")

    def test_resposta_pela_metade_lanca_erro(self):
        with self.assertRaises(ValueError):
            g.ler_resposta({"remark": "runtime error: Query timed out", "elements": []})

    def test_consulta_tem_area_do_estado(self):
        consulta = g.consulta_overpass("PE", "borracharias")
        self.assertIn('area["ISO3166-2"="BR-PE"]->.uf;', consulta)
        self.assertIn('nwr["shop"="tyres"](area.uf);', consulta)
        self.assertIn('["name"~"borrach",i](area.uf);', consulta)


class TesteArquivo(unittest.TestCase):

    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp()) / "camadas"
        # Uma resposta falsa por camada: alguns postos e um pedágio que
        # também é posto (aparece nas duas consultas e deve sair uma vez só).
        self.respostas = {
            "postos": [no(10 + i, -8.0 - i / 100, -35.0, amenity="fuel")
                       for i in range(30)]
                      + [no(99, -8.5, -35.5, amenity="fuel", barrier="toll_booth")],
            "pedagios": [no(99, -8.5, -35.5, amenity="fuel", barrier="toll_booth")],
        }

    def baixar_falso(self, consulta):
        """Devolve a resposta da camada que aparece na consulta."""
        for camada, filtros in g.CAMADAS.items():
            if filtros[0] in consulta:
                return {"elements": self.respostas.get(camada, [])}
        return {"elements": []}

    def gerar(self, **kw):
        return g.gerar_uf("PE", pasta=self.pasta, baixar=self.baixar_falso,
                          pausa=0, **kw)

    def ler(self):
        return json.loads((self.pasta / "PE.json").read_text(encoding="utf-8"))

    def test_gera_json_valido_sem_repetidos(self):
        self.assertTrue(self.gerar())
        dados = self.ler()
        self.assertEqual(dados["formato"], 1)
        self.assertEqual(list(dados["camadas"]), list(g.CAMADAS))
        self.assertEqual(len(dados["camadas"]["postos"]), 30)
        self.assertEqual([p["id"] for p in dados["camadas"]["pedagios"]], ["n99"])
        self.assertEqual(dados["camadas"]["borracharias"], [])

    def test_mesmo_conteudo_mesmo_arquivo(self):
        self.gerar()
        primeiro = (self.pasta / "PE.json").read_bytes()
        self.gerar()
        self.assertEqual((self.pasta / "PE.json").read_bytes(), primeiro)

    def test_trava_sem_postos(self):
        self.respostas = {}
        self.assertFalse(self.gerar())
        self.assertFalse((self.pasta / "PE.json").exists())

    def test_trava_queda_grande_e_forcar(self):
        self.gerar()
        self.respostas["postos"] = self.respostas["postos"][:10]  # 30 → 10 postos
        self.assertFalse(self.gerar())
        self.assertEqual(len(self.ler()["camadas"]["postos"]), 30)  # antigo intacto
        self.assertTrue(self.gerar(forcar=True))
        self.assertEqual(len(self.ler()["camadas"]["postos"]), 10)


class TesteIndice(unittest.TestCase):

    def setUp(self):
        self.raiz = Path(tempfile.mkdtemp())
        (self.raiz / "camadas").mkdir()
        self.pe = self.raiz / "camadas" / "PE.json"
        self.escrever_pe(-8.12345)

    def escrever_pe(self, lat):
        self.pe.write_text(json.dumps({"formato": 1, "camadas": {
            "postos": [{"id": "n1", "lat": lat, "lon": -34.9},
                       {"id": "n2", "lat": -3.85, "lon": -32.42}]}}),
            encoding="utf-8")

    def ler(self):
        return json.loads((self.raiz / "indice.json").read_text(encoding="utf-8"))

    def test_versao_so_sobe_quando_muda(self):
        dia1 = datetime(2026, 11, 6, 20, 0, tzinfo=indice.BRASILIA)
        dia2 = datetime(2026, 12, 1, 6, 17, tzinfo=indice.BRASILIA)
        self.assertTrue(indice.atualizar(self.raiz, dia1))
        self.assertEqual(self.ler()["arquivos"]["camadas/PE.json"]["versao"],
                         202611062000)
        # Nada mudou: não regrava.
        self.assertFalse(indice.atualizar(self.raiz, dia2))
        # Mudou: a versão sobe.
        self.escrever_pe(-8.2)
        self.assertTrue(indice.atualizar(self.raiz, dia2))
        self.assertEqual(self.ler()["arquivos"]["camadas/PE.json"]["versao"],
                         202612010617)

    def test_bbox_cobre_todos_os_pontos(self):
        indice.atualizar(self.raiz, datetime(2026, 11, 6, tzinfo=indice.BRASILIA))
        self.assertEqual(self.ler()["arquivos"]["camadas/PE.json"]["bbox"],
                         [-8.124, -34.9, -3.85, -32.42])

    def test_eventos_entram_e_arquivo_apagado_sai(self):
        (self.raiz / "eventos").mkdir()
        (self.raiz / "eventos" / "agenda.json").write_text("{}", encoding="utf-8")
        indice.atualizar(self.raiz, datetime(2026, 11, 6, tzinfo=indice.BRASILIA))
        arquivos = self.ler()["arquivos"]
        self.assertIn("eventos/agenda.json", arquivos)
        self.assertNotIn("bbox", arquivos["eventos/agenda.json"])
        self.pe.unlink()
        indice.atualizar(self.raiz, datetime(2026, 11, 7, tzinfo=indice.BRASILIA))
        self.assertNotIn("camadas/PE.json", self.ler()["arquivos"])


if __name__ == "__main__":
    unittest.main()
