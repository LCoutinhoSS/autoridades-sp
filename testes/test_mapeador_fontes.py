import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mapeador_fontes as m


class TestesMapeador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fontes = m.ler_catalogo(m.BASE / "config/fontes_candidatas_rmsp_leste.json")

    def test_catalogo_22_fontes(self):
        self.assertEqual(len(self.fontes), 22)
        self.assertEqual(len({x["municipio"] for x in self.fontes}), 11)

    def test_11_prefeitos_e_11_camaras(self):
        self.assertEqual(sum(x["categoria"] == "prefeito" for x in self.fontes), 11)
        self.assertEqual(sum(x["categoria"] == "vereador" for x in self.fontes), 11)

    def test_todas_urls_https_permitidas(self):
        self.assertTrue(all(m.url_publica(x["url_candidata"]) for x in self.fontes))

    def test_rejeicao_de_urls_inseguras(self):
        for url in ("http://camara.sp.gov.br/", "https://localhost/", "https://127.0.0.1/",
                    "https://example.com/", "https://user:pass@camara.sp.gov.br/"):
            self.assertFalse(m.url_publica(url), url)

    def test_siscam(self):
        d = m.classificar("<html><a href='/vereadores'>Vereadores</a></html>",
                         "https://siscam.camaraaruja.sp.gov.br/vereadores")
        self.assertEqual(d["familia_sugerida"], "siscam")

    def test_sapl(self):
        d = m.classificar("<html><p>Sistema de Apoio ao Processo Legislativo</p></html>",
                         "https://camara.sp.gov.br/")
        self.assertEqual(d["familia_sugerida"], "sapl")

    def test_wordpress(self):
        d = m.classificar('<html><meta name="generator" content="WordPress 6"></html>',
                         "https://camara.sp.gov.br/")
        self.assertEqual(d["familia_sugerida"], "wordpress")

    def test_nao_segue_link_externo(self):
        html = '<a href="https://evil.example.com/vereadores">Fora</a><a href="/vereadores">Vereadores</a>'
        d = m.classificar(html, "https://camara.sp.gov.br/")
        self.assertEqual(d["links_promissores"], ["https://camara.sp.gov.br/vereadores"])

    def test_avisos_licenca_e_legislatura(self):
        d = m.classificar("<html><p>Legislatura 2021-2024, vereador licenciado, suplente</p></html>",
                         "https://camara.sp.gov.br/")
        self.assertEqual(len(d["avisos"]), 2)

    def test_lote_e_relatorios(self):
        nomes = {str(i): n for i, n in enumerate(sorted({x["municipio"] for x in self.fontes}), 1)}
        rows = m.gerar_linhas(self.fontes, nomes, set(nomes), "catalogo")
        self.assertEqual(len(rows), 22)
        self.assertTrue(all(x["status_homologacao"] == "PENDENTE" for x in rows))
        self.assertTrue(all(x["gravou_no_supabase"] is False for x in rows))
        with tempfile.TemporaryDirectory() as temp:
            r = m.salvar(Path(temp), rows, "catalogo", "teste")
            self.assertEqual(r["autoridades_extraidas"], 0)
            self.assertEqual(r["posicoes"], 22)
            self.assertTrue((Path(temp)/"mapa_fontes.csv").exists())
            self.assertEqual(len(json.loads((Path(temp)/"mapa_fontes.json").read_text(encoding="utf-8"))), 22)

    def test_fonte_fora_do_lote(self):
        with self.assertRaises(ValueError):
            m.gerar_linhas(self.fontes, {"3550308":"São Paulo"}, {"3550308"}, "catalogo")

    def test_csv_formula(self):
        self.assertEqual(m.csv_seguro("=1+1"), "'=1+1")

    def test_diagnostico_bloqueado_continua_lote(self):
        nomes = {str(i): n for i, n in enumerate(sorted({x["municipio"] for x in self.fontes}), 1)}
        def fake(url):
            return {"situacao_http": "bloqueio_ou_restricao", "familia_sugerida": "desconhecida",
                    "confianca_tecnica": "nenhuma", "evidencias": [], "links_promissores": [],
                    "avisos": ["HTTP 406"]}
        rows = m.gerar_linhas(self.fontes, nomes, set(nomes), "verificar", diagnostico=fake)
        self.assertEqual(len(rows), 22)

if __name__ == "__main__":
    unittest.main()
