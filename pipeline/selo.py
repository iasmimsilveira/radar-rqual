"""Bônus de cobertura, consolidação estadual/nacional, IR e selo (SMP).

Regras (DVR, RI Anatel nº 397/2024):
- bônus +5 no IQSmun quando a cobertura 4G ou superior da área urbana for >= 95%
  (aproximação: % de moradores cobertos por 4G/5G, base Meu Município da Anatel)
- IQSuf e IQSbr = média de IQSmun ponderada por acessos
- IR = 1000 * reclamações / soma dos acessos de todos os meses do período
- faixas IR do SMP: <0,1 F1 | <0,2 F2 | <0,3 F3 | <=0,4 F4 | >0,4 F5
- faixas IQP: >=9 F1 | >=7 F2 | >=5 F3 | >=3 F4 | resto F5
- selo estadual/nacional = soma das faixas de IQS, IR e IQP: 3-4 A | 5-7 B | 8-10 C | 11-13 D | 14-15 E
"""
import duckdb

from iqs import faixa_sql

# A Anatel grafa o mesmo grupo com e sem acento ("TELEFÔNICA" no início de 2022); por isso a comparação ignora acentos.
GRUPOS = {"TELECOM ITALIA": "TIM", "TELEFONICA": "VIVO", "TELECOM AMERICAS": "CLARO"}
PRODUTOS_PESSOAIS = ("VOZ+DADOS", "DADOS", "VOZ")

# Índice de Satisfação Geral (ISG) da Pesquisa de Satisfação e Qualidade Percebida da Anatel,
# 11ª edição (campo de jul/2025 a fev/2026), usado como IQP do SMP: média simples de pós-pago e pré-pago.
# Pós-pago: Vivo 7,87 · Claro 7,72 · TIM 7,09. Pré-pago: Claro 8,02 · Vivo 7,88 · TIM 7,73.
ISG_2025 = {"TIM": (7.09, 7.73), "VIVO": (7.87, 7.88), "CLARO": (7.72, 8.02)}
IQP_BR = {op: round(sum(v) / 2, 2) for op, v in ISG_2025.items()}


def build(con: duckdb.DuckDBPyConnection) -> None:
    grupos = " ".join(f"WHEN '{g}' THEN '{p}'" for g, p in GRUPOS.items())
    produtos = ", ".join(f"'{p}'" for p in PRODUTOS_PESSOAIS)
    con.execute(f"""
    CREATE OR REPLACE TABLE acessos_prest AS
    SELECT ibge, CASE strip_accents(grupo) {grupos} END AS prestadora, ano, mes,
           ano * 10 + CASE WHEN mes <= 6 THEN 1 ELSE 2 END AS semestre,
           SUM(acessos) AS acessos
    FROM acessos_smp
    WHERE strip_accents(grupo) IN ({", ".join(f"'{g}'" for g in GRUPOS)}) AND tipo_produto IN ({produtos})
    GROUP BY ALL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cobertura_4g AS
    SELECT "Código IBGE" AS ibge, "Operadora" AS prestadora,
           CAST("Ano" AS INT) * 100 + CAST("Mês" AS INT) AS ref,
           TRY_CAST(REPLACE("% moradores cobertos", ',', '.') AS DOUBLE) AS pct_moradores
    FROM read_csv('raw/anatel/meu_municipio/Meu_Municipio_Cobertura.csv', delim=';', header=true, all_varchar=true)
    WHERE "Tecnologia" = '4G5G' AND "Operadora" IN ('TIM', 'VIVO', 'CLARO')
    """)

    # Para cada semestre, usa o retrato de cobertura mais recente até o fim do semestre.
    con.execute(f"""
    CREATE OR REPLACE TABLE smp_iqs_mun_final AS
    WITH sem_ref AS (
        SELECT DISTINCT semestre, (semestre // 10) * 100 + CASE WHEN semestre % 10 = 1 THEN 6 ELSE 12 END AS fim
        FROM smp_iqs_mun
    ),
    cob AS (
        SELECT s.semestre, c.ibge, c.prestadora,
               ARG_MAX(c.pct_moradores, c.ref) FILTER (WHERE c.ref <= s.fim) AS pct_4g_ate,
               ARG_MIN(c.pct_moradores, c.ref) AS pct_4g_primeiro
        FROM sem_ref s CROSS JOIN cobertura_4g c
        GROUP BY ALL
    ),
    acc AS (
        SELECT semestre, ibge, prestadora, AVG(acessos) AS acessos_medio
        FROM acessos_prest GROUP BY ALL
    )
    SELECT m.*,
           COALESCE(cob.pct_4g_ate, cob.pct_4g_primeiro) AS pct_4g,
           CASE WHEN COALESCE(cob.pct_4g_ate, cob.pct_4g_primeiro) >= 95 THEN 5 ELSE 0 END AS bonus,
           LEAST(100, m.iqs_base + CASE WHEN COALESCE(cob.pct_4g_ate, cob.pct_4g_primeiro) >= 95 THEN 5 ELSE 0 END) AS iqs,
           acc.acessos_medio
    FROM smp_iqs_mun m
    LEFT JOIN cob USING (semestre, ibge, prestadora)
    LEFT JOIN acc USING (semestre, ibge, prestadora)
    """)
    con.execute(f"ALTER TABLE smp_iqs_mun_final ADD COLUMN faixa INT")
    con.execute(f"UPDATE smp_iqs_mun_final SET faixa = {faixa_sql('iqs')}")

    con.execute("""
    CREATE OR REPLACE TABLE smp_ir AS
    WITH r AS (
        SELECT ano * 10 + CASE WHEN mes <= 6 THEN 1 ELSE 2 END AS semestre, uf, marca AS prestadora, SUM(n) AS reclamacoes
        FROM reclamacoes WHERE marca IN ('TIM', 'VIVO', 'CLARO')
          AND ano * 100 + mes IN (SELECT DISTINCT ano * 100 + mes FROM acessos_prest)
          -- mesmo período dos indicadores do IQS, para IQS e IR cobrirem os mesmos meses
          AND ano * 100 + mes <= (SELECT MAX(ano * 100 + mes) FROM smp_mensal)
        GROUP BY ALL
    ),
    a AS (
        SELECT a.semestre, m.uf, a.prestadora, SUM(a.acessos) AS acessos_meses
        FROM acessos_prest a
        JOIN (SELECT DISTINCT ibge, uf FROM smp_iqs_mun) m USING (ibge)
        WHERE a.ano * 100 + a.mes <= (SELECT MAX(ano * 100 + mes) FROM smp_mensal)
        GROUP BY ALL
    )
    SELECT a.semestre, a.uf, a.prestadora, COALESCE(r.reclamacoes, 0) AS reclamacoes, a.acessos_meses,
           1000.0 * COALESCE(r.reclamacoes, 0) / a.acessos_meses AS ir
    FROM a LEFT JOIN r USING (semestre, uf, prestadora)
    """)


def faixa_ir_sql(col: str) -> str:
    return f"""CASE WHEN {col} < 0.1 THEN 1 WHEN {col} < 0.2 THEN 2 WHEN {col} < 0.3 THEN 3
        WHEN {col} <= 0.4 THEN 4 ELSE 5 END"""


def faixa_iqp(v: float) -> int:
    return 1 if v >= 9 else 2 if v >= 7 else 3 if v >= 5 else 4 if v >= 3 else 5


def selo(soma: int) -> str:
    return "A" if soma <= 4 else "B" if soma <= 7 else "C" if soma <= 10 else "D" if soma <= 13 else "E"


def consolidado(con: duckdb.DuckDBPyConnection, semestres: list[int], nivel: str = "br", ir_semestres: list[int] = None):
    """Retorna IQS, IR, faixas e selo por prestadora (nível 'br') ou por prestadora e UF (nível 'uf').

    Para um período anual (código 9AAAA), passe em ir_semestres os semestres que compõem o IR.
    """
    sems = ", ".join(str(s) for s in semestres)
    ir_sems = ", ".join(str(s) for s in (ir_semestres or semestres))
    chave = "prestadora" if nivel == "br" else "prestadora, uf"
    rows = con.execute(f"""
    WITH iqs AS (
        SELECT {chave}, SUM(iqs * acessos_medio) / SUM(acessos_medio) AS iqs
        FROM smp_iqs_mun_final
        WHERE semestre IN ({sems}) AND iqs IS NOT NULL AND acessos_medio > 0 AND prestadora IN ('TIM', 'VIVO', 'CLARO')
        GROUP BY ALL
    ),
    ir AS (
        SELECT {chave}, 1000.0 * SUM(reclamacoes) / SUM(acessos_meses) AS ir
        FROM smp_ir WHERE semestre IN ({ir_sems}) GROUP BY ALL
    )
    SELECT {chave}, iqs.iqs, ir.ir, {faixa_sql('iqs.iqs')} AS f_iqs, {faixa_ir_sql('ir.ir')} AS f_ir
    FROM iqs JOIN ir USING ({chave})
    ORDER BY ALL
    """).fetchall()
    out = []
    for r in rows:
        prest = r[0]
        f_iqp = faixa_iqp(IQP_BR[prest])
        soma = r[-2] + r[-1] + f_iqp
        out.append((*r, f_iqp, soma, selo(soma)))
    return out
