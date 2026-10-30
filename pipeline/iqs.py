"""Cálculo do IQS municipal (SMP) conforme o Documento de Valores de Referência (DVR).

Fonte da regra: Resolução Interna Anatel nº 397/2024 (DVR), arts. 11 a 16.
- norm(IND) = 10 * (agr - VRinf) / (VRsup - VRinf), limitado a [0, 10]
  (a mesma expressão vale para o IND2, em que menor é melhor, porque VRinf > VRsup)
- agr(IND)  = média dos resultados mensais do semestre
- norm = NA quando pelo menos 2 meses do semestre forem NA
- pesos por posição: o pior indicador normalizado recebe 36,7%, o 2º pior 18,4%...
- IQSmun = 10 * soma(norm_i * P_i) + bônus  (escala 0 a 100)
- IQSmun = NA quando pelo menos 2 componentes forem NA
- faixas: >=90 F1 | >=75 F2 | >=60 F3 | >40 F4 | resto F5
Os valores de referência vêm da própria base da Anatel, linha a linha, porque mudam por ano.
"""
import duckdb

PESOS_SMP = [0.367, 0.184, 0.123, 0.092, 0.074, 0.061, 0.053, 0.046]
INDICADORES_SMP = ["IND1", "IND2", "IND3", "IND4", "IND5", "IND6", "IND7", "IND8"]


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    CREATE OR REPLACE TABLE smp_mensal AS
    SELECT
        CAST("Ano" AS INT) AS ano,
        CAST("Mês" AS INT) AS mes,
        CAST("Ano" AS INT) * 10 + CASE WHEN CAST("Mês" AS INT) <= 6 THEN 1 ELSE 2 END AS semestre,
        "Indicador" AS ind,
        "Descrição" AS descricao,
        "Código IBGE" AS ibge,
        "Município" AS municipio,
        "UF" AS uf,
        "Prestadora" AS prestadora,
        TRY_CAST(REPLACE("Resultado", ',', '.') AS DOUBLE) AS valor,
        CASE WHEN TRY_CAST(REPLACE("Resultado", ',', '.') AS DOUBLE) IS NULL THEN "Resultado" END AS status,
        TRY_CAST(REPLACE(REPLACE("Valor de Referência Inferior", '%', ''), ',', '.') AS DOUBLE) AS vr_inf,
        TRY_CAST(REPLACE(REPLACE("Valor de Referência Superior", '%', ''), ',', '.') AS DOUBLE) AS vr_sup
    FROM rqual
    WHERE "Serviço" = 'Telefonia Móvel' AND "Tipo" = 'Indicador IQS'
    """)

    con.execute("""
    CREATE OR REPLACE TABLE smp_ind_sem AS
    SELECT
        semestre, ind, ibge, uf, prestadora,
        ANY_VALUE(municipio) AS municipio,
        AVG(valor) AS agr,
        COUNT(valor) AS meses_validos,
        COUNT(*) FILTER (WHERE status = 'NA') AS meses_na,
        ANY_VALUE(vr_inf) AS vr_inf,
        ANY_VALUE(vr_sup) AS vr_sup,
        CASE
            WHEN COUNT(*) FILTER (WHERE status = 'NA') >= 2 OR COUNT(valor) = 0 THEN NULL
            ELSE GREATEST(0, LEAST(10, 10 * (AVG(valor) - ANY_VALUE(vr_inf)) / (ANY_VALUE(vr_sup) - ANY_VALUE(vr_inf))))
        END AS norm
    FROM smp_mensal
    GROUP BY ALL
    """)

    rank(con)

    # Quando um componente é NA, os pesos dos componentes restantes são renormalizados para somar 100%.
    con.execute("""
    CREATE OR REPLACE TABLE smp_iqs_mun AS
    SELECT
        semestre, ibge, uf, prestadora,
        ANY_VALUE(municipio) AS municipio,
        COUNT(norm) AS n_ind,
        COUNT(*) FILTER (WHERE norm IS NULL) AS n_na,
        CASE WHEN COUNT(*) FILTER (WHERE norm IS NULL) >= 2 THEN NULL
             ELSE 10 * SUM(norm * peso) / SUM(peso) END AS iqs_base
    FROM smp_ind_sem_rank
    GROUP BY ALL
    """)


def rank(con: duckdb.DuckDBPyConnection) -> None:
    """Ordena os indicadores normalizados de cada município e aplica os pesos por posição."""
    pesos = ", ".join(f"({i + 1}, {p})" for i, p in enumerate(PESOS_SMP))
    con.execute(f"""
    CREATE OR REPLACE TABLE smp_ind_sem_rank AS
    WITH pesos(rank, peso) AS (VALUES {pesos})
    SELECT s.*, r.rank, p.peso
    FROM smp_ind_sem s
    LEFT JOIN (
        SELECT semestre, ind, ibge, prestadora,
               ROW_NUMBER() OVER (PARTITION BY semestre, ibge, prestadora ORDER BY norm, ind) AS rank
        FROM smp_ind_sem WHERE norm IS NOT NULL
    ) r USING (semestre, ind, ibge, prestadora)
    LEFT JOIN pesos p ON p.rank = r.rank
    """)


def faixa_sql(col: str) -> str:
    return f"""CASE WHEN {col} IS NULL THEN NULL
        WHEN {col} >= 90 THEN 1 WHEN {col} >= 75 THEN 2 WHEN {col} >= 60 THEN 3
        WHEN {col} > 40 THEN 4 ELSE 5 END"""
