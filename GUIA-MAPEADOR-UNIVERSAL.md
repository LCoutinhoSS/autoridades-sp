# Autoridades SP — Mapeador Universal de Fontes 1.0

## Objetivo
Mapear portais oficiais de câmaras e prefeituras, identificar indícios de plataforma e registrar fontes ainda não homologadas. O mapeador NÃO extrai nomes/e-mails de autoridades e NÃO grava no Supabase. É uma etapa anterior à criação de adaptadores reutilizáveis por plataforma.

## Estrutura
- `mapeador_fontes.py`: motor de diagnóstico somente leitura.
- `config/fontes_candidatas_rmsp_leste.json`: 22 URLs candidatas, 11 câmaras + 11 prefeituras da RMSP Leste, excluída a capital.
- `testes/test_mapeador_fontes.py`: testes locais sem acesso à rede.
- `.github/workflows/mapeador-fontes.yml`: workflow manual, relatórios anexados como artifacts.

## Execução pelo GitHub
Actions → **Mapeador Universal — Fontes Oficiais** → Run workflow.
- Primeiro: modo `catalogo`, que cruza o catálogo com os 11 municípios da RMSP Leste no Supabase sem acessar portais.
- Depois: modo `verificar`, que acessa as URLs, registra status HTTP e tenta reconhecer plataformas.
- O workflow requer variáveis públicas do repositório `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY` (já configuradas para o motor regional). **Não usar service_role ou chave secreta.**
- Baixar artifact `mapeamento-rmsp-leste-catalogo` ou `mapeamento-rmsp-leste-verificar` em cada execução.

## Como interpretar
- `mapa_fontes.csv`: código IBGE, município, categoria, URL candidata, tipo de página, status HTTP, plataforma sugerida, confiança, links, alertas e status de homologação.
- `mapa_fontes.json`: mesmos dados em estrutura completa.
- `resumo_mapeamento.json`: totais de fontes e situações HTTP.
- `pendencias.csv`: motivos de revisão por instituição.
- `acessivel` indica que uma página respondeu com HTML. Não comprova identidade, e-mail, legislatura vigente ou mandato.
- `bloqueio_ou_restricao` e `falha_de_acesso` não significam que a instituição não existe.
- `familia_sugerida` é hipótese de plataforma. Exige confirmação antes de conectar um extrator.
- Todas as fontes começam com `status_homologacao = PENDENTE`.
- A capital integra legalmente a RMSP Leste, mas foi excluída deste lote operacional para evitar coleta duplicada.

## Próximos passos
1. Analisar o artifact de `catalogo`: esperar 11 municípios e 22 posições de fonte.
2. Analisar o artifact de `verificar`, priorizando padrões repetidos e falhas.
3. Confirmar manualmente os portais e as legislaturas em exercício.
4. Desenvolver adaptadores por plataforma, começando pela com maior cobertura comprovada.
5. Só posteriormente construir sincronização segura com Supabase, preservando ajustes humanos e verificações.

## Segurança e limites
- URLs devem ser HTTPS em domínios governamentais/legislativos brasileiros, mais exceção explicitamente cadastrada para portal institucional de Mogi das Cruzes.
- Limite de 2 MB por resposta e timeout de 12 segundos por URL.
- Redirecionamentos entre domínios não são seguidos silenciosamente.
- Dados dos relatórios podem conter textos oriundos de páginas externas e devem ser tratados como não confiáveis.
- Arquivos atuais `index.html`, `motor_regional.py` e `.github/workflows/motor-regional.yml` devem permanecer intactos.
