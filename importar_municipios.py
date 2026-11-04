"""Importa municípios paulistas e três agrupamentos regionais no Supabase.
Usa municipios-sp.json (cópia da API oficial do IBGE) na raiz do repositório.
Executar via GitHub Actions com SUPABASE_URL e SUPABASE_SECRET_KEY.
"""
import json
import os
from pathlib import Path
import sys
import time
import unicodedata
import requests

BASE = os.environ['SUPABASE_URL'].rstrip('/') + '/rest/v1'
KEY = os.environ['SUPABASE_SECRET_KEY']
JSON_FILE = Path(__file__).resolve().parent / 'municipios-sp.json'
SESSION = requests.Session()
SESSION.headers.update({
    'apikey': KEY,
    'Authorization': 'Bearer ' + KEY,
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
})
REGIONS = {
    'alto-tiete': ['Arujá', 'Biritiba-Mirim', 'Ferraz de Vasconcelos', 'Guararema', 'Itaquaquecetuba', 'Mogi das Cruzes', 'Poá', 'Salesópolis', 'Santa Isabel', 'Suzano'],
    'grande-abc': ['Santo André', 'São Bernardo do Campo', 'São Caetano do Sul', 'Diadema', 'Mauá', 'Ribeirão Pires', 'Rio Grande da Serra'],
    'baixada-santista': ['Bertioga', 'Cubatão', 'Guarujá', 'Itanhaém', 'Mongaguá', 'Peruíbe', 'Praia Grande', 'Santos', 'São Vicente'],
}

def normalize(value):
    text = ''.join(c for c in unicodedata.normalize('NFD', str(value).casefold()) if unicodedata.category(c) != 'Mn')
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())

def request(method, url, **kwargs):
    for attempt in range(4):
        try:
            response = SESSION.request(method, url, timeout=40, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if attempt == 3 or (isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code not in (429, 500, 502, 503, 504)):
                raise
            print(f'Tentativa {attempt + 1} falhou: {exc}', flush=True)
            time.sleep(2 ** attempt)

def upsert(table, rows, conflict):
    for i in range(0, len(rows), 100):
        request('POST', f'{BASE}/{table}?on_conflict={conflict}', json=rows[i:i + 100])
        print(f'{table}: {min(i + 100, len(rows))}/{len(rows)}', flush=True)

def main():
    if not JSON_FILE.is_file():
        sys.exit(f'Arquivo ausente: {JSON_FILE.name}. Envie-o para a raiz do repositório.')
    with JSON_FILE.open(encoding='utf-8-sig') as file:
        raw = json.load(file)
    if not isinstance(raw, list) or len(raw) != 645:
        sys.exit(f'IMPORTAÇÃO CANCELADA: esperados 645 municípios, recebidos {len(raw) if isinstance(raw, list) else "formato inválido"}.')
    rows = []
    for item in raw:
        code = str(item['id'])
        if len(code) != 7 or not code.startswith('35'):
            sys.exit(f'Código IBGE inesperado: {code}')
        immediate = item.get('regiao-imediata') or {}
        intermediate = immediate.get('regiao-intermediaria') or {}
        rows.append({
            'codigo_ibge': code,
            'nome': item['nome'],
            'uf': 'SP',
            'regiao_imediata': immediate.get('nome'),
            'regiao_intermediaria': intermediate.get('nome'),
        })
    if len({row['codigo_ibge'] for row in rows}) != 645:
        sys.exit('Códigos IBGE duplicados; importação cancelada.')
    lookup = {normalize(row['nome']): row['codigo_ibge'] for row in rows}
    links = []
    for region, names in REGIONS.items():
        for name in names:
            code = lookup.get(normalize(name))
            if not code:
                sys.exit(f'Município não encontrado no IBGE: {name}')
            links.append({'codigo_ibge': code, 'regiao_id': region})
    assert len(links) == 26
    print('Arquivo local validado: 645 municípios e 26 vínculos regionais.', flush=True)
    upsert('municipios', rows, 'codigo_ibge')
    upsert('municipios_regioes', links, 'codigo_ibge,regiao_id')
    print('CONCLUÍDO: 645 municípios; 26 vínculos. Nenhuma autoridade alterada.', flush=True)

if __name__ == '__main__':
    main()
