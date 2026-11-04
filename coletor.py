
import argparse
import os
import sys
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

FONTE_ALESP = "https://www.al.sp.gov.br/deputado/dados/"


def registrar_execucao(tipo, status, observacoes):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Credenciais do Supabase não configuradas.")

    resposta = requests.post(
        f"{SUPABASE_URL}/rest/v1/atualizacoes",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json={
            "tipo": tipo,
            "status": status,
            "iniciado_em": datetime.now(timezone.utc).isoformat(),
            "finalizado_em": datetime.now(timezone.utc).isoformat(),
            "registros_atualizados": 0,
            "registros_pendentes": 0,
            "observacoes": observacoes,
        },
        timeout=30,
    )
    resposta.raise_for_status()


def consultar_alesp():
    resposta = requests.get(
        FONTE_ALESP,
        headers={"User-Agent": "AutoridadesSP/1.0"},
        timeout=30,
    )
    resposta.raise_for_status()

    print("Fonte ALESP acessível.")
    print("Resposta HTTP:", resposta.status_code)
    print("Conteúdo recebido:", len(resposta.content), "bytes")

    # A extração dos deputados será implementada
    # depois de validar o formato da fonte oficial.
    return 0


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
            consultar_alesp()
            observacoes = (
                "Teste de acesso à fonte ALESP concluído. "
                "Nenhum cadastro foi importado."
            )
        else:
            observacoes = (
                "Coleta quinzenal ainda não implementada."
            )

        registrar_execucao(
            args.tipo,
            "teste_concluido",
            observacoes,
        )
        print(observacoes)

    except Exception as erro:
        print("Erro na execução:", erro, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
