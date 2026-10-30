"""Carrega as bases brutas de raw/ para tabelas DuckDB (data/rqual.duckdb).

Os CSVs grandes são extraídos um por vez, agregados e apagados, para caber em pouco disco.
"""
import os
import re
import zipfile

import duckdb

TMP = "raw/tmp"


def _extrair(zip_path: str, nome: str) -> str:
    os.makedirs(TMP, exist_ok=True)
    return zipfile.ZipFile(zip_path).extract(nome, TMP)


def rqual(con: duckdb.DuckDBPyConnection) -> None:
    p = _extrair("raw/anatel/indicadores_rqual.zip", "Tabela_CSV_Indicadores_RQUAL.csv")
    con.execute(f"""CREATE OR REPLACE TABLE rqual AS
        SELECT * FROM read_csv('{p}', delim=';', header=true, all_varchar=true, quote='"')""")
    os.remove(p)


def reclamacoes(con: duckdb.DuckDBPyConnection) -> None:
    p = _extrair("raw/anatel/consumidor_reclamacoes.zip", "reclamacoes_contexto.csv")
    con.execute(f"""CREATE OR REPLACE TABLE reclamacoes AS
        SELECT CAST("Ano" AS INT) ano, CAST("Mês" AS INT) mes, "UF" uf, CAST("CO_MUNICIPIO" AS VARCHAR) ibge,
               "Serviço" servico, UPPER("Marca") marca, "Assunto" assunto, "Problema" problema, "Condição" condicao,
               SUM(CAST("SOLICITAÇÕES" AS BIGINT)) n
        FROM read_csv('{p}', delim=';', header=true, all_varchar=true)
        WHERE "TipoAtendimento" = 'Reclamação' AND "Serviço" LIKE 'SMP%' AND CAST("Ano" AS INT) >= 2022
        GROUP BY ALL""")
    os.remove(p)


def acessos(con: duckdb.DuckDBPyConnection) -> None:
    z = zipfile.ZipFile("raw/anatel/acessos_telefonia_movel.zip")
    con.execute("""CREATE OR REPLACE TABLE acessos_smp
        (ibge VARCHAR, grupo VARCHAR, empresa VARCHAR, tipo_produto VARCHAR, ano INT, mes INT, acessos BIGINT)""")
    # Só os arquivos semestrais "AAAA_1S/2S_Colunas"; a base também traz arquivos históricos com outro padrão de nome.
    nomes = sorted(n for n in z.namelist()
                   if (m := re.search(r"_(\d{4})_[12]S_Colunas\.csv$", n)) and int(m.group(1)) >= 2022)
    for nome in nomes:
        p = z.extract(nome, TMP)
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_csv('{p}', delim=';', header=true, all_varchar=true)").fetchall()]
        meses = ", ".join(f'"{c}"' for c in cols if len(c) == 7 and c[4] == "-")
        con.execute(f"""INSERT INTO acessos_smp
            SELECT "Código IBGE Município", "Grupo Econômico", "Empresa", "Tipo de Produto",
                   CAST(substr(mes, 1, 4) AS INT), CAST(substr(mes, 6, 2) AS INT), SUM(TRY_CAST(v AS BIGINT))
            FROM (UNPIVOT (SELECT * FROM read_csv('{p}', delim=';', header=true, all_varchar=true))
                  ON {meses} INTO NAME mes VALUE v)
            GROUP BY ALL""")
        os.remove(p)


def meu_municipio() -> None:
    zipfile.ZipFile("raw/anatel/meu_municipio.zip").extractall("raw/anatel/meu_municipio")


def energia(con: duckdb.DuckDBPyConnection) -> None:
    with open("raw/aneel/indqual_municipio.csv", encoding="latin-1") as f, \
            open("raw/aneel/conj_mun.csv", "w", encoding="utf-8") as g:
        g.write(f.read())
    con.execute("""CREATE OR REPLACE TABLE aneel_conj_mun AS
        SELECT DISTINCT CAST(IdeConjUnidConsumidoras AS BIGINT) conj, CodMunicipio ibge, NomMunicipio municipio, SigUF uf
        FROM read_csv('raw/aneel/conj_mun.csv', delim=';', header=true, all_varchar=true)""")
    # DEC e FEC do município = média dos conjuntos que o atendem, ponderada pelo número de consumidores.
    con.execute("""CREATE OR REPLACE TABLE energia_mun_mes AS
        WITH c AS (
            SELECT IdeConjUndConsumidoras conj, AnoIndice ano, NumPeriodoIndice mes,
                   MAX(VlrIndiceEnviado) FILTER (WHERE TRIM(SigIndicador) = 'DEC') dec_h,
                   MAX(VlrIndiceEnviado) FILTER (WHERE TRIM(SigIndicador) = 'FEC') fec_n,
                   MAX(VlrIndiceEnviado) FILTER (WHERE TRIM(SigIndicador) = 'NumCon') ncon
            FROM 'raw/aneel/continuidade_2020_2029.parquet'
            WHERE AnoIndice >= 2022 AND TRIM(SigIndicador) IN ('DEC', 'FEC', 'NumCon')
            GROUP BY ALL)
        SELECT m.ibge, c.ano, c.mes,
               SUM(c.dec_h * c.ncon) / NULLIF(SUM(c.ncon), 0) dec_h,
               SUM(c.fec_n * c.ncon) / NULLIF(SUM(c.ncon), 0) fec_n,
               COUNT(*) n_conj
        FROM c JOIN aneel_conj_mun m ON m.conj = c.conj
        WHERE c.dec_h IS NOT NULL
        GROUP BY ALL""")


def main(con: duckdb.DuckDBPyConnection) -> None:
    for nome, fn in (("rqual", rqual), ("reclamacoes", reclamacoes), ("acessos", acessos), ("energia", energia)):
        print("carregando", nome)
        fn(con)
    meu_municipio()
