"""Gera os arquivos JSON lidos pela página (site/data)."""
import datetime
import glob
import json
import os
import shutil

import duckdb

import analise
import selo
from iqs import INDICADORES_SMP

PRESTADORAS = ["TIM", "VIVO", "CLARO"]
SELO_PUBLICADO_2025 = {"TIM": "C", "VIVO": "B", "CLARO": "B"}
MESES_SERIE = 24
CAPITAIS = ["1100205", "1200401", "1302603", "1400100", "1501402", "1600303", "1721000", "2111300", "2211001", "2304400",
            "2408102", "2507507", "2611606", "2704302", "2800308", "2927408", "3106200", "3205309", "3304557", "3550308",
            "4106902", "4205407", "4314902", "5002704", "5103403", "5208707", "5300108"]


def r(x, n=1):
    return None if x is None else round(float(x), n)


def salvar(caminho, obj):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def mes_txt(v):
    return None if v is None else f"{v // 100}-{v % 100:02d}"


def datas_fontes(con):
    um = lambda sql: con.execute(sql).fetchone()[0]
    focos = sorted(glob.glob("raw/inpe/focos_mensal_br_*.csv"))
    return {
        "rqual": mes_txt(um("SELECT MAX(ano * 100 + mes) FROM smp_mensal")),
        "acessos": mes_txt(um("SELECT MAX(ano * 100 + mes) FROM acessos_smp")),
        "reclamacoes": mes_txt(um("SELECT MAX(ano * 100 + mes) FROM reclamacoes")),
        "energia": mes_txt(um("SELECT MAX(ano * 100 + mes) FROM energia_mun_mes")),
        "queimadas": mes_txt(int(focos[-1][-10:-4])) if focos else None,
        "cobertura": mes_txt(um("SELECT MAX(ref) FROM cobertura_4g")),
    }


def consolidar(con, periodo):
    iqs_sems, ir_sems = [periodo["cod"]], periodo["ir_sems"]
    out = {"BR": {}, "UF": {}}
    for p, iqs, ir, f_iqs, f_ir, f_iqp, soma, sel in selo.consolidado(con, iqs_sems, ir_semestres=ir_sems):
        out["BR"][p] = {"iqs": r(iqs, 2), "ir": r(ir, 3), "f_iqs": f_iqs, "f_ir": f_ir, "f_iqp": f_iqp, "soma": soma, "selo": sel}
    for p, u, iqs, ir, f_iqs, f_ir, f_iqp, soma, sel in selo.consolidado(con, iqs_sems, nivel="uf", ir_semestres=ir_sems):
        out["UF"].setdefault(u, {})[p] = {"iqs": r(iqs, 2), "ir": r(ir, 3), "f_iqs": f_iqs, "f_ir": f_ir, "f_iqp": f_iqp, "soma": soma, "selo": sel}
    return out


def municipios(con, cod):
    base = con.execute(f"""
        SELECT f.ibge, ANY_VALUE(f.municipio), ANY_VALUE(f.uf), ANY_VALUE(e.dec_h), ANY_VALUE(fo.focos)
        FROM smp_iqs_mun_final f
        LEFT JOIN energia_mun_sem e ON e.ibge = f.ibge AND e.semestre = {cod}
        LEFT JOIN focos_mun_sem fo ON fo.ibge = f.ibge AND fo.semestre = {cod}
        WHERE f.semestre = {cod} GROUP BY 1 ORDER BY 1""").fetchall()
    ops = {}
    for row in con.execute(f"""
        SELECT f.ibge, f.prestadora, f.iqs, f.faixa, f.bonus, f.acessos_medio, h.iqs_anterior,
               LIST(s.norm ORDER BY s.ind), LIST(s.agr ORDER BY s.ind), LIST(s.ind ORDER BY s.ind),
               h.pior_ind, h.pior_norm, h.melhor_conc, h.dec_h, h.focos,
               h.compartilhado, h.especifico, h.energia, h.queimadas, h.piora
        FROM smp_iqs_mun_final f
        JOIN smp_ind_sem s ON s.semestre = f.semestre AND s.ibge = f.ibge AND s.prestadora = f.prestadora
        LEFT JOIN smp_hipotese h ON h.semestre = f.semestre AND h.ibge = f.ibge AND h.prestadora = f.prestadora
        WHERE f.semestre = {cod} AND f.prestadora IN ('TIM', 'VIVO', 'CLARO')
        GROUP BY ALL""").fetchall():
        ibge, p = row[0], row[1]
        norms, agrs = dict(zip(row[9], row[7])), dict(zip(row[9], row[8]))
        h = dict(zip(["pior_ind", "pior_norm", "melhor_conc", "dec_h", "focos", "compartilhado", "especifico",
                      "energia", "queimadas", "piora"], row[10:]))
        ops[(ibge, p)] = {
            "iqs": r(row[2]), "f": row[3], "b": row[4], "a": int(row[5] or 0), "ant": r(row[6]),
            "n": [r(norms.get(i), 2) for i in INDICADORES_SMP],
            "v": [r(agrs.get(i), 2) for i in INDICADORES_SMP],
        }
    for ibge, p, ind, g in con.execute(f"SELECT ibge, prestadora, ind, ganho FROM smp_ganho WHERE semestre = {cod}").fetchall():
        if (ibge, p) in ops:
            # A simulação respeita o teto de 100 da escala do IQS.
            teto = 100 - (ops[(ibge, p)]["iqs"] or 0)
            ops[(ibge, p)].setdefault("g", [0.0] * 8)[INDICADORES_SMP.index(ind)] = r(max(0.0, min(g, teto)), 2)
    out = []
    for ibge, nome, u, dec_h, focos in base:
        m = {"id": ibge, "nome": nome, "uf": u, "dec": r(dec_h), "focos": focos}
        for p in PRESTADORAS:
            if (ibge, p) in ops:
                m[p] = ops[(ibge, p)]
        out.append(m)
    return out


def exportar(con: duckdb.DuckDBPyConnection, saida: str, periodos: list, sem_parcial: int) -> None:
    semestres = [s for (s,) in con.execute("SELECT DISTINCT semestre FROM smp_iqs_mun ORDER BY 1").fetchall()]
    fontes = datas_fontes(con)

    serie = []
    for s in semestres:
        for p, iqs, ir, f_iqs, f_ir, f_iqp, soma, sel in selo.consolidado(con, [s]):
            serie.append({"semestre": s, "prestadora": p, "iqs": r(iqs, 2), "ir": r(ir, 3), "f_iqs": f_iqs,
                          "f_ir": f_ir, "f_iqp": f_iqp, "soma": soma, "selo": sel})

    serie_uf = {}
    for s in semestres:
        for p, u, iqs, ir, f_iqs, f_ir, f_iqp, soma, sel in selo.consolidado(con, [s], nivel="uf"):
            serie_uf.setdefault(u, []).append({"semestre": s, "prestadora": p, "iqs": r(iqs, 2), "ir": r(ir, 3)})

    validacao = [{"prestadora": p, "publicado": SELO_PUBLICADO_2025[p], "calculado": sel, "soma": soma}
                 for p, iqs, ir, f_iqs, f_ir, f_iqp, soma, sel in selo.consolidado(con, [20251])]

    vr = {i: {"inf": a, "sup": b, "descricao": d} for i, d, a, b in con.execute("""
        SELECT ind, ARG_MAX(descricao, ano * 100 + mes), ARG_MAX(vr_inf, ano * 100 + mes), ARG_MAX(vr_sup, ano * 100 + mes)
        FROM smp_mensal GROUP BY 1 ORDER BY 1""").fetchall()}

    # Séries mensais dos indicadores, ponderadas pelos acessos do mês, para o Brasil e cada UF.
    ultimo = con.execute("SELECT MAX(ano * 100 + mes) FROM smp_mensal").fetchone()[0]
    meses = [m for (m,) in con.execute(f"""
        SELECT DISTINCT ano * 100 + mes m FROM smp_mensal WHERE ano * 100 + mes <= {ultimo} ORDER BY 1 DESC LIMIT {MESES_SERIE}""").fetchall()][::-1]
    lista_meses = ", ".join(str(m) for m in meses)
    mensal = {}
    for escopo, uf, p, ind, mm, v in con.execute(f"""
        WITH x AS (
            SELECT s.uf, s.prestadora, s.ind, s.ano * 100 + s.mes mm, s.valor, a.acessos
            FROM smp_mensal s JOIN acessos_prest a ON a.ibge = s.ibge AND a.prestadora = s.prestadora AND a.ano = s.ano AND a.mes = s.mes
            WHERE s.valor IS NOT NULL AND s.ano * 100 + s.mes IN ({lista_meses}) AND s.prestadora IN ('TIM', 'VIVO', 'CLARO') AND a.acessos > 0)
        SELECT 'UF', uf, prestadora, ind, mm, SUM(valor * acessos) / SUM(acessos) FROM x GROUP BY ALL
        UNION ALL
        SELECT 'BR', 'BR', prestadora, ind, mm, SUM(valor * acessos) / SUM(acessos) FROM x GROUP BY ALL""").fetchall():
        arr = mensal.setdefault(uf, {}).setdefault(p, {}).setdefault(ind, [None] * len(meses))
        arr[meses.index(mm)] = r(v, 2)

    per_out = []
    for per in periodos:
        per_out.append({k: per[k] for k in ("id", "cod", "nome", "detalhe", "ir_sems")})
        per_out[-1]["consolidado"] = consolidar(con, per)
        salvar(f"{saida}/municipios_{per['id']}.json", municipios(con, per["cod"]))

    salvar(f"{saida}/resumo.json", {
        "gerado_em": datetime.date.today().isoformat(), "fontes": fontes,
        "semestre_parcial": sem_parcial, "periodos": per_out,
        "iqp": selo.IQP_BR, "isg": selo.ISG_2025, "capitais": CAPITAIS, "validacao": validacao, "serie": serie, "serie_uf": serie_uf, "indicadores": vr,
        "meses": meses, "mensal": mensal,
    })

    # Por UF, carregado sob demanda: série semestral do IQS e últimos meses de cada indicador por município.
    por_uf = {}
    for u, ibge, p, sems, vals in con.execute("""
        SELECT uf, ibge, prestadora, LIST(semestre ORDER BY semestre), LIST(iqs ORDER BY semestre)
        FROM smp_iqs_mun_final WHERE prestadora IN ('TIM', 'VIVO', 'CLARO') AND semestre < 90000 GROUP BY ALL""").fetchall():
        por_uf.setdefault(u, {}).setdefault(ibge, {})[p] = {"s": sems, "iqs": [r(v) for v in vals], "m": [[None] * len(meses) for _ in INDICADORES_SMP]}
    for u, ibge, p, ind, mm, v in con.execute(f"""
        SELECT uf, ibge, prestadora, ind, ano * 100 + mes, valor FROM smp_mensal
        WHERE valor IS NOT NULL AND ano * 100 + mes IN ({lista_meses}) AND prestadora IN ('TIM', 'VIVO', 'CLARO')""").fetchall():
        d = por_uf.get(u, {}).get(ibge, {}).get(p)
        if d:
            d["m"][INDICADORES_SMP.index(ind)][meses.index(mm)] = r(v, 2)
    for ibge, mm, v in con.execute(f"SELECT ibge, ano * 100 + mes, dec_h FROM energia_mun_mes WHERE ano * 100 + mes IN ({lista_meses})").fetchall():
        for u in por_uf:
            if ibge in por_uf[u]:
                por_uf[u][ibge].setdefault("dec", [None] * len(meses))[meses.index(mm)] = r(v, 2)
                break
    for u, dados in por_uf.items():
        salvar(f"{saida}/uf/{u}.json", dados)

    shutil.copy("raw/ibge/br_mun.topojson", f"{saida}/br_mun.json")
    shutil.copy("raw/ibge/br_uf.topojson", f"{saida}/br_uf.json")
    shutil.copy("raw/geo/countries-50m.json", f"{saida}/mundo.json")
