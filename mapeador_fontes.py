#!/usr/bin/env python3
"""Autoridades SP — Mapeador Universal de Fontes 1.0.

Diagnóstico de URLs candidatas por município/plataforma. Somente leitura:
não extrai autoridades nem escreve no Supabase.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = Path(__file__).resolve().parent
TIMEOUT = 12
MAX_BYTES = 2_000_000
CATEGORIAS = {"prefeito", "vereador"}
OUTROS_DOMINIOS = {"sitenovo.cmmc.com.br"}


def normalizar(valor):
    s = unicodedata.normalize("NFKD", str(valor or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def url_publica(url):
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return (p.scheme == "https" and bool(host) and not p.username and
                not p.password and p.port in (None, 443) and
                (host.endswith(".gov.br") or host.endswith(".leg.br") or host in OUTROS_DOMINIOS) and
                not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host))
    except (ValueError, TypeError):
        return False


def mesmo_host(a, b):
    return str(a or "").lower().removeprefix("www.") == str(b or "").lower().removeprefix("www.")


def csv_seguro(valor):
    s = str(valor if valor is not None else "")
    return "'" + s if s.startswith(("=", "+", "-", "@", "\t", "\r")) else s


def ler_catalogo(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    fontes = doc.get("fontes")
    if not isinstance(fontes, list) or not fontes:
        raise ValueError("Catálogo de fontes vazio")
    vistos = set()
    for fonte in fontes:
        chave = (normalizar(fonte.get("municipio")), fonte.get("categoria"))
        if not chave[0] or chave[1] not in CATEGORIAS or chave in vistos:
            raise ValueError("Município/categoria inválido ou duplicado")
        vistos.add(chave)
        if fonte.get("plataforma_confirmada") is not False:
            raise ValueError("Plataforma não pode estar previamente homologada")
        if fonte.get("url_candidata") is not None and not url_publica(fonte["url_candidata"]):
            raise ValueError("URL candidata não permitida")
    return fontes


class HTMLResumo(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.link_atual = None
        self.rotulo = []
        self.textos = []
        self.gerador = ""
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("style", "script"):
            self._ignorar += 1
        if tag == "meta" and attrs.get("name", "").lower() == "generator":
            self.gerador = attrs.get("content", "")
        if tag == "a":
            self.link_atual = attrs.get("href", "")
            self.rotulo = []

    def handle_data(self, data):
        if self.link_atual is not None:
            self.rotulo.append(data)
        if not self._ignorar:
            self.textos.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.link_atual is not None:
            self.links.append((self.link_atual, " ".join(self.rotulo).strip()))
            self.link_atual = None
        if tag in ("style", "script"):
            self._ignorar = max(0, self._ignorar - 1)


def classificar(html, url):
    parser = HTMLResumo()
    parser.feed(html)
    baixo = html.lower()
    dominio = urlparse(url).hostname or ""
    if "siscam." in dominio or ("siscam" in baixo and "vereador" in baixo):
        familia, confianca, prova = "siscam", "alta", "assinatura SISCAM"
    elif "/sapl/" in url.lower() or "sistema de apoio ao processo legislativo" in baixo:
        familia, confianca, prova = "sapl", "alta", "assinatura SAPL"
    elif "wp-content/" in baixo or "wp-includes/" in baixo or "wordpress" in parser.gerador.lower():
        familia, confianca, prova = "wordpress", "media", "estrutura WordPress"
    elif 'id="__next"' in baixo or 'id="root"' in baixo or 'id="app"' in baixo:
        familia, confianca, prova = "app_javascript", "baixa", "aplicação JavaScript"
    else:
        familia, confianca, prova = "html_generico", "baixa", "HTML sem plataforma identificada"
    texto = normalizar(" ".join(parser.textos)[:180000])
    avisos = []
    if any(x in texto for x in ("licenciado", "licenciada", "suplente")):
        avisos.append("revisar exercício: possível licença ou suplência")
    if "legislatura" in texto and any(x in texto for x in ("2021 2024", "2017 2020", "18 legislatura")):
        avisos.append("há legislatura anterior; revisar vigência")
    promissores = []
    for href, rotulo in parser.links:
        alvo = urldefrag(urljoin(url, href)).url
        p = urlparse(alvo)
        if not url_publica(alvo) or not mesmo_host(p.hostname, urlparse(url).hostname):
            continue
        if any(p.path.lower().endswith(s) for s in (".jpg", ".png", ".gif", ".pdf", ".zip", ".css", ".js")):
            continue
        if any(s in normalizar(rotulo + " " + p.path) for s in ("vereador", "prefeito", "gabinete", "parlamentar", "legislatura")):
            if alvo not in promissores:
                promissores.append(alvo)
        if len(promissores) >= 8:
            break
    return {"familia_sugerida": familia, "confianca_tecnica": confianca,
            "evidencias": [prova], "links_promissores": promissores, "avisos": avisos}


class MesmoHost(HTTPRedirectHandler):
    def __init__(self, original):
        self.original = original

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_publica(newurl) or not mesmo_host(urlparse(newurl).hostname, self.original):
            raise RuntimeError("Redirecionamento entre domínios exige revisão")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def decodificar_html(body, charset):
    """Usa UTF-8 quando o servidor informa charset inválido (ex.: 'off')."""
    nome = str(charset or "utf-8").strip() or "utf-8"
    try:
        texto = body.decode(nome, errors="replace")
        return texto, None
    except (LookupError, ValueError):
        return body.decode("utf-8", errors="replace"), (
            f"Charset informado pelo servidor é inválido ({nome!r}); fallback UTF-8"
        )


def diagnosticar(url):
    vazio = {"familia_sugerida": "desconhecida", "confianca_tecnica": "nenhuma",
             "evidencias": [], "links_promissores": [], "avisos": []}
    if not url:
        return {**vazio, "situacao_http": "sem_url", "avisos": ["URL não cadastrada"]}
    if not url_publica(url):
        raise ValueError("URL rejeitada")
    try:
        request = Request(url, headers={
            "User-Agent": "AutoridadesSP-Mapeador/1.0 (somente leitura)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.3"
        })
        opener = build_opener(MesmoHost(urlparse(url).hostname))
        with opener.open(request, timeout=TIMEOUT) as resposta:
            mime = (resposta.headers.get("Content-Type") or "").lower()
            body = resposta.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise RuntimeError("Página acima do limite de tamanho")
            if "text/html" not in mime and "application/xhtml+xml" not in mime:
                raise RuntimeError("Conteúdo não é HTML")
            charset = resposta.headers.get_content_charset() or "utf-8"
            texto, aviso_charset = decodificar_html(body, charset)
            parsed = classificar(texto, resposta.geturl())
            if aviso_charset:
                parsed["avisos"].append(aviso_charset)
            return {**parsed, "situacao_http": "acessivel", "http_codigo": resposta.status,
                    "url_final": resposta.geturl()}
    except HTTPError as exc:
        situacao = "bloqueio_ou_restricao" if exc.code in (401, 403, 406, 429) else "erro_http"
        return {**vazio, "situacao_http": situacao, "http_codigo": exc.code,
                "avisos": [f"HTTP {exc.code}: conferir portal"]}
    except (URLError, RuntimeError, OSError, TimeoutError, UnicodeError) as exc:
        return {**vazio, "situacao_http": "falha_de_acesso", "avisos": [str(exc)[:180]]}


def gerar_linhas(fontes, nomes, codigos, modo, diagnostico=diagnosticar):
    mapa = {normalizar(nomes[c]): c for c in codigos}
    if len(mapa) != len(codigos):
        raise ValueError("Nomes de municípios duplicados")
    for fonte in fontes:
        if normalizar(fonte["municipio"]) not in mapa:
            raise ValueError("Fonte fora do lote oficial")
    indice = {(normalizar(f["municipio"]), f["categoria"]): f for f in fontes}
    saida = []
    for codigo in sorted(codigos, key=lambda x: normalizar(nomes[x])):
        nome = nomes[codigo]
        for categoria in ("prefeito", "vereador"):
            f = indice.get((normalizar(nome), categoria), {})
            url = f.get("url_candidata")
            try:
                status = (diagnostico(url) if modo == "verificar"
                          else {"situacao_http": "nao_testada", "familia_sugerida": "desconhecida",
                                "confianca_tecnica": "nenhuma", "evidencias": [], "links_promissores": [],
                                "avisos": ["Catálogo não testa acesso HTTP"]})
            except (ValueError, LookupError, RuntimeError, UnicodeError, TypeError) as exc:
                # Uma fonte defeituosa não deve abortar as outras 21 posições.
                status = {"situacao_http": "falha_de_analise", "familia_sugerida": "desconhecida",
                          "confianca_tecnica": "nenhuma", "evidencias": [], "links_promissores": [],
                          "avisos": [f"Erro nesta fonte: {type(exc).__name__}: {str(exc)[:150]}"]}
            saida.append({"codigo_ibge": codigo, "municipio": nome, "categoria": categoria,
                          "url_candidata": url or "", "tipo_pagina": f.get("tipo_pagina", ""),
                          **status, "status_homologacao": "PENDENTE", "gravou_no_supabase": False})
    return saida


def salvar(saida, linhas, modo, geografia):
    saida = Path(saida)
    saida.mkdir(parents=True, exist_ok=True)
    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regiao": "rmsp", "lote": "rmsp-leste", "modo": modo, "geografia": geografia,
        "municipios": len(set(r["codigo_ibge"] for r in linhas)),
        "posicoes": len(linhas),
        "urls_candidatas": sum(bool(r["url_candidata"]) for r in linhas),
        "situacoes_http": dict(Counter(r["situacao_http"] for r in linhas)),
        "familias_sugeridas": dict(Counter(r["familia_sugerida"] for r in linhas)),
        "fontes_homologadas_para_coleta": 0, "autoridades_extraidas": 0, "gravou_no_supabase": False,
        "aviso": "Diagnóstico preliminar; não certifica mandato, titularidade, plataforma ou e-mail."
    }
    (saida / "resumo_mapeamento.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (saida / "mapa_fontes.json").write_text(json.dumps(linhas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    colunas = ["codigo_ibge", "municipio", "categoria", "url_candidata", "tipo_pagina",
               "situacao_http", "http_codigo", "familia_sugerida", "confianca_tecnica",
               "links_promissores", "avisos", "status_homologacao"]
    with (saida / "mapa_fontes.csv").open("w", encoding="utf-8-sig", newline="") as fd:
        w = csv.DictWriter(fd, fieldnames=colunas, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in linhas:
            w.writerow({k: csv_seguro(" | ".join(r.get(k, [])) if isinstance(r.get(k), list) else r.get(k)) for k in colunas})
    with (saida / "pendencias.csv").open("w", encoding="utf-8-sig", newline="") as fd:
        w = csv.writer(fd, delimiter=";")
        w.writerow(["municipio", "categoria", "pendencia"])
        for r in linhas:
            motivo = ("Confirmar URL" if not r.get("url_candidata") else
                      "Revisar bloqueio/acesso" if r["situacao_http"] not in ("acessivel", "nao_testada") else
                      "Conferir plataforma, exercício, mandato e contato")
            w.writerow([csv_seguro(r["municipio"]), csv_seguro(r["categoria"]), motivo])
    return resumo


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--regiao", choices=["rmsp"], default="rmsp")
    cli.add_argument("--lote", choices=["rmsp-leste"], default="rmsp-leste")
    cli.add_argument("--modo", choices=["catalogo", "verificar"], default="catalogo")
    cli.add_argument("--demo", action="store_true")
    cli.add_argument("--saida", default="relatorios/mapeador-fontes")
    opt = cli.parse_args()
    if opt.demo and opt.modo != "catalogo":
        cli.error("Demo somente em modo catalogo")
    from motor_regional import ClienteSupabaseLeitura, demo_geografia, validar_geografia
    if opt.demo:
        _, _, lotes, nomes = demo_geografia(opt.regiao)
    else:
        client = ClienteSupabaseLeitura(os.environ.get("SUPABASE_URL"),
                                        os.environ.get("SUPABASE_PUBLISHABLE_KEY"))
        _, _, lotes, nomes = client.municipio_e_regiao(opt.regiao)
    validar_geografia(opt.regiao, lotes, nomes)
    codigos = set(lotes[opt.lote]) - {"3550308"}
    if len(codigos) != 11:
        raise ValueError(f"Esperados 11 municípios operacionais, retornaram {len(codigos)}")
    fontes = ler_catalogo(BASE / "config/fontes_candidatas_rmsp_leste.json")
    linhas = gerar_linhas(fontes, nomes, codigos, opt.modo)
    resumo = salvar(opt.saida, linhas, opt.modo,
                    "demonstracao" if opt.demo else "supabase")
    print("MAPEADOR UNIVERSAL RMSP LESTE |", opt.modo)
    print("Municípios:", resumo["municipios"], "| posições:", resumo["posicoes"])
    print("Situação HTTP:", resumo["situacoes_http"])
    print("Nenhuma autoridade extraída ou gravada no Supabase.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as e:
        print("ERRO:", e, file=sys.stderr)
        sys.exit(1)
