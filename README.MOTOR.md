# Autoridades SP — Motor de Coleta Regional 1.0

> **Estado:** piloto auditável e somente leitura. **Não grava nada no Supabase.** Não altera o site `index.html`. Não precisa executar SQL novo nesta etapa.

## Objetivo

Gerar um inventário de prefeitos e vereadores para os municípios de uma região oficial. Consulta `municipios`, `regioes`, `municipios_regioes` e as funções **já instaladas** `consultar_autoridades_seguras()` e `consultar_vinculos_municipais_seguros()` para identificar a cobertura existente.

A execução inicial é a **Região Metropolitana de São Paulo (RMSP)**, com 39 municípios e os cinco recortes legais. A capital integra as cinco sub-regiões para fins de pesquisa, mas **só é executada no lote geral**, salvo opção explícita. Os agrupamentos Alto Tietê, Grande ABC e Baixada Santista não são modificados.

A estrutura é compartilhada. Para municípios adicionais, cadastram-se suas fontes oficiais em `config/fontes.json` e, quando necessário, criam-se *adaptadores de formato*, não 645 scripts.

### Já implementado

- Inventário CSV e JSON por região/lote, incluindo município, código IBGE, categoria, existência de fonte configurada e quantidade de registros já presentes.
- Validação rigorosa dos vínculos da RMSP: 39 municípios; Norte 6, Leste 12, Sudeste 8, Sudoeste 9 e Oeste 8 (capital contada em todas as cinco subdivisões).
- Reaproveitamento do `coletor_vereadores_sp.py` **sem alterações**, com fonte oficial para os 55 vereadores da capital. O coletor gera somente uma prévia; faz alerta de quantidade/divergências.
- Relatórios de fontes pendentes. Uma fonte não configurada **não significa** inexistência de prefeito ou vereador.
- Modo demonstração sem rede para testar o motor. Nesse modo os códigos dos municípios, exceto a capital, são **sintéticos** e nunca devem ser importados.
- Workflow manual do GitHub Actions sem qualquer escrita no Supabase.

### Ainda não implementado (deliberadamente)

- Adaptadores das outras câmaras, inclusive SAPL, antes de validar o site e o exercício/mandato de cada fonte.
- Coletores dos prefeitos, escolha de fontes oficiais específicas de cada prefeitura e secretários estaduais (estes terão fluxo próprio estadual, não multiplicado por região).
- Atualização automática de autoridades, por exigir conciliação de titulares, fontes, verificação de e-mails e respeito aos ajustes manuais.
- Agendamento automático. Só ativar quando fontes e alertas estiverem validados.

## 1) Como colocar no GitHub

No repositório `autoridades-sp` na branch `main`, adicione **o conteúdo desta pasta** na raiz, preservando as subpastas `adaptadores`, `config`, `testes` e `.github/workflows`. **Não substitua `index.html` nem remova o coletor antigo.** Se baixar ZIP, descompacte-o antes de subir os arquivos; um ZIP sozinho não é executado pelo Actions.

Em **Settings → Secrets and variables → Actions → Variables**, adicione duas *Repository variables* (não secrets privilegiados):

- `SUPABASE_URL`: a URL pública do seu projeto Supabase, que o site já utiliza.
- `SUPABASE_PUBLISHABLE_KEY`: a chave **publishable/anon** pública, a mesma usada pela consulta do site. **Nunca** use `service_role` nem uma chave `sb_secret_...`.

São usadas apenas em GETs de endpoints públicos que já possuem RLS/RPC. Não colocamos a chave privilegiada do banco no GitHub nem em arquivos HTML.

## 2) Rodar pelo GitHub Actions

Entre em **Actions → Motor Regional — Inventario/Previas → Run workflow**, escolhendo:

- `modo: inventario`
- `lote: geral`

O fluxo gera um artefato ZIP em **Artifacts** contendo:

- `resumo.json`: 39 municípios esperados, 78 posições de fonte (39 × prefeito/vereador), número de fontes configuradas e registros efetivamente existentes;
- `inventario.csv`: uma linha por município/categoria, com pendências de descoberta de fonte;
- `inventario.json`: a mesma informação estruturada.

Na configuração inicial há **uma fonte já configurada** (vereadores da capital) e **77 fontes que ainda precisam ser localizadas/configuradas**. Isso não equivale a dizer que faltam 77 autoridades, apenas que faltam fontes para coleta automatizada. O inventário real deve informar a cobertura atual vinda do Supabase (56 registros municipais neste momento, se os dados não mudaram).

Para isolar um problema em um grupo, escolha `lote: rmsp-leste`, `rmsp-sudeste` etc. O lote Leste terá 11 municípios operacionais, pois a capital é excluída para não executar cinco vezes; para pesquisa, ela continua legalmente integrada às cinco sub-regiões.

Após validar o inventário, rode `modo: coletar`, `lote: geral`. O motor aciona somente os adaptadores **explicitamente configurados**. No momento, gera a prévia dos vereadores da capital em `previas/3550308-vereador/`; não altera cadastro, status de e-mail ou auditoria.

## 3) Teste local (opcional)

Na raiz desta pasta, usando Python 3.12+:

```bash
python -m unittest discover -s testes -v
python motor_regional.py --regiao rmsp --demo --modo inventario --saida relatorios/demo
```

Para executar de verdade, defina as variáveis de ambiente `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY` antes de executar `python motor_regional.py --regiao rmsp --modo inventario`.

**Observação:** o GitHub Actions **não** usa o modo demonstração, pois deve consultar o Supabase real.

## 4) Garantias e limites

- **Apenas leitura do banco**: não existe lógica de `INSERT`, `UPDATE`, `DELETE`, `upsert`, nem acesso à chave `service_role`.
- Alterações manuais e e-mails verificados permanecem intactos; relatórios são propostas para revisão.
- Nomes/e-mails da prévia da capital vêm do coletor oficial já usado anteriormente. Fonte oficial consultável não é prova de entregabilidade do e-mail.
- Os dados podem mudar com licenças, suplências, eleições e alterações nas páginas; toda inclusão/alteração futura deve passar por comparação, validação e auditoria.
- O pacote não garante que todas as fontes oficiais tenham APIs e não inventa portais ou endereços para cidades ainda não configuradas.
- Em produção, use o catálogo geográfico do Supabase como **fonte principal**. O arquivo `rmsp_referencia.json` é exclusivamente um snapshot para testes offline, não uma segunda fonte de verdade.

## Próxima etapa planejada

Validar o primeiro inventário real da RMSP. Depois cadastrar/confirmar fontes para o **lote Leste**, investigar portais compartilhados (ex.: SAPL) e desenvolver o primeiro adaptador reutilizável para várias câmaras — sempre com prévia e revisão antes de ativar gravações administrativas.

**Referências:**

- ALESP, Lei Complementar nº 1.139/2011: https://www.al.sp.gov.br/repositorio/legislacao/lei.complementar/2011/lei.complementar-1139-16.06.2011.html
- Câmara Municipal de São Paulo: https://www.saopaulo.sp.leg.br/vereadores/membros/
- Interlegis SAPL: https://www12.senado.leg.br/interlegis/produtos/sapl
