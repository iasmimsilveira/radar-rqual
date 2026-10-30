# Radar RQUAL

Reproduz o Selo de Qualidade da Anatel (RQUAL) da telefonia móvel a partir de dados abertos e mostra onde agir para melhorar a nota: por indicador, por município e por estado.

Validação: o cálculo reproduz os selos nacionais publicados pela Anatel para o 1º semestre de 2025 (Claro B, TIM C, Vivo B).

## O que a página mostra

- **Selo nacional**: faixas de IQS (rede), IR (reclamações) e IQP (satisfação), a soma e a série por semestre.
- **Simulador**: efeito de corrigir um indicador nos municípios de maior impacto, de reduzir reclamações e de mudar o IQP.
- **Mapa**: selo municipal, hipótese de causa e pior indicador nos 5.570 municípios.
- **Ficha do município**: as três operadoras, os oito indicadores, a disponibilidade mês a mês e a queda de energia (ANEEL).
- **Lista de prioridades**: ação de maior ganho em cada município, ordenada pelo efeito no IQS nacional ou por quantos municípios saem do selo D/E.
- **Selo por estado**.

## Como rodar

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python pipeline/baixar.py      # cerca de 4 GB de bases públicas em raw/
.venv/bin/python pipeline/run_all.py     # gera data/rqual.duckdb e site/
python3 -m http.server 8765 --directory site
```

Depois abra http://localhost:8765.

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `pipeline/baixar.py` | Download das bases (Anatel, ANEEL, INPE, IBGE) |
| `pipeline/carga.py` | Carga e agregação no DuckDB |
| `pipeline/iqs.py` | IQS municipal conforme o DVR (normalização, pesos por posição) |
| `pipeline/selo.py` | Bônus de cobertura, consolidação UF/Brasil, IR, faixas e selo |
| `pipeline/analise.py` | Simulação do ganho no IQS por indicador (respeitando o teto de 100) e dados externos (ANEEL) |
| `pipeline/exportar_acesso.py` | `acesso.json` da área Rede de acesso (IND4, IND8, amostra significativa, INF4 = mediana de download) |
| `pipeline/exportar.py` | JSON lidos pela página |
| `pipeline/montar_site.py` | Gera `site/index.html` a partir de `web/radar.html` |
| `web/radar.html` | Código do site único: áreas Selo de Qualidade e Rede de acesso |
| `site/` | Site estático pronto para publicar |

## Hospedagem

`site/` é estático, então funciona em qualquer hospedagem gratuita:

- **Cloudflare Pages + Cloudflare Access**: acesso restrito a e-mails autorizados (o plano gratuito atende até 50 usuários).
- **GitHub Pages**: público. Use a versão neutra se for publicar abertamente.

O workflow `.github/workflows/atualizar.yml` baixa as bases, roda o pipeline e publica no GitHub Pages todo mês.

## Regra aplicada

Documento de Valores de Referência (Resolução Interna Anatel nº 397/2024):

- `norm = 10 × (média semestral − VRinf) / (VRsup − VRinf)`, limitado a 0..10
- pesos por posição: 36,7%, 18,4%, 12,3%, 9,2%, 7,4%, 6,1%, 5,3%, 4,6% (o pior indicador leva o maior peso)
- `IQSmun = 10 × Σ norm × peso` + 5 de bônus com cobertura 4G/5G ≥ 95%
- IQS da UF e do Brasil = média ponderada pelos acessos
- `IR = 1000 × reclamações / Σ acessos mensais`
- selo da UF e do Brasil = soma das faixas: 3–4 A, 5–7 B, 8–10 C, 11–13 D, 14–15 E

## Limites

- As hipóteses de causa são triagem, não diagnóstico. Confirme com dados internos de rede.
- O IR usa todas as reclamações de SMP na Anatel, e a regra oficial usa as "qualificáveis".
- O bônus usa a % de moradores cobertos, porque a cobertura da área urbana não é publicada.
- O IQP é anual e entra como parâmetro (`pipeline/selo.py`, `IQP_BR`).
