
import argparse
import hashlib
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
FONTE_ALESP = "https://www.al.sp.gov.br/deputado/dados/"


def agora():
    return datetime.now(timezone.utc).isoformat()


def limpar(valor):
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def normalizar(valor):
    texto = unicodedata.normalize("NFKD", limpar(valor))
    return "".join(
        letra for letra in texto if not unicodedata.combining(letra)
    ).lower()


def cabecalhos_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Credenciais do Supabase não configuradas.")

    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
    }

    # Chaves modernas sb_secret_ são usadas como apikey.
    if not SUPABASE_KEY.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"

    return headers


def registrar_execucao(tipo, status, quantidade, observacoes):
    resposta = requests.post(
        f"{SUPABASE_URL}/rest/v1/atualizacoes",
        headers=cabecalhos_supabase(),
        json={
            "tipo": tipo,
            "status": status,
            "iniciado_em": agora(),
            "finalizado_em": agora(),
            "registros_atualizados": quantidade,
            "registros_pendentes": 0,
            "observacoes": observacoes[:1000],
        },
        timeout=30,
    )
    resposta.raise_for_status()


def encontrar_colunas(tabela):
    for linha in tabela.find_all("tr"):
        celulas = linha.find_all(["th", "td"], recursive=False)
        nomes = [normalizar(c.get_text(" ", strip=True)) for c in celulas]

        def localizar(opcoes):
            for indice, nome in enumerate(nomes):
                if any(opcao in nome for opcao in opcoes):
                    return indice
            return None

        colunas = {
            "nome": localizar(["deputado", "parlamentar"]),
            "partido": localizar(["partido"]),
            "email": localizar(["email", "e-mail"]),
            "regiao": localizar(["base eleitoral"]),
        }

        if all(valor is not None for valor in colunas.values()):
            return linha, colunas

    return None, None


def coletar_alesp():
    print("Consultando fonte oficial:", FONTE_ALESP)

    resposta = requests.get(
        FONTE_ALESP,
        headers={"User-Agent": "Mozilla/5.0 AutoridadesSP/1.0"},
        timeout=45,
    )
    resposta.raise_for_status()

    soup = BeautifulSoup(resposta.content, "html.parser")

    registros = []

    for tabela in soup.find_all("table"):
        cabecalho, colunas = encontrar_colunas(tabela)

        if colunas is None:
            continue

        linhas = tabela.find_all("tr")
        inicio = linhas.index(cabecalho) + 1

        for linha in linhas[inicio:]:
            celulas = linha.find_all(["td", "th"], recursive=False)

            if len(celulas) <= max(colunas.values()):
                continue

            def valor(campo):
                return limpar(
                    celulas[colunas[campo]].get_text(" ", strip=True)
                )

            nome = valor("nome")
            partido = valor("partido")
            regiao = valor("regiao")

            celula_email = celulas[colunas["email"]]
            email = ""

            link = celula_email.find("a", href=re.compile("^mailto:", re.I))
            if link:
                email = link["href"].split(":", 1)[1].split("?")[0].strip()
            else:
                texto_email = celula_email.get_text(" ", strip=True)
                encontrado = re.search(
                    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
                    texto_email,
                )
                if encontrado:
                    email = encontrado.group(0)

            if not nome or not partido:
                continue

            identificador = hashlib.sha256(
                normalizar(nome).encode("utf-8")
            ).hexdigest()[:20]

            registros.append({
                "id": f"alesp-{identificador}",
                "name": nome,
                "kind": "deputado_estadual",
                "role": "Deputado estadual",
                "org": "Assembleia Legislativa do Estado de São Paulo",
                "city": None,
                "partido": partido,
                "regiao_atuacao": regiao or None,
                "email": email or None,
                "source": FONTE_ALESP,
                "fonte_oficial": FONTE_ALESP,
                "ultima_verificacao": agora(),
                "status_verificacao": (
                    "verificado" if email else "pendente"
                ),
            })

        if registros:
            break

    # Evita gravar uma coleta incompleta por mudança no site.
    if len(registros) < 50:
        raise RuntimeError(
            f"Apenas {len(registros)} deputados identificados. "
            "Importação cancelada para evitar dados incompletos."
        )

    ids = [item["id"] for item in registros]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Identificadores duplicados na coleta.")

    print("Deputados encontrados:", len(registros))
    print("Com e-mail:", sum(bool(r["email"]) for r in registros))
    print("Com região:", sum(bool(r["regiao_atuacao"]) for r in registros))

    return registros


def salvar_autoridades(registros):
    headers = cabecalhos_supabase()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    total = 0

    for inicio in range(0, len(registros), 50):
        lote = registros[inicio:inicio + 50]

        resposta = requests.post(
            f"{SUPABASE_URL}/rest/v1/autoridades",
            params={"on_conflict": "id"},
            headers=headers,
            json=lote,
            timeout=45,
        )

        if not resposta.ok:
            raise RuntimeError(
                f"Erro Supabase {resposta.status_code}: "
                f"{resposta.text[:500]}"
            )

        total += len(lote)

    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tipo",
        choices=["semanal", "quinzenal"],
        required=True,
    )
    args = parser.parse_args()

    print("Iniciando coleta:", args.tipo)

    try:
        if args.tipo == "semanal":
            registros = coletar_alesp()
            quantidade = salvar_autoridades(registros)

            observacoes = (
                f"ALESP: {quantidade} deputados processados. "
                "Nome, partido, e-mail e base eleitoral."
            )

            registrar_execucao(
                args.tipo, "concluido", quantidade, observacoes
            )
            print(observacoes)
            print("Importação concluída com sucesso.")

        else:
            print("Coleta quinzenal ainda não implementada.")
            registrar_execucao(
                args.tipo,
                "nao_implementado",
                0,
                "Aguardando implementação dos municípios.",
            )

    except Exception as erro:
        print("ERRO:", erro, file=sys.stderr)

        try:
            registrar_execucao(
                args.tipo, "erro", 0, str(erro)
            )
        except Exception as erro_log:
            print("Erro ao registrar log:", erro_log, file=sys.stderr)

        sys.exit(1)


if __name__ == "__main__":
    main()
