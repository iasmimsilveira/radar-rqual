"""Fronteira, ganho potencial por indicador e hipóteses de causa.

Ganho potencial: para cada município e indicador, recalcula o IQSmun como se aquele indicador
atingisse o valor de referência superior (norm = 10), refazendo a ordenação dos pesos.
Como o pior indicador leva 36,7% do peso, corrigir o pior indicador costuma render mais.

Hipótese de causa (nunca é diagnóstico):
- compartilhado: os concorrentes no mesmo município também vão mal no mesmo indicador
- específico: só a operadora vai mal no indicador
- energia: DEC do semestre no município acima do percentil 90 nacional
- queimadas: focos de calor (satélite de referência AQUA_M-T) acima do percentil 90
- piora recente: IQS caiu 5 pontos ou mais contra o semestre anterior
"""
import duckdb

from iqs import INDICADORES_SMP, PESOS_SMP

# Abaixo deste valor normalizado (0 a 10) o pior indicador é tratado como problema a explicar.
LIMIAR_PROBLEMA = 8.0

EQUIPE = {
    "IND1": "RAN: acessibilidade de voz (capacidade e otimização)",
    "IND2": "RAN/RF: cobertura e handover (queda de chamadas)",
    "IND3": "RAN: acessibilidade de dados (capacidade e otimização)",
    "IND4": "RAN e backhaul: capacidade e vazão",
    "IND5": "Transporte, core e rotas IP (latência)",
    "IND6": "Transporte, core e rotas IP (variação de latência)",
    "IND7": "Transporte e backhaul (perda de pacotes)",
    "IND8": "Energia, infraestrutura e O&M (disponibilidade)",
}


def iqs_de(norms: dict) -> float:
    vals = sorted(norms.values())
    pesos = PESOS_SMP[: len(vals)]
    return 10 * sum(v * p for v, p in zip(vals, pesos)) / sum(pesos)


def ganhos(con: duckdb.DuckDBPyConnection, semestre: int) -> None:
    rows = con.execute(f"""
        SELECT ibge, prestadora, LIST(ind ORDER BY ind), LIST(norm ORDER BY ind)
        FROM smp_ind_sem WHERE semestre = {semestre} AND norm IS NOT NULL
        GROUP BY ALL
    """).fetchall()
    out = []
    for ibge, prest, inds, norms in rows:
        n = dict(zip(inds, norms))
        if len(n) < 7:
            continue
        base = iqs_de(n)
        for ind in n:
            out.append((semestre, ibge, prest, ind, iqs_de({**n, ind: 10.0}) - base))
    con.execute("CREATE TABLE IF NOT EXISTS smp_ganho (semestre INT, ibge VARCHAR, prestadora VARCHAR, ind VARCHAR, ganho DOUBLE)")
    con.execute(f"DELETE FROM smp_ganho WHERE semestre = {semestre}")
    con.executemany("INSERT INTO smp_ganho VALUES (?, ?, ?, ?, ?)", out)


def externos(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    CREATE OR REPLACE TABLE energia_mun_sem AS
    SELECT ibge, ano * 10 + CASE WHEN mes <= 6 THEN 1 ELSE 2 END AS semestre,
           SUM(dec_h) AS dec_h, SUM(fec_n) AS fec_n, COUNT(*) AS meses
    FROM energia_mun_mes GROUP BY ALL
    """)
    con.execute("""
    CREATE OR REPLACE TABLE focos_mun_sem AS
    SELECT CAST(municipio_id AS VARCHAR) AS ibge,
           YEAR(CAST(data_hora_gmt AS TIMESTAMP)) * 10 + CASE WHEN MONTH(CAST(data_hora_gmt AS TIMESTAMP)) <= 6 THEN 1 ELSE 2 END AS semestre,
           COUNT(*) AS focos
    FROM read_csv('raw/inpe/*.csv', header=true, all_varchar=true, union_by_name=true, ignore_errors=true)
    WHERE satelite = 'AQUA_M-T'
    GROUP BY ALL
    """)


def hipoteses(con: duckdb.DuckDBPyConnection, semestre: int, anterior: int) -> None:
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE hip_tmp AS
    WITH pior AS (
        SELECT ibge, prestadora, ind AS pior_ind, norm AS pior_norm
        FROM smp_ind_sem_rank WHERE semestre = {semestre} AND rank = 1
    ),
    conc AS (
        SELECT a.ibge, a.prestadora, a.ind, MAX(b.norm) AS melhor_conc
        FROM smp_ind_sem a JOIN smp_ind_sem b
          ON b.semestre = a.semestre AND b.ibge = a.ibge AND b.ind = a.ind AND b.prestadora <> a.prestadora
         AND b.prestadora IN ('TIM', 'VIVO', 'CLARO')
        WHERE a.semestre = {semestre}
        GROUP BY ALL
    ),
    lim AS (
        SELECT QUANTILE_CONT(dec_h, 0.9) AS p90_dec FROM energia_mun_sem WHERE semestre = {semestre}
    ),
    limf AS (
        SELECT QUANTILE_CONT(focos, 0.9) AS p90_focos FROM focos_mun_sem WHERE semestre = {semestre}
    )
    SELECT {semestre} AS semestre, f.ibge, f.prestadora, f.iqs, f.faixa, prev.iqs AS iqs_anterior,
           p.pior_ind, p.pior_norm, c.melhor_conc,
           e.dec_h, fo.focos,
           (c.melhor_conc IS NOT NULL AND c.melhor_conc - p.pior_norm < 1) AS compartilhado,
           (c.melhor_conc IS NOT NULL AND c.melhor_conc - p.pior_norm >= 2) AS especifico,
           (e.dec_h >= (SELECT p90_dec FROM lim)) AS energia,
           (COALESCE(fo.focos, 0) >= (SELECT p90_focos FROM limf) AND COALESCE(fo.focos, 0) > 0) AS queimadas,
           (f.iqs - prev.iqs <= -5) AS piora
    FROM smp_iqs_mun_final f
    LEFT JOIN pior p USING (ibge, prestadora)
    LEFT JOIN conc c ON c.ibge = f.ibge AND c.prestadora = f.prestadora AND c.ind = p.pior_ind
    LEFT JOIN energia_mun_sem e ON e.ibge = f.ibge AND e.semestre = {semestre}
    LEFT JOIN focos_mun_sem fo ON fo.ibge = f.ibge AND fo.semestre = {semestre}
    LEFT JOIN smp_iqs_mun_final prev ON prev.ibge = f.ibge AND prev.prestadora = f.prestadora AND prev.semestre = {anterior}
    WHERE f.semestre = {semestre} AND f.prestadora IN ('TIM', 'VIVO', 'CLARO')
    """)
    con.execute("CREATE TABLE IF NOT EXISTS smp_hipotese AS SELECT * FROM hip_tmp WHERE false")
    con.execute(f"DELETE FROM smp_hipotese WHERE semestre = {semestre}")
    con.execute("INSERT INTO smp_hipotese SELECT * FROM hip_tmp")


def classificar(h: dict) -> tuple[str, str, list[str]]:
    """Devolve (hipótese, confiança, evidências) para um município e uma operadora."""
    ev = []
    h = {**h, **{k: bool(h[k]) for k in ("compartilhado", "especifico", "energia", "queimadas", "piora")}}
    ind = h["pior_ind"]
    if ind is None:
        return ("Sem dados suficientes", "baixa", ev)
    if h["pior_norm"] >= LIMIAR_PROBLEMA:
        return ("Sem indicador crítico", "alta", ev)
    if h["especifico"]:
        ev.append(f"Concorrentes vão bem no {ind} no mesmo município")
    if h["compartilhado"]:
        ev.append(f"Concorrentes também vão mal no {ind}")
    if h["energia"]:
        ev.append(f"Queda de energia alta no semestre (DEC {h['dec_h']:.1f} h)")
    if h["queimadas"]:
        ev.append(f"Muitos focos de queimada ({h['focos']})")
    if h["piora"]:
        ev.append("IQS caiu 5 pontos ou mais contra o semestre anterior")

    externo = h["energia"] or h["queimadas"]
    if ind == "IND8" and h["energia"]:
        hip, n = "Energia (externa)", 1 + h["compartilhado"] + h["piora"]
    elif h["compartilhado"] and externo:
        hip, n = "Condição externa compartilhada", 1 + h["piora"] + (h["energia"] and h["queimadas"])
    elif h["compartilhado"]:
        hip, n = "Condição local compartilhada", 1
    elif h["especifico"]:
        hip, n = "Rede da operadora", 1 + h["piora"] + (not externo)
    else:
        hip, n = "Inconclusivo", 0
    conf = "alta" if n >= 3 else "média" if n == 2 else "baixa"
    return (hip, conf, ev)
