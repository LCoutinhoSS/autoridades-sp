#!/usr/bin/env python3
"""Prévia segura dos vereadores da Câmara Municipal de São Paulo.

Não se conecta ao Supabase e não altera nenhum cadastro.
Fonte: https://www.saopaulo.sp.leg.br/vereadores-json/
Uso: python coletor_vereadores_sp.py --saida previa-vereadores-sp
"""

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FONTE = "https://www.saopaulo.sp.leg.br/vereadores-json/"
FONTE_GABINETES = "https://www.saopaulo.sp.leg.br/atividade-legislativa/gabinetes/"
DOMINIO_EMAIL = "saopaulo.sp.leg.br"
TOTAL_CADEIRAS = 55


def limpar(valor):
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def normalizar(valor):
    texto = unicodedata.normalize("NFKD", limpar(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.casefold()


def id_estavel(nome):
    # A mudança de nome na fonte poderá gerar outro ID: revisar antes da importação.
    resumo = hashlib.sha256(normalizar(nome).encode("utf-8")).hexdigest()[:20]
    return "cmsp-" + resumo


def sinal(valor):
    return normalizar(valor) in {"on", "sim", "true", "1"}


def partido_padronizado(valor):
    original = limpar(valor)
    mapa = {
        "uniao": "UNIÃO",
        "uniao brasil": "UNIÃO",
        "partido liberal": "PL",
        "pode": "PODEMOS",
        "podemos": "PODEMOS",
        "republicanos": "REPUBLICANOS",
        "pc do b": "PCdoB",
    }
    return mapa.get(normalizar(original), original.upper())


def primeiro_telefone(ramais):
    # Conserva a informação original no CSV; o cadastro utiliza só o primeiro ramal.
    trecho = limpar(ramais).split("/")[0]
    encontrado = re.search(r"3396\s*[- ]\s*(\d{4})", trecho)
    if encontrado:
        return f"(11) 3396-{encontrado.group(1)}"
    return None


def email_oficial(valor):
    email = limpar(valor).lower()
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", email):
        return None
    if not email.endswith("@" + DOMINIO_EMAIL):
        return None
    return email


def baixar_json():
    requisicao = Request(
        FONTE,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AutoridadesSP/4.0; coleta institucional)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urlopen(requisicao, timeout=45) as resposta:
            conteudo = resposta.read(2_500_000)
    except (HTTPError, URLError, TimeoutError) as erro:
        raise RuntimeError(f"Falha ao acessar fonte oficial: {erro}") from erro
    try:
        registros = json.loads(conteudo.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as erro:
        raise RuntimeError("A fonte não retornou um JSON válido.") from erro
    if not isinstance(registros, list) or len(registros) < 40:
        raise RuntimeError("Formato/quantidade da fonte inesperado; coleta interrompida.")
    if not all(isinstance(r, dict) for r in registros):
        raise RuntimeError("Formato de registros inesperado; coleta interrompida.")
    return registros


def preparar(registros):
    gerados, linhas = [], []
    nomes, ids, emails = set(), set(), set()
    licenciados = sum(sinal(r.get("Licenciado")) for r in registros)
    ativos_fonte = sum(sinal(r.get("Ativo")) for r in registros)
    erros, avisos = [], []
    momento = datetime.now(timezone.utc).isoformat()

    for r in registros:
        if not sinal(r.get("Ativo")) or sinal(r.get("Licenciado")):
            continue
        nome = limpar(r.get("Vereador"))
        partido = partido_padronizado(r.get("Partido"))
        if not nome or not partido:
            erros.append("Registro ativo sem nome ou partido.")
            continue

        identificador = id_estavel(nome)
        nome_chave = normalizar(nome)
        if identificador in ids or nome_chave in nomes:
            erros.append(f"Duplicidade de nome/ID: {nome}")
            continue
        ids.add(identificador)
        nomes.add(nome_chave)

        email_bruto = limpar(r.get("email"))
        email = email_oficial(email_bruto)
        if email_bruto and not email:
            avisos.append(f"E-mail fora do padrão oficial: {nome} — {email_bruto}")
        if email and email in emails:
            erros.append(f"E-mail repetido: {email} ({nome})")
        if email:
            emails.add(email)

        telefone = primeiro_telefone(r.get("Ramal"))
        if limpar(r.get("Ramal")) and not telefone:
            avisos.append(f"Ramal não interpretado: {nome}")

        registro = {
            "id": identificador,
            "name": nome,
            "kind": "vereador",
            "role": "Vereador(a)",
            "org": "Câmara Municipal de São Paulo",
            "city": "São Paulo",
            "partido": partido,
            "email": email,
            "phone": telefone,
            "source": FONTE,
            "fonte_oficial": FONTE_GABINETES,
            "ultima_verificacao": momento,
            # Encontrado em fonte oficial != caixa postal testada.
            "status_verificacao": "pendente",
            "status": "pendente",
        }
        gerados.append(registro)
        linhas.append({
            "nome": nome,
            "partido": partido,
            "partido_original": limpar(r.get("Partido")),
            "email": email or "",
            "telefone_principal": telefone or "",
            "ramais_na_fonte": limpar(r.get("Ramal")),
            "gabinete": limpar(r.get("GV")),
            "sala": limpar(r.get("Sala")),
            "suplente_em_exercicio": "sim" if sinal(r.get("Suplente")) else "não",
            "id_proposto": identificador,
            "situacao_email": "pendente",
        })

    gerados.sort(key=lambda r: normalizar(r["name"]))
    linhas.sort(key=lambda r: normalizar(r["nome"]))
    if len(gerados) < 40:
        erros.append(f"Apenas {len(gerados)} vereadores selecionados: revisar fonte.")
    if len(gerados) != TOTAL_CADEIRAS:
        avisos.append(
            f"A Câmara informa {TOTAL_CADEIRAS} cadeiras, mas foram selecionados "
            f"{len(gerados)} registros: revisar situação de suplentes/licenciados."
        )
    return gerados, linhas, {
        "fonte_oficial": FONTE,
        "coletado_em_utc": momento,
        "registros_no_json": len(registros),
        "marcados_ativos": ativos_fonte,
        "marcados_licenciados": licenciados,
        "selecionados_para_revisao": len(gerados),
        "com_email_institucional": sum(bool(r["email"]) for r in gerados),
        "sem_email_institucional": sum(not r["email"] for r in gerados),
        "com_telefone_principal": sum(bool(r["phone"]) for r in gerados),
        "erros": erros,
        "avisos": avisos,
        "importacao_realizada": False,
        "observacao": (
            "Prévia apenas. Nenhum dado foi gravado no Supabase. "
            "Verificação de caixa postal ainda pendente. Revisar nomes e exercício."
        ),
    }


def csv_seguro(valor):
    texto = str(valor or "")
    if texto.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + texto
    return texto


def executar(saida):
    registros = baixar_json()
    gerados, linhas, resumo = preparar(registros)
    destino = Path(saida)
    destino.mkdir(parents=True, exist_ok=True)

    arquivo_csv = destino / "previa_vereadores_sao_paulo.csv"
    with arquivo_csv.open("w", encoding="utf-8-sig", newline="") as arquivo:
        campos = [
            "nome", "partido", "partido_original", "email", "telefone_principal",
            "ramais_na_fonte", "gabinete", "sala", "suplente_em_exercicio",
            "id_proposto", "situacao_email",
        ]
        escritor = csv.DictWriter(arquivo, fieldnames=campos, delimiter=";")
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow({k: csv_seguro(v) for k, v in linha.items()})

    (destino / "previa_registros_supabase.json").write_text(
        json.dumps(gerados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (destino / "relatorio_coleta.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("FONTE:", FONTE)
    print("REGISTROS NA FONTE:", resumo["registros_no_json"])
    print("SELECIONADOS:", resumo["selecionados_para_revisao"])
    print("E-MAILS:", resumo["com_email_institucional"])
    print("TELEFONES PRINCIPAIS:", resumo["com_telefone_principal"])
    print("AVISOS:", len(resumo["avisos"]))
    for aviso in resumo["avisos"][:15]:
        print("  -", aviso)
    for erro in resumo["erros"]:
        print("ERRO:", erro)
    print("ARQUIVOS:", destino.resolve())
    print("SEGURANÇA: nenhum dado foi gravado no Supabase.")
    # Avisos de diferença com 55 não impedem baixar o relatório para análise.
    if resumo["erros"]:
        raise RuntimeError("Prévia com erros de integridade; não importar.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--saida", default="previa-vereadores-sp")
    argumentos = parser.parse_args()
    executar(argumentos.saida)
