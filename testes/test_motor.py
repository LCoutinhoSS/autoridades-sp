import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import motor_regional as m
from adaptadores import coletor_vereadores_sp as cmsp


class TesteMotorRegional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alvo, cls.regioes, cls.lotes, cls.nomes = m.demo_geografia("rmsp")
        cls.fontes = m.carregar_fontes(m.BASE_DIR / "config/fontes.json")

    def args(self, lote=None, incluir_capital=False):
        return SimpleNamespace(regiao="rmsp", lote=lote, modo="inventario", demo=True, incluir_capital=incluir_capital)

    def test_39_municipios_subregioes_e_capital_em_todas(self):
        m.validar_geografia("rmsp", self.lotes, self.nomes)
        self.assertEqual(len(self.lotes["rmsp"]), 39)
        self.assertEqual([len(self.lotes[k]) for k in m.CONTAGENS_RMSP], [39,6,12,8,9,8])

    def test_inventario_geral_78_posicoes_sem_registros_inventados(self):
        linhas, resumo = m.gerar_inventario(self.args(), self.alvo, self.regioes, self.lotes, self.nomes, self.fontes, None)
        self.assertEqual(len(linhas), 78)
        self.assertEqual(resumo["fontes_configuradas"], 1)
        self.assertEqual(resumo["fontes_a_identificar"], 77)
        self.assertIsNone(resumo["registros_atuais_em_categorias_do_lote"])
        self.assertFalse(resumo["gravou_no_supabase"])

    def test_lote_leste_sem_capital_e_sem_duplicacao(self):
        linhas, resumo = m.gerar_inventario(self.args("rmsp-leste"), self.alvo, self.regioes, self.lotes, self.nomes, self.fontes, None)
        self.assertEqual(resumo["municipios_no_lote"], 11)
        self.assertEqual(len(linhas), 22)
        self.assertEqual(resumo["fontes_configuradas"], 0)
        self.assertTrue(all(x["municipio"] != "São Paulo" for x in linhas))

    def test_lote_leste_pode_incluir_capital_explicitamente(self):
        linhas, resumo = m.gerar_inventario(self.args("rmsp-leste", True), self.alvo, self.regioes, self.lotes, self.nomes, self.fontes, None)
        self.assertEqual(resumo["municipios_no_lote"], 12)
        self.assertEqual(resumo["fontes_configuradas"], 1)

    def test_fonte_duplicada_rejeitada(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fontes.json"
            f = self.fontes[("3550308", "vereador")]
            p.write_text(json.dumps({"fontes": [f, f]}), encoding="utf8")
            with self.assertRaises(ValueError):
                m.carregar_fontes(p)

    def test_publicacao_privilegiada_bloqueada(self):
        with self.assertRaises(ValueError):
            m.ClienteSupabaseLeitura("https://exemplo.supabase.co", "sb_secret_fake")

    def test_salvar_inventario_sem_mudar_banco(self):
        linhas, resumo = m.gerar_inventario(self.args(), self.alvo, self.regioes, self.lotes, self.nomes, self.fontes, None)
        with tempfile.TemporaryDirectory() as tmp:
            m.salvar_inventario(Path(tmp), linhas, resumo)
            self.assertEqual(len(json.loads((Path(tmp)/"inventario.json").read_text(encoding="utf8"))), 78)
            self.assertFalse(json.loads((Path(tmp)/"resumo.json").read_text(encoding="utf8"))["gravou_no_supabase"])

    def test_camara_sp_reaproveitada_55_e_mantem_pendente(self):
        exemplo = [{"Ativo": "on", "Licenciado": "off", "Vereador": f"Pessoa Teste {n}", "Partido": "PT", "email": f"teste{n}@saopaulo.sp.leg.br", "Ramal": "3396-1234"} for n in range(1,56)]
        registros, linhas, resumo = cmsp.preparar(exemplo)
        self.assertEqual(len(registros), 55)
        self.assertEqual(resumo["erros"], [])
        self.assertTrue(all(r["status_verificacao"] == "pendente" for r in registros))
        self.assertFalse(resumo["importacao_realizada"])

if __name__ == "__main__":
    unittest.main()
