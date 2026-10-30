"""Roda o pipeline completo: carga, IQS, selo, períodos, análises e exportação para a página.

Uso:  python pipeline/run_all.py              (a partir da raiz do projeto)
      python pipeline/run_all.py --sem-carga  (reaproveita as tabelas já carregadas)
Pré-requisito: python pipeline/baixar.py     (baixa as bases públicas em raw/)
"""
import os
import sys
import time

import duckdb

sys.path.insert(0, os.path.dirname(__file__))
import analise  # noqa: E402
import carga  # noqa: E402
import exportar  # noqa: E402
import exportar_acesso  # noqa: E402
import iqs  # noqa: E402
import montar_site  # noqa: E402
import periodos  # noqa: E402
import selo  # noqa: E402


def etapa(nome, fn, *args):
    t = time.time()
    fn(*args)
    print(f"{nome}: {time.time() - t:.1f}s")


def main():
    os.makedirs("data", exist_ok=True)
    con = duckdb.connect("data/rqual.duckdb")
    if "--sem-carga" not in sys.argv:
        etapa("Carga das bases", carga.main, con)
    etapa("IQS municipal", iqs.build, con)
    etapa("Selo e IR", selo.build, con)
    etapa("Bases externas", analise.externos, con)

    ano, mes = con.execute("SELECT MAX(ano), MAX(ano * 100 + mes) % 100 FROM smp_mensal").fetchone()
    sem_parcial = ano * 10 + (2 if mes > 6 else 1)
    sem_fechado = sem_parcial - 1 if sem_parcial % 10 == 2 else (ano - 1) * 10 + 2
    etapa("Períodos anuais", periodos.criar_todos, con, [ano - 1, ano])

    lista = [
        {"id": "atual", "cod": periodos.codigo(ano), "nome": f"{ano}",
         "detalhe": f"de janeiro a {['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'][mes]} de {ano}, último mês publicado pela Anatel",
         "ir_sems": [ano * 10 + 1, ano * 10 + 2], "anterior": periodos.codigo(ano - 1)},
        {"id": "semestre", "cod": sem_fechado, "nome": f"{sem_fechado % 10}º semestre {sem_fechado // 10}",
         "detalhe": "último semestre completo", "ir_sems": [sem_fechado], "anterior": sem_fechado - 1 if sem_fechado % 10 == 2 else (sem_fechado // 10 - 1) * 10 + 2},
        {"id": "anterior", "cod": periodos.codigo(ano - 1), "nome": f"{ano - 1}",
         "detalhe": "base do próximo selo anual da Anatel", "ir_sems": [(ano - 1) * 10 + 1, (ano - 1) * 10 + 2], "anterior": None},
    ]
    for per in lista:
        etapa(f"Ganho potencial {per['nome']}", analise.ganhos, con, per["cod"])
        etapa(f"Hipóteses {per['nome']}", analise.hipoteses, con, per["cod"], per["anterior"] or 0)
    etapa("Exportação", exportar.exportar, con, "site/data", lista, sem_parcial)
    con.close()
    etapa("Exportação Radar de Acesso", exportar_acesso.main)
    etapa("Páginas", montar_site.main)
    con = duckdb.connect("data/rqual.duckdb", read_only=True)
    for p, iqs_br, ir, f_iqs, f_ir, f_iqp, soma, sel in selo.consolidado(con, [20251]):
        print(f"validação 2025S1 {p}: calculado {sel} (publicado {exportar.SELO_PUBLICADO_2025[p]})")


if __name__ == "__main__":
    main()
