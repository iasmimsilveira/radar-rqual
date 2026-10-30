"""Períodos anuais exibidos na página, tratados como "semestres" com código próprio (9AAAA).

- IQS anual do município = média dos IQS semestrais (DVR, art. 13). Na prévia do ano corrente,
  o 2º semestre entra com os meses já publicados.
- Valores dos indicadores, ganho potencial e hipóteses do período usam a média mensal do período.
"""
import duckdb

import iqs
from iqs import faixa_sql


def codigo(ano: int) -> int:
    return 90000 + ano


def criar(con: duckdb.DuckDBPyConnection, ano: int) -> None:
    cod, sems = codigo(ano), (ano * 10 + 1, ano * 10 + 2)
    for t in ("smp_ind_sem", "smp_iqs_mun_final", "energia_mun_sem", "focos_mun_sem"):
        con.execute(f"DELETE FROM {t} WHERE semestre = {cod}")
    con.execute(f"""
    INSERT INTO smp_ind_sem BY NAME
    SELECT {cod} AS semestre, ind, ibge, uf, prestadora,
           ANY_VALUE(municipio) AS municipio,
           AVG(valor) AS agr,
           COUNT(valor) AS meses_validos,
           COUNT(*) FILTER (WHERE status = 'NA') AS meses_na,
           ARG_MAX(vr_inf, mes) AS vr_inf,
           ARG_MAX(vr_sup, mes) AS vr_sup,
           CASE WHEN COUNT(valor) = 0 OR COUNT(*) FILTER (WHERE status = 'NA') > COUNT(valor) THEN NULL
                ELSE GREATEST(0, LEAST(10, 10 * (AVG(valor) - ARG_MAX(vr_inf, mes)) / (ARG_MAX(vr_sup, mes) - ARG_MAX(vr_inf, mes))))
           END AS norm
    FROM smp_mensal WHERE ano = {ano}
    GROUP BY ALL
    """)
    con.execute(f"""
    INSERT INTO smp_iqs_mun_final BY NAME
    SELECT {cod} AS semestre, ibge, uf, prestadora,
           ANY_VALUE(municipio) AS municipio,
           MIN(n_ind) AS n_ind, MAX(n_na) AS n_na,
           AVG(iqs_base) AS iqs_base,
           ARG_MAX(pct_4g, semestre) AS pct_4g,
           ARG_MAX(bonus, semestre) AS bonus,
           AVG(iqs) AS iqs,
           AVG(acessos_medio) AS acessos_medio,
           {faixa_sql('AVG(iqs)')} AS faixa
    FROM smp_iqs_mun_final WHERE semestre IN {sems}
    GROUP BY ALL
    """)
    con.execute(f"""
    INSERT INTO energia_mun_sem BY NAME
    SELECT ibge, {cod} AS semestre, SUM(dec_h) AS dec_h, SUM(fec_n) AS fec_n, COUNT(*) AS meses
    FROM energia_mun_mes WHERE ano = {ano} GROUP BY ALL
    """)
    con.execute(f"""
    INSERT INTO focos_mun_sem BY NAME
    SELECT ibge, {cod} AS semestre, SUM(focos) AS focos
    FROM focos_mun_sem WHERE semestre IN {sems} GROUP BY ALL
    """)


def criar_todos(con: duckdb.DuckDBPyConnection, anos) -> None:
    for ano in anos:
        criar(con, ano)
    iqs.rank(con)
