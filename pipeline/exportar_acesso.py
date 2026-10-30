"""Dados do Radar de Acesso: foco em IND4 (velocidade entregue) e IND8 (disponibilidade).

Roda depois de run_all.py (usa as tabelas e o arquivo municipios_atual.json já gerados).
Saída: site/data/acesso.json
"""
import json
import os
import statistics
import sys

import duckdb

sys.path.insert(0, os.path.dirname(__file__))
from exportar import r, salvar  # noqa: E402

OPS = ["TIM", "VIVO", "CLARO"]
SAIDA = "site/data"
REGIAO = {"1": "Norte", "2": "Nordeste", "3": "Sudeste", "4": "Sul", "5": "Centro-Oeste"}


def main():
    con = duckdb.connect("data/rqual.duckdb", read_only=True)
    ano, ultimo = con.execute("SELECT MAX(ano), MAX(ano * 100 + mes) FROM smp_mensal").fetchone()
    meses_ano = [m for (m,) in con.execute(f"SELECT DISTINCT ano * 100 + mes FROM smp_mensal WHERE ano = {ano} ORDER BY 1").fetchall()]

    # IND4 e IND8 mês a mês no ano corrente, com a marcação de significância estatística da própria Anatel.
    linhas = con.execute(f"""
        SELECT "Código IBGE", "Prestadora", "Indicador", CAST("Ano" AS INT) * 100 + CAST("Mês" AS INT) mm,
               TRY_CAST(REPLACE("Resultado", ',', '.') AS DOUBLE) v,
               TRY_CAST("Número de Medidas" AS INT) med,
               TRY_CAST(REPLACE("Erro Amostral", ',', '.') AS DOUBLE) erro,
               "Estatística" = 'SIM' sig
        FROM rqual
        WHERE "Serviço" = 'Telefonia Móvel' AND "Ano" = '{ano}' AND "Prestadora" IN ('TIM', 'VIVO', 'CLARO')
          AND "Indicador" IN ('IND4', 'IND8', 'INF4-DL-4G', 'INF4-DL-5G')""").fetchall()
    serie = {}
    for ibge, p, ind, mm, v, med, erro, sig in linhas:
        serie.setdefault((ibge, p), {}).setdefault(ind, {})[mm] = (v, med, erro, sig)

    dec = {}
    for ibge, mm, v in con.execute(f"SELECT ibge, ano * 100 + mes, dec_h FROM energia_mun_mes WHERE ano = {ano}").fetchall():
        dec.setdefault(ibge, {})[mm] = v

    acessos = {(i, p): a for i, p, a in con.execute(f"""
        SELECT ibge, prestadora, acessos FROM acessos_prest WHERE ano * 100 + mes = {ultimo}""").fetchall()}
    base = {m["id"]: m for m in json.load(open("site/data/municipios_atual.json", encoding="utf-8"))}
    vr = {i: (a, b) for i, a, b in con.execute(f"""
        SELECT ind, ARG_MAX(vr_inf, ano * 100 + mes), ARG_MAX(vr_sup, ano * 100 + mes) FROM smp_mensal
        WHERE ind IN ('IND4', 'IND8') GROUP BY 1""").fetchall()}

    def media(d, chaves):
        vals = [d[k][0] for k in chaves if k in d and d[k][0] is not None]
        return sum(vals) / len(vals) if vals else None

    tres_ult, tres_ant = meses_ano[-3:], meses_ano[-6:-3]
    out = []
    for ibge, m in base.items():
        reg = {"id": ibge, "nome": m["nome"], "uf": m["uf"], "reg": REGIAO[ibge[0]],
               "dec": r(sum(v for v in dec.get(ibge, {}).values() if v is not None)) if ibge in dec else None}
        d_dec = dec.get(ibge, {})
        for p in OPS:
            s = serie.get((ibge, p))
            if not s or p not in m:
                continue
            i4, i8 = s.get("IND4", {}), s.get("IND8", {})
            v4 = [i4[k] for k in meses_ano if k in i4 and i4[k][0] is not None]
            sig4 = sum(1 for x in v4 if x[3])
            v8 = {k: i8[k][0] for k in meses_ano if k in i8 and i8[k][0] is not None}
            pior8 = min(v8, key=v8.get) if v8 else None
            # Fatos do mês de pior disponibilidade: DEC da cidade naquele mês e IND8 das outras operadoras.
            dvals = [v for v in d_dec.values() if v is not None]
            conc8 = {o: r(serie[(ibge, o)]["IND8"][pior8][0], 2) for o in OPS
                     if pior8 and o != p and (ibge, o) in serie and pior8 in serie[(ibge, o)].get("IND8", {})
                     and serie[(ibge, o)]["IND8"][pior8][0] is not None}
            mo = m[p]
            reg[p] = {
                "a": int(acessos.get((ibge, p)) or mo["a"] or 0),
                "iqs": mo["iqs"], "f": mo["f"],
                "g4": (mo.get("g") or [0] * 8)[3], "g8": (mo.get("g") or [0] * 8)[7],
                "i4": r(media(i4, meses_ano), 2), "i4u": r(i4[meses_ano[-1]][0], 2) if meses_ano[-1] in i4 else None,
                "t4": r((media(i4, tres_ult) or 0) - (media(i4, tres_ant) or 0), 2) if media(i4, tres_ult) is not None and media(i4, tres_ant) is not None else None,
                "s4": round(sig4 / len(v4), 2) if v4 else None, "k4": sig4, "n4": len(v4), "m4": sum(x[1] or 0 for x in v4),
                "e4": r(sum(x[2] or 0 for x in v4) / len(v4), 1) if v4 else None,
                "i8": r(media(i8, meses_ano), 3), "i8m": r(v8[pior8], 3) if pior8 else None, "i8mm": pior8,
                "dpm": r(d_dec.get(pior8), 2) if pior8 else None, "dmed": r(statistics.median(dvals), 2) if dvals else None, "c8": conc8,
                "dl4": r(media(s.get("INF4-DL-4G", {}), meses_ano), 1), "dl5": r(media(s.get("INF4-DL-5G", {}), meses_ano), 1),
            }
        if any(p in reg for p in OPS):
            out.append(reg)

    resumo = json.load(open("site/data/resumo.json", encoding="utf-8"))
    # Séries mensais, mapas e arquivos por UF já estão em site/data (exportar.py); aqui vai só o recorte de IND4 e IND8.
    salvar(f"{SAIDA}/acesso.json", {
        "ano": ano, "meses_ano": meses_ano, "vr": vr, "gerado_em": resumo["gerado_em"], "municipios": out,
    })


if __name__ == "__main__":
    main()
