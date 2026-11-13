#!/usr/bin/env python3
"""Autoridades SP — Motor Regional 1.0: inventário territorial e coleta EM PRÉVIA.

Usa as tabelas geográficas e as RPCs públicas seguras já existentes.
Nenhuma rota de escrita ou chave service_role é utilizada.

Exemplos:
  python motor_regional.py --regiao rmsp --modo inventario
  python motor_regional.py --regiao rmsp --lote rmsp-leste --modo inventario
  python motor_regional.py --regiao rmsp --modo coletar
  python motor_regional.py --regiao rmsp --demo --modo inventario
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
CATEGORIAS = ("prefeito", "vereador")
CAPITAL_IBGE = "3550308"
CONTAGENS_RMSP = {
    "rmsp": 39,
    "rmsp-norte": 6,
    "rmsp-leste": 12,
    "rmsp-sudeste": 8,
    "rmsp-sudoeste": 9,
    "rmsp-oeste": 8,
}
LIMITE_RESPOSTA_BYTES = 6_000_000


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalizar(s):
    t = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).casefold().strip()


def csv_seguro(value):
    text = str(value if value is not None else "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def dominio_https(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username


def carregar_fontes(arquivo):
    dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    fonte_chaves = set()
    for f in dados.get("fontes", []):
        chave = (f.get("municipio_ibge"), f.get("categoria"))
        if chave in fonte_chaves:
            raise ValueError(f"Fonte duplicada para município/categoria: {chave}")
        fonte_chaves.add(chave)
        if not (len(str(chave[0])) == 7 and str(chave[0]).startswith("35")):
            raise ValueError(f"Código IBGE inválido: {chave[0]}")
        if chave[1] not in CATEGORIAS:
            raise ValueError(f"Categoria ainda não suportada: {chave[1]}")
        if f.get("situacao") != "configurada":
            raise ValueError(f"Fonte não aprovada como configurada: {chave}")
        if not dominio_https(f.get("url", "")):
            raise ValueError(f"URL HTTPS oficial obrigatória: {chave}")
        # Adaptadores desconhecidos nunca são acionados automaticamente.
        if f.get("adaptador") not in ("cmsp_vereadores",):
            raise ValueError(f"Adaptador não implementado/revisado: {f.get('adaptador')}")
        if f["adaptador"] == "cmsp_vereadores" and chave != (CAPITAL_IBGE, "vereador"):
            raise ValueError("O adaptador CMSP só é válido para vereadores de São Paulo.")
    return { (f["municipio_ibge"], f["categoria"]): f for f in dados.get("fontes", []) }


class ClienteSupabaseLeitura:
    """Somente GETs públicos; nunca escreve na API, autenticação nem no banco."""

    def __init__(self, url, chave):
        url = str(url or "").rstrip("/")
        if not dominio_https(url):
            raise ValueError("Defina SUPABASE_URL com uma URL HTTPS válida.")
        if not chave:
            raise ValueError("Defina SUPABASE_PUBLISHABLE_KEY (chave pública; nunca service_role).")
        if "service_role" in chave.lower() or chave.startswith("sb_secret_"):
            raise ValueError("Chave privilegiada proibida. Use somente a publishable/anon.")
        self.base = url + "/rest/v1/"
        self.chave = chave

    def get(self, rota, params=None):
        if "/" in rota and rota.startswith("http"):
            raise ValueError("A rota deve ser relativa à API pública.")
        query = "?" + urlencode(params) if params else ""
        req = Request(self.base + rota + query, headers={
            "apikey": self.chave,
            "Accept": "application/json",
            "User-Agent": "AutoridadesSP-Motor-Regional/1.0 (inventario institucional)",
        })
        try:
            with urlopen(req, timeout=40) as resp:
                payload = resp.read(LIMITE_RESPOSTA_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"Falha ao consultar {rota}: {exc}") from exc
        if len(payload) > LIMITE_RESPOSTA_BYTES:
            raise RuntimeError("Resposta da API ultrapassou o limite de segurança.")
        try:
            rows = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"JSON inválido na rota {rota}.") from exc
        if not isinstance(rows, list):
            raise RuntimeError(f"Formato inesperado na rota {rota} (esperado lista).")
        return rows

    def municipio_e_regiao(self, regiao):
        municipios = self.get("municipios", {
            "select": "codigo_ibge,nome", "order": "nome.asc", "limit": "1000"
        })
        regioes = self.get("regioes", {
            "select": "id,nome,tipo", "order": "nome.asc", "limit": "1000"
        })
        alvo = next((r for r in regioes if r.get("id") == regiao), None)
        if alvo is None:
            raise RuntimeError(f"Região '{regiao}' não localizada no Supabase.")
        regioes_lote = {r["id"]: r for r in regioes if r.get("id") == regiao or r.get("id", "").startswith(regiao + "-")}
        codigos_por_regiao = {}
        for rid in regioes_lote:
            vinculos = self.get("municipios_regioes", {
                "select": "codigo_ibge", "regiao_id": "eq." + rid, "limit": "1000"
            })
            codigos_por_regiao[rid] = {str(v["codigo_ibge"]).strip() for v in vinculos}
        por_codigo = {str(m["codigo_ibge"]).strip(): str(m["nome"]) for m in municipios}
        return alvo, regioes_lote, codigos_por_regiao, por_codigo

    def cobertura_atuais(self):
        """Só lê ID/categoria e vínculo IBGE; nunca armazena contato pessoal no relatório."""
        autoridades = self.get("rpc/consultar_autoridades_seguras", {
            "select": "id,kind", "limit": "2000"
        })
        vinculos = self.get("rpc/consultar_vinculos_municipais_seguros", {
            "select": "autoridade_id,municipio_ibge", "limit": "2000"
        })
        kinds = {a["id"]: a["kind"] for a in autoridades if a.get("kind") in CATEGORIAS}
        contagem = Counter()
        for v in vinculos:
            categoria = kinds.get(v.get("autoridade_id"))
            ibge = str(v.get("municipio_ibge") or "").strip()
            if categoria and ibge:
                contagem[(ibge, categoria)] += 1
        return contagem


def demo_geografia(regiao):
    if regiao != "rmsp":
        raise ValueError("A demonstração offline inclui somente a RMSP.")
    fonte = json.loads((BASE_DIR / "config/rmsp_referencia.json").read_text(encoding="utf-8"))
    lista = fonte["municipios"]
    if len(lista) != 39:
        raise ValueError("Snapshot oficial local inválido: esperado 39 municípios.")
    ids = {"rmsp": {x["nome"] for x in lista}}
    for direcao in ("norte", "leste", "sudeste", "sudoeste", "oeste"):
        ids["rmsp-"+direcao] = {x["nome"] for x in lista if x["subregiao"] in (direcao, "capital")}
    # A demonstração usa IDs fictícios apenas para o inventário, nunca para a importação.
    codigo_por_nome = {x["nome"]: (CAPITAL_IBGE if x["nome"] == "São Paulo" else f"demo-{i:03d}") for i,x in enumerate(lista,1)}
    codigo = {r:{codigo_por_nome[n] for n in nomes} for r,nomes in ids.items()}
    nome = {v:k for k,v in codigo_por_nome.items()}
    regioes = {r:{"id":r,"nome":r,"tipo":"referencia_demo"} for r in ids}
    return regioes[regiao], regioes, codigo, nome


def validar_geografia(regiao, lotes, nomes):
    base = lotes.get(regiao, set())
    if not base:
        raise RuntimeError(f"Região {regiao} sem municípios vinculados.")
    if len(nomes) != len(set(nomes)):
        raise RuntimeError("Duplicidade no catálogo municipal.")
    if regiao == "rmsp":
        for rid, esperado in CONTAGENS_RMSP.items():
            real = len(lotes.get(rid, set()))
            if real != esperado:
                raise RuntimeError(f"Integridade territorial falhou: {rid} deveria ter {esperado}, retornou {real}.")
            if rid != "rmsp" and CAPITAL_IBGE not in lotes[rid]:
                raise RuntimeError(f"Capital ausente da sub-região legal {rid}.")
        uniao = set().union(*(s for rid,s in lotes.items() if rid.startswith("rmsp-")))
        if uniao != base:
            raise RuntimeError("Sub-regiões não recompõem os mesmos 39 municípios da RMSP.")
    if any(c not in nomes for c in base):
        raise RuntimeError("Há vínculos sem correspondência na tabela de municípios.")


def gerar_inventario(args, regiao_alvo, regioes, lotes, nomes, fontes, cobertura):
    if args.lote:
        if args.lote not in lotes or not args.lote.startswith(args.regiao + "-"):
            raise ValueError(f"Lote '{args.lote}' inválido para {args.regiao}.")
        codigos = set(lotes[args.lote])
        # A capital pertence a todas as sub-regiões por força legal, mas só roda no lote geral.
        if args.regiao == "rmsp" and not args.incluir_capital:
            codigos.discard(CAPITAL_IBGE)
    else:
        codigos = set(lotes[args.regiao])

    data = []
    for codigo in sorted(codigos, key=lambda c: normalizar(nomes[c])):
        subregioes = sorted(r for r, vs in lotes.items() if r != args.regiao and codigo in vs)
        for categoria in CATEGORIAS:
            origem = fontes.get((codigo, categoria))
            data.append({
                "codigo_ibge": codigo,
                "municipio": nomes[codigo],
                "categoria": categoria,
                "lote_solicitado": args.lote or args.regiao,
                "subregioes_oficiais": ", ".join(subregioes),
                "fonte_configurada": "sim" if origem else "não",
                "adaptador": origem["adaptador"] if origem else "",
                "fonte_url": origem["url"] if origem else "",
                "registros_atuais": cobertura.get((codigo, categoria)) if cobertura is not None else None,
                "situacao": "configurada_para_previa" if origem else "fonte_a_identificar",
            })
    resumo = {
        "gerado_em_utc": agora(),
        "regiao": args.regiao,
        "nome_regiao": regiao_alvo.get("nome", ""),
        "lote": args.lote or "geral",
        "modo": args.modo,
        "fonte_geografia": "snapshot_demonstracao_offline" if args.demo else "supabase_oficial",
        "dados_reais_banco": not args.demo,
        "capital_incluida_no_lote": CAPITAL_IBGE in codigos,
        "municipios_no_lote": len(codigos),
        "municipios_na_regiao": len(lotes[args.regiao]),
        "posicoes_prefeito_vereador": len(data),
        "fontes_configuradas": sum(x["fonte_configurada"] == "sim" for x in data),
        "fontes_a_identificar": sum(x["fonte_configurada"] == "não" for x in data),
        "registros_atuais_em_categorias_do_lote": sum(x["registros_atuais"] or 0 for x in data) if cobertura is not None else None,
        "gravou_no_supabase": False,
        "observacao": "Inventário de cobertura e fontes. Falta de fonte não prova ausência de autoridades. Sem alterações no Supabase."
    }
    return data, resumo


def salvar_inventario(destino, linhas, resumo):
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "resumo.json").write_text(json.dumps(resumo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (destino / "inventario.json").write_text(json.dumps(linhas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    campos = ["codigo_ibge", "municipio", "categoria", "lote_solicitado", "subregioes_oficiais", "fonte_configurada", "adaptador", "fonte_url", "registros_atuais", "situacao"]
    with (destino / "inventario.csv").open("w", encoding="utf-8-sig", newline="") as arquivo:
        w = csv.DictWriter(arquivo, fieldnames=campos, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for linha in linhas:
            w.writerow({k: csv_seguro(linha.get(k)) for k in campos})


def coletar_previas(destino, linhas, fontes):
    """Executa somente adaptadores explicitamente liberados no registro de fontes."""
    status = []
    for linha in linhas:
        origem = fontes.get((linha["codigo_ibge"], linha["categoria"]))
        if not origem:
            continue
        subdir = destino / "previas" / f'{linha["codigo_ibge"]}-{linha["categoria"]}'
        subdir.mkdir(parents=True, exist_ok=True)
        try:
            if origem["adaptador"] == "cmsp_vereadores":
                from adaptadores import coletor_vereadores_sp as cmsp
                cmsp.executar(str(subdir))
                auditoria = json.loads((subdir / "relatorio_coleta.json").read_text(encoding="utf-8"))
                status.append({"municipio_ibge": linha["codigo_ibge"], "categoria": linha["categoria"], "status": "previa_gerada", "selecionados": auditoria["selecionados_para_revisao"], "avisos": len(auditoria["avisos"])})
            else:
                raise RuntimeError("Adaptador não suportado.")
        except Exception as exc:
            status.append({"municipio_ibge": linha["codigo_ibge"], "categoria": linha["categoria"], "status": "erro_requer_revisao", "erro": str(exc)})
    (destino / "resultado_coletas.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status


def executar(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regiao", default="rmsp", help="ID oficial da região cadastrado no Supabase (inicialmente rmsp)")
    parser.add_argument("--lote", help="ID de sub-região, por ex. rmsp-leste")
    parser.add_argument("--modo", choices=("inventario", "coletar"), default="inventario")
    parser.add_argument("--incluir-capital", action="store_true", help="Inclui capital num lote de sub-região; padrão é coletar só no lote geral")
    parser.add_argument("--demo", action="store_true", help="Teste offline do inventário; códigos sintéticos NÃO servem para importação")
    parser.add_argument("--saida", default="relatorios/motor-regional", help="Pasta para os relatórios e prévias")
    args = parser.parse_args(argv)
    if args.demo and args.modo == "coletar":
        parser.error("Demonstração não pode executar adaptadores de coleta. Use --modo inventario.")
    fontes = carregar_fontes(BASE_DIR / "config/fontes.json")
    if args.demo:
        alvo, regioes, lotes, nomes = demo_geografia(args.regiao)
        cobertura = None
    else:
        cliente = ClienteSupabaseLeitura(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_PUBLISHABLE_KEY"))
        alvo, regioes, lotes, nomes = cliente.municipio_e_regiao(args.regiao)
        cobertura = cliente.cobertura_atuais()
    validar_geografia(args.regiao, lotes, nomes)
    linhas, resumo = gerar_inventario(args, alvo, regioes, lotes, nomes, fontes, cobertura)
    destino = Path(args.saida)
    salvar_inventario(destino, linhas, resumo)
    print(f"REGIÃO: {resumo['nome_regiao']} ({args.regiao}) | Lote: {resumo['lote']}")
    print(f"MUNICÍPIOS NO LOTE: {resumo['municipios_no_lote']}")
    print(f"POSIÇÕES PREFEITO/VEREADOR: {resumo['posicoes_prefeito_vereador']}")
    print(f"FONTES CONFIGURADAS: {resumo['fontes_configuradas']}")
    print(f"FONTES PARA IDENTIFICAR: {resumo['fontes_a_identificar']}")
    print(f"REGISTROS JÁ CADASTRADOS: {resumo['registros_atuais_em_categorias_do_lote'] if cobertura is not None else 'não consultados — DEMO'}")
    if args.modo == "coletar":
        status = coletar_previas(destino, linhas, fontes)
        resumo["coletas_realizadas"] = len(status)
        resumo["coletas_com_erro"] = sum(x["status"] != "previa_gerada" for x in status)
        (destino / "resumo.json").write_text(json.dumps(resumo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"PRÉVIAS GERADAS: {len(status) - resumo['coletas_com_erro']} | ERROS: {resumo['coletas_com_erro']}")
        if resumo["coletas_com_erro"]:
            raise RuntimeError("Falha em uma ou mais prévias oficiais. Examine resultado_coletas.json.")
    print(f"RELATÓRIOS: {destino.resolve()}")
    print("SEGURANÇA: nenhum dado foi escrito no Supabase.")
    return resumo


if __name__ == "__main__":
    try:
        executar()
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)
