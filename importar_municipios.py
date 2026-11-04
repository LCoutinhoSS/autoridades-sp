"""Importa municípios paulistas e 3 agrupamentos regionais no Supabase.
Executar via GitHub Actions; nunca colocar a chave secreta no frontend.
"""
import os
import sys
import time
import unicodedata
import requests

IBGE_URL = 'https://servicodados.ibge.gov.br/api/v1/localidades/estados/35/municipios'
BASE = os.environ['SUPABASE_URL'].rstrip('/') + '/rest/v1'
KEY = os.environ['SUPABASE_SECRET_KEY']
SESSION = requests.Session()
SESSION.headers.update({'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
                        'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates,return=minimal'})

REGIONS = {
    'alto-tiete': ['Arujá', 'Biritiba-Mirim', 'Ferraz de Vasconcelos', 'Guararema', 'Itaquaquecetuba', 'Mogi das Cruzes', 'Poá', 'Salesópolis', 'Santa Isabel', 'Suzano'],
    'grande-abc': ['Santo André', 'São Bernardo do Campo', 'São Caetano do Sul', 'Diadema', 'Mauá', 'Ribeirão Pires', 'Rio Grande da Serra'],
    'baixada-santista': ['Bertioga', 'Cubatão', 'Guarujá', 'Itanhaém', 'Mongaguá', 'Peruíbe', 'Praia Grande', 'Santos', 'São Vicente'],
}

def normalize(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s.casefold()) if unicodedata.category(c) != 'Mn')

def request(method, url, **kwargs):
    for attempt in range(4):
        try:
            res = SESSION.request(method, url, timeout=40, **kwargs)
            if res.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f'{res.status_code}: {res.text[:250]}')
            res.raise_for_status()
            return res
        except (requests.RequestException, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

def upsert(table, rows, key):
    for i in range(0, len(rows), 100):
        batch = rows[i:i+100]
        request('POST', f'{BASE}/{table}?on_conflict={key}', json=batch)
        print(f'{table}: {min(i+100, len(rows))}/{len(rows)}')

def main():
    res = request('GET', IBGE_URL, headers={'Accept': 'application/json'})
    raw = res.json()
    if not isinstance(raw, list) or len(raw) != 645:
        sys.exit(f'IMPORTAÇÃO CANCELADA: esperado 645 municípios, recebido {len(raw) if isinstance(raw,list) else "formato inválido"}.')
    rows = []
    for m in raw:
        code = str(m['id'])
        if len(code) != 7 or not code.startswith('35'):
            sys.exit(f'Código IBGE inesperado: {code}')
        immediate = m.get('regiao-imediata') or {}
        intermediate = immediate.get('regiao-intermediaria') or {}
        rows.append({'codigo_ibge': code, 'nome': m['nome'], 'uf': 'SP',
                     'regiao_imediata': immediate.get('nome'),
                     'regiao_intermediaria': intermediate.get('nome')})
    if len({r['codigo_ibge'] for r in rows}) != 645:
        sys.exit('Códigos IBGE duplicados; importação cancelada.')
    lookup = {normalize(r['nome']): r['codigo_ibge'] for r in rows}
    links = []
    for region, names in REGIONS.items():
        for name in names:
            code = lookup.get(normalize(name))
            if not code:
                sys.exit(f'Município não encontrado no IBGE: {name}')
            links.append({'codigo_ibge': code, 'regiao_id': region})
    upsert('municipios', rows, 'codigo_ibge')
    upsert('municipios_regioes', links, 'codigo_ibge,regiao_id')
    print(f'CONCLUÍDO: {len(rows)} municípios; {len(links)} vínculos nas 3 regiões. Nenhuma autoridade alterada.')

if __name__ == '__main__':
    main()
